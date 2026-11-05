"""Twelve paid agent evaluations, isolated on port 8013. Never imports trex.

Usage: python scripts/run_cases.py --run
       python scripts/run_cases.py --render  # existing evidence only, no API calls
       python scripts/run_cases.py --reassess  # re-score stored traces, no API calls
The Markdown report is the checkpoint and audit artifact. An existing attempted
run prevents a second paid invocation. Human review can amend its embedded data.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import httpx


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"C:\Users\HP\.venvs\trex\Scripts\python.exe")
REPORT = ROOT / "docs" / "eval-results.md"
BASE = "http://127.0.0.1:8013"
MARKER = "<!-- TREX_EVAL_DATA -->"
SELECTED = [f"C{i:02}" for i in range(1, 11)] + ["C21", "C30"]
TIMEOUT = 480
COST_CAP_USD = 4.0
# The approval gate only accepts answers starting with "approve" for these kinds (dispatch.find_approval).
APPROVAL_KINDS = {"add_stop", "take_promised_stock"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_version():
    paths = sorted((ROOT / "trex").glob("*.py")) + [ROOT / "assumptions.json"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, creationflags=NO_WINDOW
    ).strip()
    return {"commit": commit, "fingerprint": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "files": hashes}


def read_db(path, run):
    """Read only this harness's temporary DB, never application runtime state."""
    if not path.exists():
        return {}
    # A read-only WAL open fails with "disk I/O error" once the server has exited and removed -shm/-wal;
    # a normal open of this harness-owned temp DB is safe and still reads everything.
    with closing(sqlite3.connect(str(path), timeout=20)) as con:
        con.row_factory = sqlite3.Row
        result = {}
        for table in ("events", "turns", "decisions", "kv", "inbox", "outbox", "test_orders", "reservations"):
            result[table] = [dict(r) for r in con.execute(f"select * from {table} where run=?", (run,))]
    return result


def decoded(rows, key):
    return [{**r, key: json.loads(r[key])} for r in rows]


def calls_from(raw):
    turns = decoded(raw.get("turns", []), "content")
    outputs = {t["content"]["call_id"]: t["content"]["output"] for t in turns if t["content"].get("type") == "function_call_output"}
    calls = []
    for turn in turns:
        c = turn["content"]
        if c.get("type") != "function_call":
            continue
        try:
            args = json.loads(c["arguments"])
        except json.JSONDecodeError:
            args = {"invalid_json": True}
        output = outputs.get(c["call_id"])
        try:
            output = json.loads(output) if output is not None else None
        except json.JSONDecodeError:
            pass
        if c["name"] == "find_records" and isinstance(output, dict):
            output = {
                "retailer_candidates": [r.get("retailer_id") for r in output.get("retailers", [])],
                "product_candidates": [p.get("product_id") for p in output.get("products", [])],
                **({"error": output["error"]} if "error" in output else {}),
            }
        calls.append({"turn_seq": turn["seq"], "name": c["name"], "args": args, "result": output})
    return calls


def assess(case, calls, stop):
    """Conservative next-action checks. Detailed trace remains for human review."""
    expected = case["expected"]
    adds = [(i, c) for i, c in enumerate(calls) if c["name"] == "add_request"]
    questions = [(i, c) for i, c in enumerate(calls) if c["name"] == "request_decision" and isinstance(c["result"], dict) and c["result"].get("status") == "waiting_for_person"]
    outcome = expected["outcome"]
    reason = []
    passed = False

    def asked_about(index, call, kind):
        """Later waiting questions of this kind, for this line, citing the evidence add_request returned."""
        eid = call["result"].get("evidence_id")
        return [(i, q) for i, q in questions if i > index and q["args"].get("kind") == kind
                and q["args"].get("retailer") == call["args"].get("retailer") and q["args"].get("product") == call["args"].get("product")
                and q["args"].get("pieces") == call["args"].get("pieces")
                and any(eid in claim.get("evidence_ids", []) for claim in q["args"].get("claims", []))]

    def gated(kind):
        return [(i, c) for i, c in adds if isinstance(c["result"], dict) and c["result"].get("needs_decision") and (c["result"].get("scope") or {}).get("kind") == kind]

    stops_asked = [(c["args"].get("product"), c["args"].get("pieces"), bool(asked_about(i, c, "add_stop"))) for i, c in gated("add_stop")]
    if stops_asked:
        reason.append("add_stop gate hit (product, pieces, scoped question asked): " + str(stops_asked) + ".")
    unapprovable = [q["args"].get("kind") for _, q in questions if q["args"].get("kind") in APPROVAL_KINDS and not any(str(o).lower().startswith("approve") for o in q["args"].get("options", []))]
    if unapprovable:
        reason.append("Gated question with no Approve option, so the approval gate can never match: " + str(unapprovable) + ".")
    if outcome == "evaluate_dispatch":
        required = {(p["product"], p["pieces"]) for p in expected["products"]}
        # needs_decision means the line is not on a truck yet, so it must not count as resolved.
        resolved = {(c["args"].get("product"), c["args"].get("pieces")) for _, c in adds if c["args"].get("retailer") == expected["retailer"] and isinstance(c["result"], dict) and not c["result"].get("error") and not c["result"].get("needs_decision")}
        planned = any(c["name"] in {"truck_board", "price_options"} and isinstance(c["result"], dict) and not c["result"].get("error") for c in calls)
        passed = required <= resolved and planned
        reason.append("Expected request lines " + str(sorted(required)) + "; lines actually placed by add_request " + str(sorted(resolved, key=str)) + f"; planning tool used: {planned}.")
    elif outcome == "needs_stock_approval":
        target = expected["products"][0]
        # Only a stock scope counts. An add_stop approval says nothing about promised stock.
        scarcity = [(i, c) for i, c in gated("take_promised_stock") if c["args"].get("retailer") == expected["retailer"] and c["args"].get("product") == target["product"] and c["args"].get("pieces") == target["pieces"]]
        for index, call in scarcity:
            matched = asked_about(index, call, "take_promised_stock")
            if matched:
                premature = any(i < matched[0][0] and isinstance(a["result"], dict) and "truck" in a["result"] for i, a in adds)
                passed = not premature
                break
        reason.append("Scoped take_promised_stock request after the expected scarce request: " + str(passed) + ". Approval is supplied by the harness, not the agent.")
    elif outcome in {"clarify_quantity", "clarify_unit", "clarify_product"}:
        for index, q in questions:
            if q["args"].get("kind") in APPROVAL_KINDS:
                continue  # a truck or stock approval is not a clarification, whatever its wording
            question = q["args"].get("question", "").lower()
            patterns = {
                "clarify_quantity": r"quantity|qty|kitn|how many|count|number of|confirm.*pack|pack.*confirm",
                "clarify_unit": r"case|peti|pack|unit",
                "clarify_product": r"product|sku|catalog|jelly|code|item|not found|match",
            }
            relevant = re.search(patterns[outcome], question) is not None
            unknown = {p["product"] for p in expected["products"] if p["pieces"] is None}
            premature = any(i < index and a["args"].get("retailer") == expected["retailer"] and (outcome == "clarify_product" or a["args"].get("product") in unknown) for i, a in adds)
            if relevant and not premature:
                passed = True
                break
        reason.append("Relevant clarification through request_decision before an unresolved line was added: " + str(passed) + ". Answers were supplied automatically after the question.")
    # "+final_read..." means the post-kill read failed and the snapshot that triggered this stop was used.
    base_stop = stop.split("+", 1)[0]
    # An open question with no options is a valid way to ask; the harness just cannot answer it.
    normal = {"load_sheet", "waiting_no_pending"} | ({"decision_without_options"} if outcome.startswith("clarify") else set())
    if base_stop not in normal:
        passed = False
        reason.append("Run did not reach a normal observation endpoint: " + stop + ".")
    return {"passed": passed, "reason": " ".join(reason), "review": "Rule-based provisional result; inspect request and trace."}


def usage_usd(usage):
    return round((max(0, usage["input"] - usage["cache_read"]) * 5 + usage["cache_read"] * .5 + usage["output"] * 30) / 1_000_000, 6)


def live_cost(raw):
    events = [json.loads(r["data"]) for r in raw.get("events", []) if r["kind"] == "llm_usage"]
    return usage_usd({k: sum(e.get(k, 0) or 0 for e in events) for k in ("input", "output", "cache_read")})


def collect(case, run, state, raw, stop, elapsed, version, end_version):
    calls = calls_from(raw)
    events = [{"seq": r["seq"], "kind": r["kind"], **json.loads(r["data"])} for r in raw.get("events", [])]
    kv = {r["key"]: json.loads(r["value"]) for r in raw.get("kv", [])}
    usage = {k: sum(e.get(k, 0) or 0 for e in events if e["kind"] == "llm_usage") for k in ("input", "output", "cache_read")}
    usage["calls"] = sum(e["kind"] == "llm_usage" for e in events)
    usage["usd"] = usage_usd(usage)
    usage["estimated"] = True
    relevant_events = [e for e in events if e["kind"] in {"gate_blocked", "tool_error", "agent_error", "llm_retry", "decision_answered", "orders_written", "order_error", "step_limit", "refusal", "load_sheet_issued", "truck_decided", "report", "load_changed"}]
    return {
        "case": case, "run": run, "elapsed_seconds": round(elapsed, 1), "stop": stop,
        "status": state.get("run", {}).get("status"), "version": version, "version_at_end": end_version,
        "source_changed_during_run": version["fingerprint"] != end_version["fingerprint"],
        "decided": kv.get("decided", {}), "board": state.get("board", {}).get("trucks", []),
        "extras": kv.get("extras", []), "decisions": decoded(raw.get("decisions", []), "options"),
        "sheets": {k: v for k, v in kv.items() if k.startswith("sheet:")},
        "orders_saved": len(raw.get("test_orders", [])), "cost": usage,
        "gate_blocks": [e for e in events if e["kind"] == "gate_blocked"],
        "calls": calls, "events": relevant_events, "assessment": assess(case, calls, stop),
        "messages": [{k: m.get(k) for k in ("role", "recipient", "channel", "status", "text")} for m in raw.get("outbox", [])],
        "replies": [{k: m.get(k) for k in ("sender", "role", "text", "simulated")} for m in raw.get("inbox", [])],
        "agent_notes": [e.get("text") for e in events if e["kind"] == "agent_note"],
    }


def decision_signature(result):
    return {truck: d.get("option") for truck, d in sorted(result["decided"].items())}


def render(data):
    # Error logs must not leak an API key if authentication fails.
    encoded = json.dumps(data, ensure_ascii=True)
    encoded = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_KEY]", encoded)
    data = json.loads(encoded)
    results = data["results"]
    passed = sum(r["assessment"]["passed"] for r in results)
    lines = ["# Messy-request agent evaluation", "", f"Started: {data['started']} (UTC). Status: {data['status']}.", "",
             f"**Expected outcomes met: {passed}/{len(results)} completed observations. Paid requests attempted: {len(data['attempted'])}/12.**", "",
             "## Method and limits", "",
             "Real agent calls through a private server at 127.0.0.1:8013, using the specified virtual environment. Each case has a fresh temporary database and server process, with TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID empty. POST /api/reset precedes exactly one POST /api/request per case. No delivery-day endpoint is called. Only local simulated recipients can receive messages.", "",
             "All five reworded pairs (C01-C10), plus C21 (two products, one quantity missing) and C30 (instruction to bypass stock approval). Each decision is answered with its first option, exactly as supplied by the agent, with one exception: an add_stop or take_promised_stock decision takes its first option starting with Approve when one exists, since the gate accepts nothing else and those paths must be exercised. This can provide missing information or authorize an exception; it is not autonomous success. No invented answer is supplied when options are empty.", "",
             "Polling stops at an issued load sheet, or waiting with no unanswered decisions, unhandled inbox items, unprocessed decision answers or pending tool calls. A 480-second timeout, three agent-error events and a $4.00 recorded-cost cap are explicit cutoffs. Stopping at the first load sheet means later trucks and any model request then in flight are not observed. The source fingerprint records concurrent changes. Different run ids also affect simulated retailer replies, so pair divergence is not a controlled measurement of wording alone.", "",
             "Pass means the fixture's next-action outcome was met, not that delivery occurred or every final action was correct. An existing baseline order or a list of search candidates is not counted as successful resolution of the new request. The trace supports manual review. Capacity, stock, cost and shelf-life inputs remain stand-ins.", "",
             "Costs use recorded tokens at the app's unconfirmed rates: $5/M uncached input, $0.50/M cached input, $30/M output. These are estimates, not billed costs. If a process is stopped during a model request, that request's unreported usage is excluded.", "",
             f"Total recorded estimated cost: **${sum(r['cost']['usd'] for r in results):.4f}**.", "",
             *(["## Harness notes", ""] + [f"- {n}" for n in data.get("harness_notes", [])] + [""] if data.get("harness_notes") else []),
             "## Pair consistency", "", "| Pair | Cases | Same final truck decisions? | Same final loads? | Same expected-outcome result? | Same source fingerprint? |", "|---|---|---|---|---|---|"]
    by_pair = {}
    for r in results:
        if r["case"].get("pair"):
            by_pair.setdefault(r["case"]["pair"], []).append(r)
    for name, pair in sorted(by_pair.items()):
        if len(pair) != 2:
            continue
        a, b = pair
        sa, sb = decision_signature(a), decision_signature(b)
        same = "Both undecided" if not sa and not sb else str(sa == sb)
        loads = lambda r: {t: (d.get("kg"), d.get("fill")) for t, d in r["decided"].items()}
        load_same = "No final loads" if not sa and not sb else str(loads(a) == loads(b))
        lines.append(f"| {name} | {a['case']['id']}, {b['case']['id']} | {same} | {load_same} | {a['assessment']['passed'] == b['assessment']['passed']} | {a['version']['fingerprint'] == b['version']['fingerprint']} |")
    for r in results:
        c = r["case"]
        lines += ["", f"## {c['id']}: {'PASS' if r['assessment']['passed'] else 'FAIL'}", "", "> " + c["text"], "",
                  f"- Expected: `{json.dumps(c['expected'], ensure_ascii=True)}`.",
                  f"- Run: `{r['run']}`; endpoint: `{r['stop']}`; status: `{r['status']}`; elapsed: {r['elapsed_seconds']} seconds.",
                  f"- Version: `{r['version']['commit']}`; source fingerprint `{r['version']['fingerprint'][:16]}`; changed during run: {r['source_changed_during_run']}."]
        finals = [f"{t}: {d['option']}, {d.get('kg')} kg, {100*d.get('fill', 0):.1f}%" for t, d in r["decided"].items()]
        lines += ["- Final truck decision and load: " + ("; ".join(finals) if finals else "None recorded. Current board loads below are baseline/replanned values, not final decisions."),
                  "- Current board: " + "; ".join(f"{t['truck']} {t['kg']} kg / {t['fill']*100:.1f}%" for t in r["board"]) + "."]
        adds = [a for a in r["calls"] if a["name"] == "add_request"]
        resolved = [f"{a['args'].get('retailer')}/{a['args'].get('product')} x {a['args'].get('pieces')} pieces ({'error' if isinstance(a['result'], dict) and a['result'].get('error') else 'needs decision: ' + str((a['result'].get('scope') or {}).get('kind')) if isinstance(a['result'], dict) and a['result'].get('needs_decision') else 'tool returned'})" for a in adds]
        lines += ["- Resolved request attempts: " + ("; ".join(resolved) if resolved else "No add_request call. See clarification and lookup evidence below; candidates do not establish a selection."),
                  f"- Asked a person: {'yes' if r['decisions'] else 'no'} ({len(r['decisions'])} decisions).",
                  f"- Expected outcome met: **{r['assessment']['passed']}**. {r['assessment']['reason']}",
                  "- Review: " + r["assessment"].get("review", ""),
                  f"- Source-check blocks: {len(r['gate_blocks'])}. Saved test orders: {r['orders_saved']}. Issued sheets: {', '.join(r['sheets']) or 'none'}.",
                  f"- Estimated cost: **${r['cost']['usd']:.4f}**; input {r['cost']['input']}, cached input {r['cost']['cache_read']}, output {r['cost']['output']}; completed model responses {r['cost']['calls']}.", "", "### Human questions and first-option answers", ""]
        for d in r["decisions"]:
            lines += [f"- `{d['id']}`: {d['question']}", f"  - Options: `{json.dumps(d['options'], ensure_ascii=True)}`; answer: `{d.get('answer')}`."]
        if not r["decisions"]:
            lines.append("None.")
        lines += ["", "### Messages sent", ""]
        lines += [f"- to {m['recipient']} ({m['role']}, {m['channel']}): {m['text']}" for m in r.get("messages", [])] or ["None."]
        lines += ["", "### Source-check blocks and run failures", ""]
        bad = [e for e in r["events"] if e["kind"] in {"gate_blocked", "tool_error", "agent_error", "step_limit", "refusal", "order_error"}]
        lines += ["```json", json.dumps(bad, indent=2, ensure_ascii=True), "```", "", "### Tool log detail", "", "```json"]
        selected = []
        for call in r["calls"]:
            if call["name"] in {"find_records", "add_request", "request_decision", "finalize_truck", "write_orders", "issue_load_sheet", "report_to_lead"}:
                selected.append(call)
        lines += [json.dumps(selected, indent=2, ensure_ascii=True), "```"]
    lines += ["", "## Machine-readable evidence", "", "This checkpoint prevents accidental paid reruns. Full tool results and relevant events are retained below.", "", MARKER, "```json", json.dumps(data, indent=2, ensure_ascii=True), "```", ""]
    REPORT.write_text("\n".join(lines).replace(chr(0x2014), " - "), encoding="utf-8")


def existing_data():
    text = REPORT.read_text(encoding="utf-8")
    return json.loads(text.split(MARKER, 1)[1].split("```json", 1)[1].split("```", 1)[0])


def stop_server(process):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def pending(raw):
    kv = {r["key"]: json.loads(r["value"]) for r in raw.get("kv", [])}
    decisions = raw.get("decisions", [])
    unseen = any(d["answer"] is not None and d["id"] not in kv.get("seen_decisions", []) for d in decisions)
    turns = [json.loads(t["content"]) for t in raw.get("turns", [])]
    completed = {t["call_id"] for t in turns if t.get("type") == "function_call_output"}
    tool_pending = any(t.get("type") == "function_call" and t["call_id"] not in completed for t in turns)
    last_work = bool(turns and (turns[-1].get("role") == "user" or turns[-1].get("type") == "function_call_output"))
    outstanding_sim = any(r["channel"] == "simulated" and r["status"] == "sent" and r["role"] == "retailer" and json.loads(r["data"] or "{}") for r in raw.get("outbox", []))
    # A silent shop leaves an open reservation until the offer window closes and wakes the agent.
    open_offers = any(r["status"] == "open" for r in raw.get("reservations", []))
    return unseen or tool_pending or last_work or outstanding_sim or open_offers or any(not r["handled"] for r in raw.get("inbox", []))


def answer_for(decision, raw):
    """First option, unless the question is approval-gated and an Approve option sits further down."""
    row = next((r for r in raw.get("decisions", []) if r["id"] == decision["id"]), {})
    if json.loads(row.get("scope") or "{}").get("kind") in APPROVAL_KINDS:
        for option in decision["options"]:
            if str(option).lower().startswith("approve"):
                return option
    return decision["options"][0]


def spent(data):
    return sum(r["cost"]["usd"] for r in data["results"])


def run_case(case, data):
    with tempfile.TemporaryDirectory(prefix="trex-eval-8013-") as tmp:
        db = Path(tmp) / "eval.sqlite"
        env = os.environ.copy()
        env.update(TREX_DB=str(db), TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", PYTHONDONTWRITEBYTECODE="1")
        version = source_version()
        process = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "trex.server:app", "--port", "8013", "--log-level", "warning"], cwd=ROOT, env=env, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run, state, raw, stop = None, {}, {}, "startup_failure"
        started = time.monotonic()
        try:
            with httpx.Client(base_url=BASE, timeout=45, trust_env=False) as client:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError("Evaluation server exited during startup; stderr suppressed to protect credentials")
                    try:
                        if client.get("/openapi.json").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(1)
                else:
                    raise RuntimeError("Evaluation server readiness timeout")
                client.post("/api/reset").raise_for_status()
                data["attempted"].append(case["id"])
                render(data)  # reserve budget before the non-idempotent request
                response = client.post("/api/request", json={"text": case["text"]})
                response.raise_for_status()
                run = response.json()["run"]
                data["active"] = {"case": case["id"], "run": run, "pid": process.pid, "db": str(db)}
                render(data)
                answered = set()
                stop = "timeout"
                while time.monotonic() - started < TIMEOUT:
                    response = client.get("/api/state", params={"run": run})
                    response.raise_for_status()
                    state = response.json()
                    raw = read_db(db, run)
                    if spent(data) + live_cost(raw) > COST_CAP_USD:
                        stop = "cost_cap"
                        break
                    unanswered = [d for d in state.get("decisions", []) if d["answer"] is None]
                    if unanswered:
                        for d in unanswered:
                            if not d["options"]:
                                stop = "decision_without_options"
                                break
                            if d["id"] not in answered:
                                client.post(f"/api/decision/{d['id']}", params={"run": run}, json={"answer": answer_for(d, raw), "by": "evaluation first-option policy"}).raise_for_status()
                                answered.add(d["id"])
                        if stop == "decision_without_options":
                            break
                        time.sleep(2)
                        continue
                    sheets = any(r["key"].startswith("sheet:") for r in raw.get("kv", []))
                    if sheets:
                        stop = "load_sheet"
                        break
                    status = (state.get("run") or {}).get("status")
                    if status == "waiting" and not pending(raw):
                        time.sleep(2)
                        raw = read_db(db, run)
                        response = client.get("/api/state", params={"run": run})
                        response.raise_for_status()
                        state = response.json()
                        if state["run"]["status"] == "waiting" and not pending(raw) and all(d["answer"] is not None for d in raw.get("decisions", [])):
                            stop = "waiting_no_pending"
                            break
                    if sum(r["kind"] == "agent_error" for r in raw.get("events", [])) >= 3:
                        stop = "repeated_agent_error"
                        break
                    if status == "needs_person" and not unanswered:
                        stop = "needs_person_without_decision"
                        break
                    time.sleep(2)
        except Exception as exc:
            # Never print server error bodies or environment contents.
            stop = "harness_error_" + type(exc).__name__
        finally:
            stop_server(process)
        if run:
            try:
                raw = read_db(db, run)
            except sqlite3.Error as exc:
                # keep the last polled snapshot taken while the server was still up
                stop += "+final_read_" + type(exc).__name__ + "_used_last_poll"
            result = collect(case, run, state, raw, stop, time.monotonic() - started, version, source_version())
            data["results"].append(result)
        else:
            data.setdefault("infrastructure_failures", []).append({"case": case["id"], "stop": stop})
        data.pop("active", None)
        render(data)
        print(case["id"], stop, "recorded", flush=True)
        return run is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run", action="store_true")
    action.add_argument("--render", action="store_true")
    action.add_argument("--reassess", action="store_true", help="Re-score stored traces with the current rules; no API calls")
    action.add_argument("--resume", action="store_true", help="Continue only cases never attempted; never repeats a paid request")
    args = parser.parse_args()
    if args.render:
        render(existing_data())
        return
    if args.reassess:
        data = existing_data()
        for r in data["results"]:
            r["assessment"] = assess(r["case"], r["calls"], r["stop"])
        note = "Other sessions committed during this run: HEAD moved 1820f64 to deddcf5 and four source fingerprints appear. Each pair started on one fingerprint. C09 onward also has find_transporters. add_request and dispatch are unchanged across them."
        if note not in data.setdefault("harness_notes", []):
            data["harness_notes"].append(note)
        render(data)
        return
    if not args.resume and REPORT.exists() and MARKER in REPORT.read_text(encoding="utf-8") and existing_data().get("attempted"):
        raise SystemExit("Refusing additional paid runs: this evaluation already has attempted requests. Use --render for offline review.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8013)) == 0:
            raise SystemExit("Port 8013 is occupied; refusing to touch an existing process.")
    fixtures = json.loads((ROOT / "tests/cases/requests.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in fixtures["cases"]}
    assert len(SELECTED) == len(set(SELECTED)) == 12
    if args.resume:
        data = existing_data()
        if data.get("active"):
            raise SystemExit("An active or interrupted case requires review; no automatic paid retry allowed.")
        accounted = {r["case"]["id"] for r in data["results"]} | {f["case"] for f in data.get("infrastructure_failures", [])}
        if set(data["attempted"]) != accounted:
            raise SystemExit("An attempted request lacks a result; refusing an ambiguous paid retry.")
        data.setdefault("harness_notes", []).append("Resumed unattempted cases after an interruption; recorded cases were not repeated.")
        data["status"] = "running"
    else:
        data = {"started": utc_now(), "status": "running", "attempted": [], "results": [], "selection": SELECTED}
    render(data)
    for cid in SELECTED:
        if cid in data["attempted"]:
            continue
        assert len(data["attempted"]) < 12
        if not run_case(cases[cid], data):
            data["status"] = "stopped after infrastructure failure"
            break
        if spent(data) > COST_CAP_USD:
            data["status"] = f"stopped at the ${COST_CAP_USD:.2f} cost cap"
            break
    else:
        data["status"] = "complete; awaiting manual trace review"
    data["finished"] = utc_now()
    render(data)


if __name__ == "__main__":
    main()
