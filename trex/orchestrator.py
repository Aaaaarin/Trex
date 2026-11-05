"""Keeps each run moving: wakes the agent on replies and resumes unfinished runs after a restart."""
import json
import threading
import time
import traceback

from . import channels, dispatch, store
from .agent import Agent, has_pending_work
from .config import DEMO


class Runner:
    def __init__(self, run: str, client=None):
        self.run = run
        self.client = client
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"agent-{run}")
        self.simulator = channels.Simulator(run, DEMO["plan_evening"], DEMO["plan_date"], on_reply=self.wake.set)

    def start(self) -> None:
        self.thread.start()
        self.simulator.start()
        _ensure_poller()

    def stop(self) -> None:
        self.stopped.set()
        self.wake.set()
        self.simulator.stopped.set()

    def _has_work(self) -> bool:
        if store.unhandled_inbox(self.run):
            return True
        seen = store.get_kv(self.run, "seen_decisions", [])
        answered = store.rows("select id from decisions where run=? and answer is not null", (self.run,))
        if any(d["id"] not in seen for d in answered):
            return True
        return has_pending_work(store.get_turns(self.run))

    def _loop(self) -> None:
        agent = None
        while not self.stopped.is_set():
            if self._has_work():
                # Let a burst of replies arrive together, so one turn handles them all.
                time.sleep(1.5)
                try:
                    agent = agent or Agent(self.run, self.client)
                    agent.advance()
                except Exception as err:  # keep the run alive and visible, whatever the failure
                    store.log(self.run, "agent_error", error=repr(err), trace=traceback.format_exc()[-800:])
                    store.set_status(self.run, "error")
                    self.wake.wait(5)
                    self.wake.clear()
                continue
            self.wake.wait(2)
            self.wake.clear()


RUNNERS: dict[str, Runner] = {}
_poller: channels.TelegramPoller | None = None


def _latest_run() -> str | None:
    found = store.rows("select id from runs order by created desc limit 1")
    return found[0]["id"] if found else None


def _wake(run: str) -> None:
    if run in RUNNERS:
        RUNNERS[run].wake.set()


def ensure_poller() -> None:
    _ensure_poller()


def _ensure_poller() -> None:
    global _poller
    if _poller is None:
        _poller = channels.TelegramPoller(_latest_run, on_reply=_wake)
        _poller.start()


def start_run(run: str, request: str, client=None) -> Runner:
    agent = Agent(run, client)
    agent.start(request)
    return ensure_runner(run, client)


def ensure_runner(run: str, client=None) -> Runner:
    if run not in RUNNERS:
        RUNNERS[run] = Runner(run, client)
        RUNNERS[run].start()
    return RUNNERS[run]


def resume_all() -> list[str]:
    """After a crash, pick up every run that was not finished."""
    runs = [r["id"] for r in store.rows("select id from runs where status!='closed'")]
    for run in runs:
        store.log(run, "resumed_after_restart")
        ensure_runner(run)
    return runs


def answer_decision(run: str, decision_id: str, answer: str, by: str) -> None:
    store.execute("update decisions set answer=?, answered_by=? where id=? and run=? and answer is null", (answer, by, decision_id, run))
    store.log(run, "decision_answered", id=decision_id, answer=answer, by=by)
    if run in RUNNERS:
        RUNNERS[run].wake.set()


def run_tomorrow(run: str, failed_share: int = 11) -> dict:
    """Simulate the delivery day. Live stops wait for a real photo from the builder's phone."""
    store.set_kv(run, "delivery_started", True)
    live = store.get_kv(run, "live", {})
    live_stops = set(live.get("recipients", []))
    decided = store.get_kv(run, "decided", {})
    sent = 0
    for truck in decided:
        plan = dispatch.current_plan(run, truck)
        if not plan or plan["status"] != "issued":
            continue  # held, merged or not yet issued: nothing is out for delivery
        sheet = store.get_kv(run, f"sheet:{truck}", [])
        stops = sorted({row["retailer"] for row in sheet})
        for i, retailer in enumerate(stops):
            if retailer in live_stops:
                channels.send(run, "driver", f"driver-{truck}", f"Drop at {retailer}: photo bhejo", f"{run}:drop-ask:{truck}:{retailer}", {"stop": retailer})
                continue
            failed = (i + 1) % failed_share == 0
            text = f"{retailer} dukan band thi, maal wapas" if failed else f"{retailer} delivered"
            store.add_inbox(run, f"driver-{truck}", "driver", text, True, f"drop:{truck}:{retailer}", {"photo": not failed, "retailer": retailer})
            sent += 1
    if run in RUNNERS:
        RUNNERS[run].wake.set()
    return {"simulated_drops": sent, "waiting_on_live": sorted(live_stops)}


def state_snapshot_json(run: str) -> str:
    return json.dumps({"run": run})
