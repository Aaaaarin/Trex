"""Run one request from the terminal: python -m trex.cli "Route 4 wali gaadi kal jayegi?"

Prints what the agent does. Approves decisions and runs the delivery day automatically.
"""
import json
import sys
import time

from . import orchestrator, store


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    request = " ".join(sys.argv[1:]) or "Route 4 wali gaadi kal jayegi? Butter 500 gram ka kya scene hai?"
    run = f"cli{int(time.time())}"
    orchestrator.start_run(run, request)
    seen, last_move, tomorrow_done = 0, time.time(), False
    while time.time() - last_move < 120:
        for e in store.rows("select seq, kind, data from events where run=? and seq>? order by seq", (run, seen)):
            seen = e["seq"]
            last_move = time.time()
            if e["kind"] in ("llm_usage", "gate_passed", "tool_result"):
                continue
            print(e["kind"], json.dumps(json.loads(e["data"]), ensure_ascii=False)[:300])
        for d in store.rows("select id, question from decisions where run=? and answer is null", (run,)):
            orchestrator.answer_decision(run, d["id"], "Approve", "cli")
        status = store.rows("select status from runs where id=?", (run,))[0]["status"]
        if status == "waiting" and store.get_kv(run, "decided", {}) and not tomorrow_done:
            has_sheet = any(store.get_kv(run, f"sheet:{t}") for t in store.get_kv(run, "decided", {}))
            if has_sheet:
                orchestrator.run_tomorrow(run)
                tomorrow_done = True
                last_move = time.time()
        time.sleep(1)
    print("run", run, "status", status)


if __name__ == "__main__":
    main()
