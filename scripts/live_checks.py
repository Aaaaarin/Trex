"""Run two behaviours against the real agent, each on its own server and temporary database.

Usage: python scripts/live_checks.py [case names]   (spends a small amount of model credit)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CASES = [
    {"name": "hard request, failed write", "port": 8021, "text": "Route 4 ki gaadi kal jayegi? load kam hai. Hotel R-120 ko Butter 30x500 CP ke 200 packet chahiye kal subah", "fault": "500_after_write", "focus": "V2"},
    {"name": "truck that cannot reach target", "port": 8022, "text": "Route 13 ki gaadi kal jayegi kya? Bahut khaali lag rahi hai, kya karein", "fault": None, "focus": "V3"},
    {"name": "merge", "port": 8023, "text": "Route 13 ki gaadi kal jayegi kya? Bahut khaali lag rahi hai, kya karein", "fault": None, "focus": "V3", "prefer": "merge"},
    {"name": "hold", "port": 8024, "text": "Route 13 ki gaadi workshop mein hai, kal nahi ja sakti aur koi doosri gaadi uske stops nahi le sakti. Hold karwa do", "fault": None, "focus": "V3", "prefer": "hold"},
]


def run_case(case: dict, out: list) -> None:
    db = Path(tempfile.mkdtemp()) / "live.sqlite"
    base = f"http://127.0.0.1:{case['port']}"
    env = {**os.environ, "TREX_DB": str(db), "TREX_SIM_DELAY": "3", "PYTHONUTF8": "1", "TELEGRAM_BOT_TOKEN": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "trex.server:app", "--port", str(case["port"]), "--log-level", "warning"], cwd=ROOT, env=env, creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lines = [f"=== {case['name']}"]
    try:
        client = httpx.Client(base_url=base, timeout=60)
        for _ in range(60):
            try:
                client.get("/api/state")
                break
            except httpx.HTTPError:
                time.sleep(1)
        run = client.post("/api/request", json={"text": case["text"]}).json()["run"]
        if case["fault"]:
            client.post(f"/api/fault?run={run}", json={"fault": case["fault"], "times": 3})
        answered, tomorrow, start, last, signature = set(), False, time.time(), time.time(), ""
        while time.time() - start < 420 and time.time() - last < 90:
            s = client.get(f"/api/state?run={run}").json()
            status = s["run"]["status"]
            sig = json.dumps([status, len(s.get("timeline") or []), s.get("timeline", [{}])[-1].get("seq") if s.get("timeline") else 0])
            if sig != signature:
                signature, last = sig, time.time()
            for d in s.get("decisions", []):
                if d["answer"] is None and d["id"] not in answered:
                    prefer = case.get("prefer", "approve")
                    answer = next((o for o in d["options"] if prefer in o.lower()), None) or next((o for o in d["options"] if o.lower().startswith("approve")), d["options"][0])
                    lines.append(f"DECISION: {d['question'][:260]} | options {d['options']} | answered {answer}")
                    client.post(f"/api/decision/{d['id']}?run={run}", json={"answer": answer})
                    answered.add(d["id"])
            if status == "waiting" and s.get("sheet") and not tomorrow:
                lines.append(f"delivery day: {client.post(f'/api/tomorrow?run={run}').json()}")
                tomorrow, last = True, time.time()
            time.sleep(2)
        s = client.get(f"/api/state?run={run}").json()
        con = sqlite3.connect(db)
        kinds = dict(con.execute("select kind, count(*) from events group by kind").fetchall())
        tool_names = [json.loads(r[0])["name"] for r in con.execute("select data from events where kind='tool_call' order by seq").fetchall()]
        dup = con.execute("select count(*) - count(distinct recipient || text) from outbox").fetchone()[0]
        lines.append(f"status {s['run']['status']} board {[(t['truck'], t['fill'], (t.get('decision') or {}).get('option')) for t in s['board']['trucks']]}")
        lines.append(f"plan {s.get('plan') and {k: s['plan'][k] for k in ('version', 'status', 'option', 'fill')}} orders {s['orders_saved']} drops {len(s['drops'])} misses {len(s['misses'])}")
        lines.append("tools " + " ".join(tool_names)[:900])
        for k in ("plan_rejected", "plan_frozen", "gate_blocked", "order_read_back_found", "promise_blocked", "revision_proposed", "stock_reserved", "stock_released", "truck_held", "truck_merged", "tool_error", "agent_error"):
            if kinds.get(k):
                lines.append(f"  {k}: {kinds[k]}")
        for row in con.execute("select data from events where kind in ('plan_rejected', 'tool_error', 'agent_error') order by seq limit 5").fetchall():
            lines.append("  detail: " + row[0][:320])
        lines.append(f"identical messages sent twice {dup} cost {s['cost']['usd']}")
        for r in (s.get("reports") or [])[-1:]:
            lines.append("REPORT: " + r["text"][:900])
        con.close()
    except Exception as err:
        lines.append(f"FAILED {type(err).__name__}: {err}")
    finally:
        proc.kill()
        out.append("\n".join(lines))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    out: list[str] = []
    chosen = [c for c in CASES if len(sys.argv) < 2 or c["name"] in sys.argv[1:]]
    threads = [threading.Thread(target=run_case, args=(c, out)) for c in chosen]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("\n\n".join(out))


if __name__ == "__main__":
    main()
