"""Everything the screens show, read from saved state. The web page holds no truth of its own."""
import json

from . import store
from .agent import Agent  # noqa: F401  (keeps import order predictable for the server)
from .config import ASSUMPTIONS, DEMO, standin
from .ledger import Ledger
from .planning import Night
from .tools import Tools
from .upstream import cover, trace

BUTTER = "P006"
PRICE_PER_MTOK = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}
_night_cache: dict[str, tuple[str, Night]] = {}


def _night(run: str | None) -> Night:
    extras = store.get_kv(run, "extras", []) if run else []
    sig = json.dumps(extras, sort_keys=True)
    key = run or "_"
    if key not in _night_cache or _night_cache[key][0] != sig:
        _night_cache[key] = (sig, Night(DEMO["plan_evening"], DEMO["plan_date"], DEMO["distributor"], extras))
    return _night_cache[key][1]


def _events(run: str, kinds: tuple[str, ...] | None = None, limit: int = 400) -> list[dict]:
    rows = store.rows("select seq, ts, kind, data from events where run=? order by seq desc limit ?", (run, limit))
    rows.reverse()
    out = []
    for r in rows:
        if kinds and r["kind"] not in kinds:
            continue
        out.append({"seq": r["seq"], "ts": r["ts"], "kind": r["kind"], **json.loads(r["data"])})
    return out


def _offer_rows(run: str, truck: str) -> list[dict]:
    planned = store.get_kv(run, f"offers:{truck}", {})
    extras = {(e["retailer"], e["product"]): e for e in store.get_kv(run, "extras", []) if e["source"] == "offer"}
    replies: dict[str, dict] = {}
    for r in store.rows("select sender, text, simulated from inbox where run=? and role='retailer' order by seq", (run,)):
        replies[r["sender"]] = r
    sent = {r["recipient"]: r for r in store.rows("select recipient, text, channel, data from outbox where run=? and role='retailer' and key like '%:offer:%'", (run,))}
    out = []
    for retailer, o in planned.items():
        accepted = extras.get((retailer, o["product"]))
        reply = replies.get(retailer)
        state = "planned"
        if retailer in sent:
            state = "waiting"
        if reply:
            state = "replied"
        if accepted:
            state = "yes"
        added = round(accepted["pieces"] * (o["kg"] / max(o["pieces"], 1)), 1) if accepted else 0
        out.append({**o, "state": state, "reply": reply["text"] if reply else None, "simulated": bool(reply["simulated"]) if reply else (sent.get(retailer, {}).get("channel") != "telegram"), "added_kg": added})
    return out


def _cost(run: str) -> dict:
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}
    for e in _events(run, ("llm_usage",), 2000):
        total["calls"] += 1
        for k in ("input", "output", "cache_read", "cache_write"):
            total[k] += e.get(k) or 0
    usd = sum(total[k] * PRICE_PER_MTOK[k] for k in PRICE_PER_MTOK) / 1_000_000
    return {**total, "usd": round(usd, 3)}


def butter_chart() -> dict:
    """The real gap: every bill for the biggest butter pack in the month, masked. Not limited by as-of."""
    from .ledger import _all_lines

    d = _all_lines()
    d = d[d["distributor"] == DEMO["distributor"]]
    days = sorted(d["invoice_date"].unique())
    billed = d[d["product_id"] == BUTTER].groupby("invoice_date")["net_amount"].sum().round().to_dict()
    return {"product": BUTTER, "label": "Butter 500 g", "days": [{"date": x, "billed_inr": billed.get(x, 0)} for x in days]}


def board(run: str | None) -> dict:
    night = _night(run)
    trucks = night.truck_summary()
    decided = store.get_kv(run, "decided", {}) if run else {}
    target = standin("fill_target")
    for t in trucks:
        t["decision"] = decided.get(t["truck"])
        t["at_target"] = t["fill"] >= target
    return {"trucks": trucks, "target": target, "plan_date": DEMO["plan_date"], "records_to": DEMO["plan_evening"], "distributor": DEMO["distributor"]}


def batches(run: str | None) -> list[dict]:
    night = _night(run)
    hero = DEMO["hero_truck"]
    risk = night.expiry_risk(hero)["batches"]
    return risk[:12]


def retailer_view(run: str | None, retailer: str) -> dict:
    t = Tools(run or "_view", DEMO["plan_evening"], DEMO["plan_date"], DEMO["distributor"])
    ledger = Ledger(DEMO["plan_evening"], DEMO["distributor"])
    info = t._retailer(retailer)
    d = ledger.lines()
    h = d[d["retailer_id"] == retailer]
    top = h.groupby(["product_id", "product_name"], as_index=False)["net_amount"].sum().sort_values("net_amount", ascending=False).head(6)
    from .belief import estimate

    rows = [{"product": r.product_id, "product_name": r.product_name, **estimate(ledger, retailer, r.product_id).dict()} for r in top.itertuples()]
    return {"retailer": info, "bills": int(h["invoice_id"].nunique()), "beliefs": rows, "type_note": "Inferred from name and packs, not recorded"}


def snapshot(run: str | None) -> dict:
    out = {"board": board(run), "butter": butter_chart(), "standins": {k: v["note"] for k, v in ASSUMPTIONS["standins"].items()}, "upstream": trace(BUTTER), "cover": cover(BUTTER)}
    if not run:
        return {**out, "run": None}
    row = store.rows("select id, request, status, created from runs where id=?", (run,))
    if not row:
        return {**out, "run": None}
    focus = store.get_kv(run, "focus", DEMO["hero_truck"])
    options = store.get_kv(run, f"options:{focus}")
    night = _night(run)
    decisions = store.rows("select id, question, options, answer, answered_by from decisions where run=? order by ts", (run,))
    for d in decisions:
        d["options"] = json.loads(d["options"])
    messages = store.rows("select role, recipient as who, text, channel, ts, 'out' as direction from outbox where run=? union all select role, sender as who, text, case simulated when 1 then 'simulated' else 'telegram' end, ts, 'in' from inbox where run=? order by ts", (run, run))
    gate_blocks = len(_events(run, ("gate_blocked",), 2000))
    out.update(
        {
            "run": row[0],
            "focus": focus,
            "options": options,
            "offers": _offer_rows(run, focus),
            "load_events": _events(run, ("load_changed",)),
            "timeline": [e for e in _events(run) if e["kind"] not in ("tool_result", "llm_usage", "gate_passed", "status")][-60:],
            "messages": messages[-80:],
            "decisions": decisions,
            "sheet": night.load_sheet(focus) if store.get_kv(run, f"sheet:{focus}") else None,
            "drops": store.get_kv(run, "drops", {}),
            "misses": store.get_kv(run, "misses", []),
            "reports": _events(run, ("report",)),
            "batches": batches(run),
            "orders_saved": store.rows("select count(*) c from test_orders where run=?", (run,))[0]["c"],
            "order_events": _events(run, ("order_error", "order_read_back_found", "order_already_saved", "orders_written")),
            "safety": {"source_check_blocks": gate_blocks, "duplicate_replies_ignored": len(_events(run, ("duplicate_reply_ignored",), 2000)), "sends_skipped": len(_events(run, ("send_skipped_duplicate",), 2000)), "resumes": len(_events(run, ("resumed_after_restart",), 2000))},
            "cost": _cost(run),
            "live": store.get_kv(run, "live", {}),
        }
    )
    return out
