"""Drivers, the dispatcher and the retailer desk. No model calls: time is passed in, and offers run on real plans."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trex import desk, dispatch, fleet, fleet_api, orchestrator, store
from trex.tools import Tools

EVENING, DAY = "2026-10-15", "2026-10-16"
_n = [0]


@pytest.fixture
def run():
    _n[0] += 1
    name = f"f{_n[0]}"
    store.create_run(name, "test")
    return name


def approve(run, kind, **scope):
    full = {"kind": kind, "truck": "", "retailer": "", "product": "", "pieces": 0, "detail": "", "basis": dispatch.basis_of(run, scope.get("truck") or None), **scope}
    did = f"{run}-{kind}-{len(store.rows('select id from decisions where run=?', (run,)))}"
    store.execute("insert into decisions(id, run, ts, question, options, answer, answered_by, evidence, scope) values(?,?,?,?,?,?,?,?,?)", (did, run, 0, "q", "[]", "Approve", "test", "[]", json.dumps(full)))


def issue(run, truck="V3"):
    tools = Tools(run, EVENING, DAY)
    approve(run, "under_target", truck=truck, pieces=tools.preview_plan(truck)["kg"])
    assert tools.freeze_plan(truck, "go", "test")["status"] == "frozen"
    tools.write_orders(truck)
    assert tools.issue_load_sheet(truck).get("stops")
    return tools


def sent(run, role=None):
    rows = store.rows("select role, recipient, text, channel from outbox where run=?", (run,))
    return [r for r in rows if role is None or r["role"] == role]


def test_issued_plan_opens_one_job_with_a_pay_offer_and_no_model_turn(run):
    issue(run)
    fleet.tick(run, 1000)
    fleet.tick(run, 1001)
    job = fleet.job_for(run, "V3")
    assert job["status"] == "open" and job["round"] == 1 and job["pay"] == fleet.base_pay("V3")
    offers = sent(run, "driver_pool")
    assert len(offers) == fleet.CONFIG["offer_width"] and "stand-in" in offers[0]["text"]
    assert not store.unhandled_inbox(run), "A driver offer must not wake the agent"


def test_first_yes_takes_the_job_and_a_later_yes_is_told_it_has_gone(run):
    issue(run)
    fleet.tick(run, 1000)
    first, second = list(fleet.job_for(run, "V3")["asked"])[:2]
    assert fleet.driver_reply(run, first, "haan le lunga", now=1001)["driver"] == first
    assert "error" in fleet.driver_reply(run, second, "haan", now=1002)
    job = fleet.job_for(run, "V3")
    assert job["driver"] == first and job["how"] == "accepted"
    assert any("aapki" in m["text"] and m["recipient"] == first for m in sent(run))
    assert not store.unhandled_inbox(run)


def test_unclear_reply_is_not_a_yes(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    assert fleet.driver_reply(run, driver, "dekhta hoon", now=1001)["answer"] == "unclear"
    assert fleet.job_for(run, "V3")["status"] == "open"


def test_pay_rises_within_the_ceiling_then_the_job_goes_to_someone_by_default(run):
    issue(run)
    fleet.CONFIG["route_reluctance"]["V3"] = 9.0  # nobody accepts this route at any allowed pay
    try:
        now = 1000
        fleet.tick(run, now)
        pays = [fleet.job_for(run, "V3")["pay"]]
        for _ in range(6):
            now += fleet.CONFIG["round_seconds"] + 1
            fleet.tick(run, now)
            pays.append(fleet.job_for(run, "V3")["pay"])
    finally:
        fleet.CONFIG["route_reluctance"]["V3"] = 1.6
    job = fleet.job_for(run, "V3")
    assert pays == sorted(pays) and pays[-1] > pays[0] and pays[-1] <= job["ceiling"] <= job["cap"]
    assert job["status"] == "assigned" and job["how"] == "default"
    assert any("Koi tayyar nahi tha" in m["text"] for m in sent(run))


def test_pay_cannot_pass_what_going_saves_over_holding(run):
    issue(run)
    store.set_kv(run, "options:V3", {"options": [{"name": "go", "net_cost_inr": 1000}, {"name": "hold", "net_cost_inr": 1100}]})
    limit = fleet.pay_ceiling(run, "V3", "go")
    assert limit["ceiling"] == fleet.base_pay("V3") + 100


def test_dispatcher_hears_who_comes_and_exactly_what_to_hand_over(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    fleet.driver_reply(run, driver, "haan", now=1001)
    note = sent(run, "dispatcher")[0]["text"]
    picks = fleet._pick_list(run, "V3")
    assert driver in note and "stand-in" in note and picks[0]["product_name"] in note and str(picks[0]["pieces"]) in note
    sheet = store.get_kv(run, "sheet:V3")
    assert sum(p["pieces"] for p in picks) == sum(r["pieces"] for r in sheet)


def test_gate_log_is_safe_when_repeated_or_out_of_order(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    fleet.driver_reply(run, driver, "haan", now=1001)
    report = fleet.job_for(run, "V3")["report_time"]
    assert not fleet.may_roll(run, "V3")
    assert fleet.log_gate(run, "V3", "out", clock="07:00")["status"] == "logged"
    assert "in" not in fleet.job_for(run, "V3")["gate"], "An in that never came stays not established"
    late = fleet._clock(fleet._minutes(report) + 30)
    assert fleet.log_gate(run, "V3", "in", clock=late)["gate"]["in"]["late_minutes"] == 30
    assert fleet.log_gate(run, "V3", "in", clock=report)["status"] == "already_logged"
    assert fleet.may_roll(run, "V3")


def test_holding_a_truck_cancels_its_job_and_tells_the_driver(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    fleet.driver_reply(run, driver, "haan", now=1001)
    store.set_kv(run, "truck_status:V3", "held")
    fleet.tick(run, 1002)
    assert fleet.job_for(run, "V3")["status"] == "cancelled"
    assert any("cancel" in m["text"] and m["recipient"] == driver for m in sent(run))


def test_no_show_reopens_at_top_pay_without_that_driver_and_tells_a_person(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    fleet.driver_reply(run, driver, "haan", now=1001)
    fleet.no_show(run, "V3", now=1002)
    job = fleet.job_for(run, "V3")
    assert job["status"] == "open" and driver in job["excluded"] and driver not in job["asked"] and job["pay"] == job["ceiling"]
    assert any(r["sender"] == "fleet" for r in store.unhandled_inbox(run))


def test_delivery_day_keeps_the_driver_label_that_proof_of_delivery_needs(run):
    tools = issue(run)
    orchestrator.run_tomorrow(run)
    job = fleet.job_for(run, "V3")
    assert job["status"] == "assigned" and "out" in job["gate"] and job["gate"]["out"]["simulated"]
    msg = next(r for r in store.unhandled_inbox(run) if r["role"] == "driver")
    assert msg["sender"] == "driver-V3"
    retailer = store.get_kv(run, "sheet:V3")[0]["retailer"]
    row = next(r for r in store.unhandled_inbox(run) if retailer in r["text"])
    eid = store.add_evidence(run, "message", f"inbox:{row['seq']}", "drop", {"sender": row["sender"], "role": row["role"], "text": row["text"], "retailer": retailer})
    assert tools.record_drop("V3", retailer, "delivered", eid)["closed"] == retailer


def test_delivery_day_finishes_the_offer_rounds_before_any_default(run):
    issue(run)
    fleet.CONFIG["route_reluctance"]["V3"] = 1.1  # needs one pay rise, then someone says yes
    try:
        fleet.tick(run, 1000, roll=False)
        orchestrator.run_tomorrow(run)
    finally:
        fleet.CONFIG["route_reluctance"]["V3"] = 1.6
    job = fleet.job_for(run, "V3")
    assert job["status"] == "assigned" and job["how"] == "accepted" and job["round"] >= 2
    assert job["pay"] > job["base_pay"]


def test_desk_answers_status_from_records_at_every_stage(run):
    retailer_gone = desk.handle(run, "R-999", "meri gaadi kahan hai", True, "a")
    assert retailer_gone["intent"] == "status" and retailer_gone["state"] == "not_on_truck" and "not established" in retailer_gone["answer"]
    issue(run)
    retailer = store.get_kv(run, "sheet:V3")[0]["retailer"]
    assert desk.handle(run, retailer, "gaadi kab aayegi", True, "b")["state"] == "planned"
    orchestrator.run_tomorrow(run)
    road = desk.handle(run, retailer, "kahan hai", True, "c")
    assert road["state"] == "on_road" and "estimate" in road["answer"] and len(road["evidence"]) == 2
    assert store.get_evidence(road["evidence"][1])["standin"] == 1


def test_desk_sends_orders_to_the_agent_without_promising_anything(run):
    result = desk.handle(run, "R-120", "20 kilo paneer chahiye kal", True, "o")
    assert result["intent"] == "order" and "pakka nahi" in result["answer"]
    row = store.unhandled_inbox(run)[0]
    assert row["role"] == "retailer" and row["sender"] == "R-120"


def test_desk_answers_a_repeat_once_and_calls_back_a_busy_shop_once(run):
    assert desk.handle(run, "R-120", "abhi busy hoon baad me", True, "x")["intent"] == "busy"
    assert desk.handle(run, "R-120", "abhi busy hoon baad me", True, "x")["status"] == "duplicate"
    due = store.get_kv(run, "callbacks")[0]["due"]
    desk.tick(run, due - 1)
    assert not [m for m in sent(run) if "Ab baat" in m["text"]]
    desk.tick(run, due + 1)
    desk.tick(run, due + 2)
    assert len([m for m in sent(run) if "Ab baat" in m["text"]]) == 1


def test_desk_uses_the_model_only_when_keywords_fail_and_survives_its_failure(run):
    class Usage:
        input_tokens, output_tokens = 5, 1

    class Answer:
        output_text, usage = "status.", Usage

    class Fake:
        class responses:
            create = staticmethod(lambda **kw: Answer)

    class Broken:
        class responses:
            create = staticmethod(lambda **kw: (_ for _ in ()).throw(AttributeError("boom")))

    assert desk.classify(run, "kahan hai", Broken)["by"] == "keywords"
    assert desk.classify(run, "bhaiya aaj kuch hua", Fake)["intent"] == "status"
    assert desk.classify(run, "bhaiya aaj kuch hua", Broken) == {"intent": "unclear", "by": "model failed"}


def test_fleet_screen_gets_an_empty_answer_before_any_run_exists(monkeypatch):
    monkeypatch.setattr(fleet_api, "_latest", lambda: None)
    app = FastAPI()
    app.include_router(fleet_api.router)
    response = TestClient(app).get("/api/fleet")
    assert response.status_code == 200
    body = response.json()
    assert body["run"] is None and body["jobs"] == [] and body["drivers"] == [] and body["desk"]["answers"] == []


def test_phone_commands_are_explicit_and_ordinary_replies_are_left_alone(run):
    issue(run)
    fleet.tick(run, 1000)
    driver = list(fleet.job_for(run, "V3")["asked"])[0]
    assert fleet.route_phone(run, "haan 20 bhej do", False, 1) is False
    assert fleet.route_phone(run, f"/driver {driver} haan", False, 2) is True
    assert fleet.job_for(run, "V3")["driver"] == driver
    assert fleet.route_phone(run, "/gate V3 in", True, 3) is True
    assert fleet.job_for(run, "V3")["gate"]["in"]["photo"] is True
    assert fleet.route_phone(run, "/gate V3 in", True, 3) is True
    before = store.get_kv(run, "last_live")
    assert fleet.route_phone(run, "/shop R-120 gaadi kahan hai", False, 4) is True
    assert store.get_kv(run, "last_live") == before
