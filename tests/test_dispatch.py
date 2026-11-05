"""The dispatch safeguards. No model calls: these exercise the tools directly."""
import json

import pytest

from trex import dispatch, orders_api, store
from trex.tools import Tools

EVENING, DAY = "2026-10-15", "2026-10-16"
_n = [0]


@pytest.fixture
def run():
    _n[0] += 1
    name = f"d{_n[0]}"
    store.create_run(name, "test")
    return name


def tools_for(run) -> Tools:
    return Tools(run, EVENING, DAY)


def approve(run, kind, **scope):
    full = {"kind": kind, "truck": "", "retailer": "", "product": "", "pieces": 0, "detail": "", "basis": dispatch.basis_of(run, scope.get("truck") or None), **scope}
    did = f"{run}-{kind}-{_n[0]}-{len(store.rows('select id from decisions where run=?', (run,)))}"
    store.execute("insert into decisions(id, run, ts, question, options, answer, answered_by, evidence, scope) values(?,?,?,?,?,?,?,?,?)", (did, run, 0, "q", "[]", "Approve", "test", "[]", json.dumps(full)))
    return did


def frozen_v3(run):
    """V3 is far under target, so it needs an approval to dispatch."""
    tools = tools_for(run)
    approve(run, "under_target", truck="V3", pieces=tools.preview_plan("V3")["kg"])
    result = tools.freeze_plan("V3", "go", "test")
    assert result["status"] == "frozen", result
    return tools, result


def test_under_target_is_refused_without_approval(run):
    result = tools_for(run).freeze_plan("V3", "go", "test")
    assert "error" in result
    assert any("under the" in p for p in result["problems"])
    assert dispatch.current_plan(run, "V3") is None


def test_approval_for_another_truck_does_not_count(run):
    approve(run, "under_target", truck="V2", pieces=tools_for(run).preview_plan("V3")["kg"])
    assert "error" in tools_for(run).freeze_plan("V3", "go", "test")


def test_approval_for_a_different_load_does_not_count(run):
    tools = tools_for(run)
    approve(run, "under_target", truck="V3", pieces=tools.preview_plan("V3")["kg"] + 50)
    assert "error" in tools.freeze_plan("V3", "go", "test")


def test_preview_freezes_nothing_and_matches_the_plan(run):
    tools = tools_for(run)
    preview = tools.preview_plan("V3")
    assert dispatch.current_plan(run, "V3") is None
    approve(run, "under_target", truck="V3", pieces=preview["kg"])
    assert round(tools.freeze_plan("V3", "go", "test")["kg"]) == preview["kg"]


def test_approval_stops_counting_when_the_plan_changes(run):
    approve(run, "under_target", truck="V3", pieces=tools_for(run).preview_plan("V3")["kg"])
    store.set_kv(run, "extras", [{"truck": "V3", "route": "R13", "retailer": "R-901", "retailer_type": "horeca", "product": "P001", "pieces": 1, "source": "request", "evidence": "x"}])
    assert "error" in tools_for(run).freeze_plan("V3", "go", "test")


def test_going_under_target_works_with_exact_approval(run):
    _, result = frozen_v3(run)
    assert result["version"] == 1 and result["fill"] < 0.8


def test_later_tools_read_the_same_frozen_plan(run):
    tools, result = frozen_v3(run)
    written = tools.write_orders("V3")
    plan = dispatch.current_plan(run, "V3")
    assert written["version"] == result["version"]
    rows = store.rows("select payload from test_orders where run=?", (run,))
    assert rows and all(json.loads(r["payload"])["plan_hash"] == plan["hash"] for r in rows)
    assert len(rows) == len(plan["lines"])
    issued = tools.issue_load_sheet("V3")
    assert issued["version"] == result["version"]
    sheet = store.get_kv(run, "sheet:V3")
    assert {(s["retailer"], s["product"], s["pieces"]) for s in sheet} == {(ln["retailer"], ln["product"], ln["pieces"]) for ln in plan["lines"]}


def test_a_tampered_plan_is_refused(run):
    tools, _ = frozen_v3(run)
    row = store.rows("select data from plans where run=?", (run,))[0]
    data = json.loads(row["data"])
    data["lines"][0]["pieces"] += 1000
    store.execute("update plans set data=? where run=?", (json.dumps(data, sort_keys=True), run))
    result = tools.write_orders("V3")
    assert "hash" in result["error"]
    assert orders_api.count(run) == 0


def test_issuing_needs_saved_orders_first(run):
    tools, _ = frozen_v3(run)
    assert "error" in tools.issue_load_sheet("V3")


def test_failed_write_blocks_only_that_promise(run):
    tools, _ = frozen_v3(run)
    orders_api.inject(run, "409_stale_stock", 1)
    written = tools.write_orders("V3")
    assert written["counts"].get("conflict") == 1 and len(written["blocked_lines"]) == 1
    issued = tools.issue_load_sheet("V3")
    assert len(issued["blocked_lines"]) == 1
    plan = dispatch.current_plan(run, "V3")
    assert issued["lines"] == len(plan["lines"]) - 1
    blocked = issued["blocked_lines"][0]
    assert not any(s["retailer"] == blocked[0] and s["product"] == blocked[1] for s in store.get_kv(run, "sheet:V3"))


def test_retry_after_failed_write_saves_only_the_missing_line(run):
    tools, _ = frozen_v3(run)
    orders_api.inject(run, "409_stale_stock", 1)
    tools.write_orders("V3")
    again = tools.write_orders("V3")
    assert again["blocked_lines"] == []
    assert orders_api.count(run) == len(dispatch.current_plan(run, "V3")["lines"])


def test_late_yes_after_commit_is_a_proposal_not_a_change(run):
    tools, _ = frozen_v3(run)
    tools.write_orders("V3")
    before = dispatch.current_plan(run, "V3")["hash"]
    extras_before = store.get_kv(run, "extras", [])
    result = tools._add_extra("R-901", "P001", 12, "offer", "x", "R13")
    assert result["status"] == "proposed_revision" and result["applied"] is False
    assert dispatch.current_plan(run, "V3")["hash"] == before
    assert store.get_kv(run, "extras", []) == extras_before
    assert len(store.get_kv(run, "revisions:V3")) == 1


def test_change_before_commit_replaces_the_frozen_version(run):
    tools, result = frozen_v3(run)
    tools._add_extra("R-901", "P001", 12, "offer", "x", "R13")
    assert dispatch.current_plan(run, "V3") is None
    versions = store.rows("select status from plans where run=? and truck='V3'", (run,))
    assert versions[0]["status"] == "superseded"


def test_hold_needs_approval_and_stops_dispatch(run):
    tools = tools_for(run)
    assert "error" in tools.hold_truck("V3", "test")
    approve(run, "hold", truck="V3")
    assert tools.hold_truck("V3", "test")["status"] == "held"
    assert "error" in tools.freeze_plan("V3", "go", "test")
    assert "error" in tools.write_orders("V3")
    assert "error" in tools.issue_load_sheet("V3")
    assert orders_api.count(run) == 0
    assert "error" in tools._add_extra("R-901", "P001", 12, "offer", "x", "R13")


def test_held_truck_is_not_sent_out_on_the_delivery_day(run):
    from trex import orchestrator

    tools = tools_for(run)
    approve(run, "hold", truck="V3")
    tools.hold_truck("V3", "test")
    assert orchestrator.run_tomorrow(run)["simulated_drops"] == 0


def test_merge_needs_approval_naming_the_host(run):
    tools = tools_for(run)
    approve(run, "merge", truck="V3", detail="V9")
    assert "error" in tools.merge_truck("V3", "V2", "test")
    approve(run, "merge", truck="V3", detail="V2")
    result = tools.merge_truck("V3", "V2", "test")
    if "error" in result:
        assert "cannot carry" in result["error"]
        return
    assert result["status"] == "merged"
    assert "V3" not in {t["truck"] for t in tools.night().truck_summary()}
    assert "error" in tools.freeze_plan("V3", "go", "test")


def test_merge_that_does_not_fit_is_refused(run):
    tools = tools_for(run)
    approve(run, "merge", truck="V1", detail="V3")
    assert "cannot carry" in tools.merge_truck("V1", "V3", "test")["error"]


def test_offers_reserve_stock_so_rounds_cannot_compete(run):
    tools = tools_for(run)
    tools.plan_offers("V2")
    offer = next(iter(store.get_kv(run, "offers:V2").values()))
    free_before = tools.night().free_stock()[offer["product"]]
    tools.send_offer(offer["retailer"], offer["product"], offer["pieces"], offer["discount_inr_per_piece"], "Kal bhej dein?", [])
    assert tools.night().free_stock()[offer["product"]] == free_before - offer["pieces"]


def test_offer_reservation_is_released_on_no(run):
    tools = tools_for(run)
    tools.plan_offers("V2")
    offer = next(iter(store.get_kv(run, "offers:V2").values()))
    free_before = tools.night().free_stock()[offer["product"]]
    tools.send_offer(offer["retailer"], offer["product"], offer["pieces"], 0, "Kal bhej dein?", [])
    tools.release_offer(offer["retailer"], offer["product"], "said no", "x")
    assert tools.night().free_stock()[offer["product"]] == free_before


def test_offer_over_the_allowed_discount_is_refused(run):
    tools = tools_for(run)
    tools.plan_offers("V2")
    offer = next(o for o in store.get_kv(run, "offers:V2").values())
    result = tools.send_offer(offer["retailer"], offer["product"], offer["pieces"], offer["discount_inr_per_piece"] + 100, "Kal bhej dein?", [])
    assert "discount" in result["error"]


def test_unplanned_offer_is_refused(run):
    assert "plan_offers" in tools_for(run).send_offer("R-101", "P001", 5, 0, "Kal bhej dein?", [])["error"]


def test_taking_promised_stock_needs_approval_for_that_exact_quantity(run):
    tools = tools_for(run)
    msg = store.add_evidence(run, "message", "m", "m", {"sender": "lead", "role": "lead", "text": "x"})
    first = tools.add_request("R-120", "P006", 500, msg)
    assert first["needs_decision"]
    approve(run, "take_promised_stock", retailer="R-120", product="P006", pieces=400)
    assert tools.add_request("R-120", "P006", 500, msg)["needs_decision"]
    approve(run, "take_promised_stock", retailer="R-120", product="P006", pieces=500)
    assert "fill" in tools.add_request("R-120", "P006", 500, msg)


def test_validator_refuses_stock_over_the_record(run):
    tools = tools_for(run)
    plan = dispatch.build(run, tools.night(), "V3", "go", "t")
    plan["lines"][0]["pieces"] = 10_000_000
    plan["lines"][0]["batches"][0]["pieces"] = 10_000_000
    problems = dispatch.validate(run, plan, tools.night())
    assert any("stock" in p for p in problems)
    assert any("capacity" in p or "requested" in p for p in problems)


def test_validator_refuses_unassigned_batches(run):
    tools = tools_for(run)
    plan = dispatch.build(run, tools.night(), "V3", "go", "t")
    plan["lines"][0]["batches"] = []
    assert any("batches do not add up" in p for p in dispatch.validate(run, plan, tools.night()))


def test_validator_refuses_an_offer_with_no_reply(run):
    tools = tools_for(run)
    plan = dispatch.build(run, tools.night(), "V3", "go", "t")
    plan["lines"][0]["source"] = "offer"
    plan["lines"][0]["evidence"] = "nothing"
    assert any("no reply" in p for p in dispatch.validate(run, plan, tools.night()))


def test_drop_rules_on_an_issued_plan(run):
    tools, _ = frozen_v3(run)
    tools.write_orders("V3")
    tools.issue_load_sheet("V3")
    stop = store.get_kv(run, "sheet:V3")[0]["retailer"]
    wrong_sender = store.add_evidence(run, "message", "a", "m", {"sender": "driver-V2", "role": "driver", "text": f"{stop} delivered"})
    assert "error" in tools.record_drop("V3", stop, "delivered", wrong_sender)
    wrong_stop = store.add_evidence(run, "message", "b", "m", {"sender": "driver-V3", "role": "driver", "text": "R-000 delivered", "retailer": "R-000"})
    assert "error" in tools.record_drop("V3", stop, "delivered", wrong_stop)
    not_on_plan = store.add_evidence(run, "message", "c", "m", {"sender": "driver-V3", "role": "driver", "text": "R-000 delivered", "retailer": "R-000"})
    assert "error" in tools.record_drop("V3", "R-000", "delivered", not_on_plan)
    good = store.add_evidence(run, "message", "d", "m", {"sender": "driver-V3", "role": "driver", "text": f"{stop} delivered", "retailer": stop, "photo": True})
    closed = tools.record_drop("V3", stop, "delivered", good)
    assert closed["status"] == "delivered"
    assert store.get_kv(run, "drops")[stop]["plan_version"] == 1


def test_drop_before_issue_is_refused(run):
    tools, _ = frozen_v3(run)
    msg = store.add_evidence(run, "message", "e", "m", {"sender": "driver-V3", "role": "driver", "text": "R-1 delivered", "retailer": "R-1"})
    assert "no issued plan" in tools.record_drop("V3", "R-1", "delivered", msg)["error"]


def test_going_under_target_can_be_the_cheapest_priced_option(run):
    priced = tools_for(run).price_options("V3")
    names = {o["name"] for o in priced["options"]}
    assert priced["target_reachable_by_offers"] is False
    assert "sell_to_fill" not in names and {"go", "hold"} <= names


def test_selling_is_priced_on_expected_yes_when_it_can_work(run):
    priced = tools_for(run).price_options("V2")
    sell = next(o for o in priced["options"] if o["name"] == "sell_to_fill")
    assert "expect about" in sell["note"]


def test_no_selling_when_target_is_out_of_reach(run):
    result = tools_for(run).plan_offers("V3")
    assert result["target_reachable"] is False and result["offers"] == []


def test_questions_per_truck_are_capped(run):
    from trex.config import standin

    tools = tools_for(run)
    tools.plan_offers("V2")
    assert len(store.get_kv(run, "offers:V2")) <= standin("max_questions_per_truck")


def test_every_offer_has_a_batch_that_retailer_can_take(run):
    tools = tools_for(run)
    night = tools.night()
    shares = __import__("trex.config", fromlist=["standin"]).standin("min_life_share")
    for o in night.offers("V2", limit=20):
        rows = night.stock_table[(night.stock_table["product_id"] == o["product"]) & (night.stock_table["share_left"] >= shares[o["retailer_type"]])]
        assert rows["pieces"].sum() >= o["pieces"], o


def test_offer_window_closes_and_wakes_the_agent(run):
    from trex import orchestrator

    runner = orchestrator.Runner(run)
    store.execute("insert into reservations(key, run, truck, retailer, product, pieces, status, ts, discount) values(?,?,?,?,?,?,?,?,?)", (f"{run}:k", run, "V2", "R-1", "P001", 5, "open", 0, 0))
    store.set_status(run, "waiting")
    runner._close_offer_window()
    assert store.rows("select status from reservations where key=?", (f"{run}:k",))[0]["status"] == "expired"
    assert any("Offer window closed" in r["text"] for r in store.unhandled_inbox(run))


def test_change_on_another_truck_does_not_cancel_an_approval(run):
    tools, _ = frozen_v3(run)
    store.set_kv(run, "extras", [{"truck": "V2", "route": "R04", "retailer": "R-120", "retailer_type": "horeca", "product": "P001", "pieces": 1, "source": "request", "evidence": "x", "needs_approval": False}])
    assert tools.write_orders("V3").get("version") == 1


def test_batch_code_shared_by_two_products_stays_separate(run):
    tools = tools_for(run)
    night = tools.night()
    counts = night.stock_table.groupby("batch_id")["product_id"].nunique()
    assert (counts > 1).any(), "the real data has repeated batch codes, and this test depends on that"
    plan = dispatch.build(run, night, "V2", "go", "t")
    problems = [p for p in dispatch.validate(run, plan, night) if "Batch" in p]
    assert problems == []


def test_merge_is_priced_with_the_space_it_saves_on_the_host(run):
    priced = tools_for(run).price_options("V3")
    options = {o["name"]: o for o in priced["options"]}
    assert options["merge"]["with_truck"] == "V1"
    assert options["merge"]["truck_cost_inr"] < 0, "the host's empty space saved is larger than the extra cost"
    assert priced["recommended"] == "merge"


def test_merge_chains_are_refused(run):
    tools = tools_for(run)
    approve(run, "merge", truck="V3", detail="V1")
    assert tools.merge_truck("V3", "V1", "test")["status"] == "merged"
    approve(run, "merge", truck="V1", detail="V2")
    assert "carrying" in tools.merge_truck("V1", "V2", "test")["error"]


def test_merge_into_a_held_truck_is_refused(run):
    tools = tools_for(run)
    approve(run, "hold", truck="V1")
    tools.hold_truck("V1", "test")
    approve(run, "merge", truck="V3", detail="V1")
    assert "already held" in tools.merge_truck("V3", "V1", "test")["error"]


def test_host_of_a_merge_cannot_be_held(run):
    tools = tools_for(run)
    approve(run, "merge", truck="V3", detail="V1")
    tools.merge_truck("V3", "V1", "test")
    approve(run, "hold", truck="V1")
    assert "strand" in tools.hold_truck("V1", "test")["error"]


def test_truck_with_no_stops_or_already_decided_gets_a_clear_answer(run):
    tools = tools_for(run)
    assert "no stops" in tools.price_options("V6")["error"]
    approve(run, "merge", truck="V3", detail="V1")
    tools.merge_truck("V3", "V1", "test")
    assert "already merged" in tools.price_options("V3")["error"]


def test_merged_stops_are_told_and_ship_on_the_host(run):
    tools = tools_for(run)
    moved = {ln.retailer for ln in tools.night().lines if ln.truck == "V3"}
    approve(run, "merge", truck="V3", detail="V1")
    assert tools.merge_truck("V3", "V1", "test")["retailers_told"] == len(moved)
    plan = dispatch.build(run, tools.night(), "V1", "go", "t")
    assert moved & {ln["retailer"] for ln in plan["lines"]}


def test_screen_follows_a_merged_truck_to_its_host(run):
    tools = tools_for(run)
    tools.price_options("V3")
    approve(run, "merge", truck="V3", detail="V1")
    tools.merge_truck("V3", "V1", "test")
    assert store.get_kv(run, "focus") == "V1"


def test_two_failed_drops_on_one_route_are_both_kept(run):
    tools = tools_for(run)
    tools.log_miss("R12", "unknown", "delivery failed: R-473 shop closed", "retailer", [])
    tools.log_miss("R12", "unknown", "delivery failed: R-595 shop closed", "retailer", [])
    tools.log_miss("R12", "unknown", "delivery failed: R-595 shop closed", "retailer", [])
    assert len(store.get_kv(run, "misses")) == 2


def _shop_with_no_truck(tools):
    served = {ln.route for ln in tools.night().lines}
    d = tools.ledger.lines()
    routes = d.groupby("retailer_id")["route_id"].agg(set)
    return next(r for r, s in routes.items() if not s & served)


def test_shop_with_no_truck_is_asked_about_and_nothing_is_promised(run):
    tools = tools_for(run)
    shop = _shop_with_no_truck(tools)
    result = tools.add_request(shop, "P001", 12, tools._standin_evidence("t", "t", {}))
    assert result["needs_decision"] and result["scope"]["kind"] == "add_stop" and result["trucks_with_room"]
    assert result["evidence_id"] and store.get_kv(run, "extras", []) == []


def test_approved_stop_rides_on_the_named_truck_only(run):
    tools = tools_for(run)
    shop = _shop_with_no_truck(tools)
    evidence = tools._standin_evidence("t", "t", {})
    approve(run, "add_stop", truck="V3", retailer=shop, product="P001", pieces=12)
    assert tools.add_request(shop, "P001", 24, evidence)["needs_decision"], "approval covers the exact quantity only"
    result = tools.add_request(shop, "P001", 12, evidence)
    assert result["truck"] == "V3"
    assert shop in {ln.retailer for ln in tools.night().lines if ln.truck == "V3"}


def test_approved_stop_cannot_ride_on_a_held_truck(run):
    tools = tools_for(run)
    shop = _shop_with_no_truck(tools)
    approve(run, "hold", truck="V3")
    tools.hold_truck("V3", "test")
    approve(run, "add_stop", truck="V3", retailer=shop, product="P001", pieces=12)
    assert "already held" in tools.add_request(shop, "P001", 12, tools._standin_evidence("t", "t", {}))["error"]


def _fake_search(calls, status="ok"):
    def search(lane, vehicle_type, limit=5):
        calls.append((lane, vehicle_type))
        rows = [{"name": "Test Carriers", "website": None, "location": "Kanpur", "size": None, "why_matched": "test", "source_url": None}] if status == "ok" else []
        return {"lane": lane, "vehicle_type": vehicle_type, "transporters": rows, "count": len(rows), "status": status, "note": "test"}
    return search


def test_transporter_search_sends_only_the_test_lane_and_runs_once(run, monkeypatch):
    from trex import crustdata
    from trex.config import standin

    calls = []
    monkeypatch.setattr(crustdata, "find_transporters", _fake_search(calls))
    tools = tools_for(run)
    first = tools.find_transporters("P006")
    second = tools.find_transporters("P006")
    assert calls == [(standin("transporter_lane"), "refrigerated")]
    assert first["contacted"] == 0 and second["transporters"] == first["transporters"]
    assert store.get_evidence(first["evidence_id"])


def test_failed_transporter_search_is_not_remembered(run, monkeypatch):
    from trex import crustdata

    calls = []
    monkeypatch.setattr(crustdata, "find_transporters", _fake_search(calls, "error"))
    tools = tools_for(run)
    assert tools.find_transporters("P006")["status"] == "error"
    assert store.get_kv(run, "transporters", {}) == {}


def test_no_transporter_search_once_a_truck_is_matched(run, monkeypatch):
    from trex import crustdata, upstream

    monkeypatch.setattr(upstream, "trace", lambda product: {"vehicle_matched": True, "po_id": "PO-1"})
    monkeypatch.setattr(crustdata, "find_transporters", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no search")))
    assert "error" in tools_for(run).find_transporters("P006")


def test_one_approved_stop_does_not_open_its_route(run):
    tools = tools_for(run)
    shop = _shop_with_no_truck(tools)
    evidence = tools._standin_evidence("t", "t", {})
    approve(run, "add_stop", truck="V3", retailer=shop, product="P001", pieces=12)
    assert tools.add_request(shop, "P001", 12, evidence)["truck"] == "V3"
    assert tools.add_request(shop, "P009", 6, evidence)["needs_decision"]
    d = tools.ledger.lines()
    own = d[d["retailer_id"] == shop]["route_id"].iloc[0]
    other = next(r for r in d[d["route_id"] == own]["retailer_id"].unique() if r != shop and not (set(d[d["retailer_id"] == r]["route_id"]) & tools._served_routes()))
    assert tools.add_request(other, "P001", 12, evidence)["needs_decision"]


def test_extra_stop_question_must_name_a_truck_and_shows_what_a_yes_covers(run):
    tools = tools_for(run)
    assert "error" in tools.request_decision("add_stop", "", "R-1", "P001", 12, "", "Add it?", ["Approve", "Reject"], [])
    tools.request_decision("add_stop", "V3", "R-1", "P001", 12, "", "Add it?", ["Approve", "Reject"], [])
    question = store.rows("select question from decisions where run=?", (run,))[0]["question"]
    assert "V3" in question and "R-1" in question and "12 pieces" in question


def test_shop_message_promises_no_next_truck_and_lists_the_request_first(run):
    tools = tools_for(run)
    evidence = tools._standin_evidence("t", "t", {})
    tools.add_request("R-120", "P006", 12, evidence)
    approve(run, "under_target", truck="V2", pieces=tools.preview_plan("V2")["kg"])
    tools.freeze_plan("V2", "go", "test")
    tools.write_orders("V2")
    tools.issue_load_sheet("V2")
    texts = [r["text"] for r in store.rows("select text from outbox where run=? and recipient like 'R-%'", (run,))]
    assert texts and not any("agli gaadi" in t for t in texts)
