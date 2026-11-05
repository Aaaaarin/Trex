"""The dispatch agent. It pauses between events, so a case can wait days and survive a crash.

Each turn is saved before its tools run. On restart, unfinished tool calls run again, and the
tools themselves make sure nothing is sent or written twice.
"""
import json
import time

import anthropic

from . import store
from .config import DEMO, MODEL
from .tools import TOOLS, Tools

SYSTEM = """You run a distributor's nightly dispatch desk in India, for chilled and packaged dairy goods. Your job is finished work: trucks decided, stock allocated, people told, orders written, deliveries proven. You are not an adviser and not a chat assistant.

Tonight is the evening before the plan date. Records are poor and late, and retailers keep few records. Treat that as normal.

How to work:
- Understand the request first. Use find_records for any name. If a retailer or product stays ambiguous and it changes the result, ask one specific question.
- Check the facts: truck_board, billing_gap and trace_upstream as needed. Say "not established" when records cannot show a cause. Never invent one. Invoices cannot prove a stock-out.
- Decide with price_options. Follow the cheapest sensible option, and state your reason. A lost sale is gone: later bills never make up for it.
- If a truck is under target and selling to fill is best, use plan_offers, then send_offer to each retailer listed, in short natural Hinglish. Ask nobody else. Use only the quantities tools gave you.
- You never set quantities yourself. The solver allocates stock and gives partials. Hotels matter most when stock is short.
- Ask a person with request_decision only when the answer changes the result: going under target, discounts above the allowed limit, taking stock promised to someone else, credit, or substitution. Then end your turn.
- When replies arrive, accept each yes with accept_offer, citing that reply's evidence id. Ignore a duplicate. Treat a confusing reply as not accepted and say so.
- When the truck is ready: finalize_truck, write_orders, issue_load_sheet, then end your turn and wait for drivers.
- A drop closes only with record_drop citing the driver's own message. "Dispatched" never closes anything. A failed drop goes to log_miss.
- When every stop has closed, or nothing further can be done without a person, use report_to_lead to summarise what happened, what is proven and what is still open. Then end your turn.

Rules for words:
- People see only what you send through tools. Use report_to_lead for the dispatch lead.
- Every number in any message needs a claim citing evidence ids that tools returned. If the source check blocks you, fix the claim or remove the number. Never work around it.
- Tell the lead when a figure rests on a stand-in. Capacity, costs, margin, stock, shelf life, retailer types and all upstream data are stand-ins tonight. Orders are next-day bills used as a proxy.
- The same request, reworded, must lead to the same decision. Decide from the tools, not from the wording.
- Keep messages short and plain. Do not use em dashes."""

MAX_STEPS = 60
EFFORT = "medium"


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _blocks(content) -> list[dict]:
    return [b.model_dump(mode="json", exclude_none=True) for b in content]


def _pending_tool_uses(turns: list[dict]) -> list[dict]:
    if not turns or turns[-1]["role"] != "assistant":
        return []
    return [b for b in turns[-1]["content"] if b.get("type") == "tool_use"]


class Agent:
    def __init__(self, run: str, client=None, evening: str | None = None, plan_date: str | None = None):
        self.run = run
        self.client = client or _client()
        self.tools = Tools(run, evening or DEMO["plan_evening"], plan_date or DEMO["plan_date"], DEMO["distributor"])

    def _request(self, messages: list[dict]):
        return self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            cache_control={"type": "ephemeral"},
        )

    def _inbox_blocks(self) -> list[dict]:
        rows = store.unhandled_inbox(self.run)
        blocks = []
        for row in rows:
            data = json.loads(row["data"] or "{}")
            eid = store.add_evidence(self.run, "message", f"inbox:{row['seq']}", f"Message from {row['sender']}", {"sender": row["sender"], "role": row["role"], "text": row["text"], "simulated": bool(row["simulated"]), "photo": data.get("photo", False), "offer": data.get("offer")})
            label = "simulated reply" if row["simulated"] else "live reply"
            blocks.append({"type": "text", "text": f"New message ({label}) from {row['sender']} ({row['role']}), evidence_id {eid}: {row['text']}"})
        store.mark_handled([r["seq"] for r in rows])
        for d in store.rows("select * from decisions where run=? and answer is not null and id not in (select value from kv where run=? and key like 'seen_decision:%')", (self.run, self.run)):
            eid = store.add_evidence(self.run, "decision", d["id"], "Person's decision", {"question": d["question"], "answer": d["answer"], "by": d["answered_by"]})
            store.set_kv(self.run, f"seen_decision:{d['id']}", d["id"])
            store.execute("update kv set value=? where run=? and key=?", (d["id"], self.run, f"seen_decision:{d['id']}"))
            blocks.append({"type": "text", "text": f"Decision {d['id']} answered by {d['answered_by']}: {d['answer']}. evidence_id {eid}"})
        return blocks

    def start(self, request: str) -> None:
        store.create_run(self.run, request)
        if store.get_turns(self.run):
            return
        eid = store.add_evidence(self.run, "message", "request", "The dispatch lead's request", {"sender": "lead", "role": "lead", "text": request})
        text = f"Dispatch lead's request, evidence_id {eid}:\n{request}\n\nPlan date {self.tools.plan_date}. Records run to {self.tools.evening}."
        store.add_turn(self.run, "user", [{"type": "text", "text": text}])

    def _run_pending_tools(self, turns: list[dict]) -> list[dict]:
        results = []
        for use in _pending_tool_uses(turns):
            output, is_error = self.tools.call(use["name"], use["input"])
            results.append({"type": "tool_result", "tool_use_id": use["id"], "content": output, "is_error": is_error})
        return results

    def advance(self) -> str:
        """Work until the agent has nothing more to do right now. Returns the run's status."""
        store.set_status(self.run, "working")
        for _ in range(MAX_STEPS):
            turns = store.get_turns(self.run)
            content = self._run_pending_tools(turns) + self._inbox_blocks()
            if content:
                store.add_turn(self.run, "user", content)
                turns = store.get_turns(self.run)
            elif turns[-1]["role"] == "assistant":
                store.set_status(self.run, "waiting")
                return "waiting"
            try:
                response = self._request(turns)
            except anthropic.RateLimitError:
                store.log(self.run, "llm_retry", reason="rate limit")
                time.sleep(10)
                continue
            except anthropic.APIConnectionError:
                store.log(self.run, "llm_retry", reason="connection")
                time.sleep(5)
                continue
            usage = response.usage
            store.log(self.run, "llm_usage", input=usage.input_tokens, output=usage.output_tokens, cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0, cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0)
            store.add_turn(self.run, "assistant", _blocks(response.content))
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    store.log(self.run, "agent_note", text=block.text)
            if response.stop_reason == "refusal":
                store.log(self.run, "refusal", details=str(getattr(response, "stop_details", "")))
                store.set_status(self.run, "needs_person")
                return "needs_person"
            if response.stop_reason in ("end_turn", "stop_sequence") and not store.unhandled_inbox(self.run):
                store.set_status(self.run, "waiting")
                return "waiting"
        store.log(self.run, "step_limit")
        store.set_status(self.run, "needs_person")
        return "needs_person"
