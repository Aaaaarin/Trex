"""The properties judges will try to break: no unsourced numbers, no duplicates, no lost work."""
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


from trex import channels, gate, orders_api, store, upstream
from trex.agent import Agent
from trex.tools import Tools

EVENING, DAY = "2026-10-15", "2026-10-16"
_n = [0]


@pytest.fixture
def run():
    _n[0] += 1
    name = f"t{_n[0]}"
    store.create_run(name, "test")
    return name


def test_number_without_claim_is_blocked(run):
    with pytest.raises(gate.Blocked):
        gate.check(run, "Kal 12 peti aayegi", [])


def test_claim_must_match_its_evidence(run):
    eid = store.add_evidence(run, "plan", "x", "x", {"pieces": 8})
    with pytest.raises(gate.Blocked):
        gate.check(run, "Kal 12 peti", [{"value": 12, "evidence_ids": [eid]}])
    assert gate.check(run, "Kal 8 peti", [{"value": 8, "evidence_ids": [eid]}])


def test_invented_evidence_is_blocked(run):
    with pytest.raises(gate.Blocked):
        gate.check(run, "Kal 8 peti", [{"value": 8, "evidence_ids": ["nope"]}])


def test_identifiers_and_times_are_not_claims(run):
    assert gate.check(run, "R-204 ko V2 se 11 baje", [])


def test_standin_use_is_reported(run):
    eid = store.add_evidence(run, "standin", "x", "x", {"fill": 0.84}, standin=True)
    assert gate.check(run, "Load 84%", [{"value": 84, "evidence_ids": [eid]}])["used_standin"]


@pytest.mark.parametrize("fault,expected", [("500_after_write", "saved_after_error"), ("timeout_after_write", "saved_after_error"), ("malformed", "saved_after_error")])
def test_failure_after_write_creates_no_duplicate(run, fault, expected):
    orders_api.inject(run, fault)
    result = orders_api.write_safely(run, f"{run}:k1", {"a": 1})
    assert result["status"] == expected
    assert orders_api.count(run) == 1
    assert orders_api.write_safely(run, f"{run}:k1", {"a": 1})["status"] == "already_saved"
    assert orders_api.count(run) == 1


def test_failure_before_write_retries_once_saved(run):
    orders_api.inject(run, "500_before_write")
    assert orders_api.write_safely(run, f"{run}:k1", {"a": 1})["status"] == "saved"
    assert orders_api.count(run) == 1


def test_stale_stock_conflict_is_reported_not_retried(run):
    orders_api.inject(run, "409_stale_stock")
    assert orders_api.write_safely(run, f"{run}:k1", {"a": 1})["status"] == "conflict"
    assert orders_api.count(run) == 0


def test_a_message_is_sent_only_once(run):
    assert channels.send(run, "retailer", "R-1", "hello", "key1")["status"] == "sent"
    assert channels.send(run, "retailer", "R-1", "hello", "key1")["status"] == "already_sent"
    assert len(store.rows("select * from outbox where run=?", (run,))) == 1


def test_update_arriving_twice_counts_once(run):
    assert store.add_inbox(run, "R-1", "retailer", "haan", True, "d1")
    assert not store.add_inbox(run, "R-1", "retailer", "haan", True, "d1")
    assert len(store.unhandled_inbox(run)) == 1


def test_drop_needs_the_drivers_own_message(run):
    tools = Tools(run, EVENING, DAY)
    retailer_msg = store.add_evidence(run, "message", "m1", "m", {"sender": "R-1", "role": "retailer", "text": "aa gaya"})
    assert "error" in tools.record_drop("R-1", "delivered", retailer_msg)
    driver_msg = store.add_evidence(run, "message", "m2", "m", {"sender": "driver-V2", "role": "driver", "text": "done", "photo": True})
    assert tools.record_drop("R-1", "delivered", driver_msg)["status"] == "delivered"


def test_going_under_target_needs_approval(run):
    tools = Tools(run, EVENING, DAY)
    assert "error" in tools.finalize_truck("V2", "go", "test")


def test_accepting_more_than_offered_is_refused(run):
    tools = Tools(run, EVENING, DAY)
    channels.send(run, "retailer", "R-120", "offer", f"{run}:offer:R-120:P001", {"retailer": "R-120", "product": "P001", "pieces": 5, "group": "milk"})
    msg = store.add_evidence(run, "message", "m3", "m", {"sender": "R-120", "role": "retailer", "text": "9 bhej do"})
    assert "error" in tools.accept_offer("R-120", "P001", 9, msg)


def test_paper_cover_is_not_real_cover():
    c = upstream.cover("P006")
    assert c["paper_cover_days"] > c["real_cover_days"] > c["stock_at_branch_days"]
    assert upstream.trace("P006")["stopped_at"] == "open purchase order has no truck"


class _Block(SimpleNamespace):
    def model_dump(self, **_):
        return {k: v for k, v in vars(self).items()}


class FakeClient:
    """Scripted model replies, so that crash handling can be tested with no API calls."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw["messages"])
        blocks = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(content=blocks, stop_reason=stop, usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def _use(i, name, **inp):
    return _Block(type="tool_use", id=f"tu{i}", name=name, input=inp)


def test_crash_after_saved_turn_resumes_and_never_sends_twice(run):
    send = _use(1, "send_message", role="salesman", recipient="S01", text="Route check karo", claims=[])
    # A crash here: the model's turn is saved, its tool never ran.
    store.add_turn(run, "user", [{"type": "text", "text": "go"}])
    store.add_turn(run, "assistant", [send.model_dump()])
    client = FakeClient([[_Block(type="text", text="done")]])
    agent = Agent(run, client, EVENING, DAY)
    assert agent.advance() == "waiting"
    assert len(store.rows("select * from outbox where run=?", (run,))) == 1
    # A second restart repeats nothing.
    client2 = FakeClient([])
    assert Agent(run, client2, EVENING, DAY).advance() == "waiting"
    assert len(store.rows("select * from outbox where run=?", (run,))) == 1


def test_history_is_append_only(run):
    client = FakeClient([[_Block(type="text", text="a")], [_Block(type="text", text="b")]])
    agent = Agent(run, client, EVENING, DAY)
    store.add_turn(run, "user", [{"type": "text", "text": "first"}])
    agent.advance()
    before = json.dumps(store.get_turns(run))
    store.add_inbox(run, "R-1", "retailer", "haan", True, "d9")
    agent.advance()
    assert json.dumps(store.get_turns(run)).startswith(before[:-1])
