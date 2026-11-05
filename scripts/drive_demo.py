"""Drive one full demo through the running web app, approving exceptions and running the delivery day.

Usage: python scripts/drive_demo.py "request text" [fault]
"""
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("TREX_BASE", "http://127.0.0.1:8000")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    text = sys.argv[1]
    fault = sys.argv[2] if len(sys.argv) > 2 else None
    client = httpx.Client(base_url=BASE, timeout=60)
    run = client.post("/api/request", json={"text": text}).json()["run"]
    print("run", run)
    if fault:
        client.post(f"/api/fault?run={run}", json={"fault": fault, "times": 3})
    tomorrow = False
    start = last_move = time.time()
    seen = 0
    answered = set()
    while time.time() - last_move < 150 and time.time() - start < 560:
        s = client.get(f"/api/state?run={run}").json()
        for e in s.get("timeline", []):
            if e["seq"] <= seen:
                continue
            seen = e["seq"]
            last_move = time.time()
            if e["kind"] not in ("tool_call", "message_sent", "reply"):
                print(f"[{time.time() - start:5.0f}s]", e["kind"], json.dumps({k: v for k, v in e.items() if k not in ("seq", "ts", "kind")}, ensure_ascii=False)[:240])
        for d in s.get("decisions", []):
            if d["answer"] is None and d["id"] not in answered:
                print("DECISION:", d["question"], d["options"])
                answer = next((o for o in d["options"] if o.lower().startswith("approve")), d["options"][0])
                client.post(f"/api/decision/{d['id']}?run={run}", json={"answer": answer})
                answered.add(d["id"])
        decided = any(t.get("decision") for t in s["board"]["trucks"])
        if decided and s["run"]["status"] == "waiting" and s.get("sheet") and not tomorrow:
            print("delivery day:", client.post(f"/api/tomorrow?run={run}").json())
            tomorrow = True
        time.sleep(1.5)
    s = client.get(f"/api/state?run={run}").json()
    focus = next(t for t in s["board"]["trucks"] if t["truck"] == s["focus"])
    print("status", s["run"]["status"], "focus", s["focus"], "fill", focus["fill"], "kg", focus["kg"], "orders", s["orders_saved"], "drops", len(s["drops"]), "misses", len(s["misses"]))
    print("safety", s["safety"], "cost", s["cost"])
    print("order events", [e["kind"] for e in s["order_events"]][:12])
    for r in s.get("reports", []):
        print("REPORT:", r["text"])


if __name__ == "__main__":
    main()
