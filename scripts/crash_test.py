"""Kill the real server mid-run, restart it, and check that nothing was lost or sent twice.

Usage: python scripts/crash_test.py   (spends a small amount of model credit)
"""
import collections
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8011
BASE = f"http://127.0.0.1:{PORT}"
DB = Path(tempfile.mkdtemp()) / "crash.sqlite"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def start() -> subprocess.Popen:
    env = {**os.environ, "TREX_DB": str(DB), "TREX_SIM_DELAY": "3", "PYTHONUTF8": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "trex.server:app", "--port", str(PORT), "--log-level", "warning"], cwd=ROOT, env=env, creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            httpx.get(f"{BASE}/api/state", timeout=5)
            return proc
        except httpx.HTTPError:
            time.sleep(1)
    raise SystemExit("server did not start")


def query(sql: str):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    proc = start()
    try:
        run = httpx.post(f"{BASE}/api/request", json={"text": "Route 4 ki gaadi kal jayegi? load kam lag raha hai, bhar do"}, timeout=30).json()["run"]
        deadline = time.time() + 240
        while time.time() < deadline and query("select count(*) from outbox")[0][0] < 2:
            time.sleep(0.5)
        sent_before = query("select count(*) from outbox")[0][0]
        turns_before = query("select count(*) from turns")[0][0]
        proc.kill()
        proc.wait()
        print(f"KILLED mid-run: {sent_before} messages sent, {turns_before} steps saved")
        time.sleep(2)
        proc = start()
        print("restarted")
        answered = set()
        tomorrow = False
        deadline = time.time() + 400
        status = ""
        while time.time() < deadline:
            s = httpx.get(f"{BASE}/api/state?run={run}", timeout=60).json()
            status = s["run"]["status"]
            for d in s.get("decisions", []):
                if d["answer"] is None and d["id"] not in answered:
                    httpx.post(f"{BASE}/api/decision/{d['id']}?run={run}", json={"answer": d["options"][0]}, timeout=30)
                    answered.add(d["id"])
            if status == "waiting" and s.get("sheet") and not tomorrow:
                httpx.post(f"{BASE}/api/tomorrow?run={run}", timeout=30)
                tomorrow = True
            if tomorrow and status == "waiting" and s["drops"] and s["reports"] and len(s["drops"]) >= len({r["retailer"] for r in s["sheet"]}):
                break
            time.sleep(2)
        resumes = query("select count(*) from events where kind='resumed_after_restart'")[0][0]
        outbox = [r[0] for r in query("select recipient || '|' || text from outbox")]
        repeated = [k for k, n in collections.Counter(outbox).items() if n > 1]
        orders = query("select count(*), count(distinct key) from test_orders")[0]
        print("resumed", resumes, "| status", status, "| messages", len(outbox), "| identical messages sent twice", len(repeated), "| orders", orders[0], "distinct", orders[1], "| drops", len(s["drops"]))
        ok = resumes >= 1 and not repeated and orders[0] == orders[1] and orders[0] > 0 and len(s["drops"]) > 0
        print("PASS" if ok else "FAIL")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
