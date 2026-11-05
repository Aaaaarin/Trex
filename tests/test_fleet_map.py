"""The schematic fleet map. No model calls: state is built through the store and fleet functions."""
import json
import random

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trex import dispatch, fleet, fleet_map, store
from trex.tools import Tools

EVENING, DAY = "2026-10-15", "2026-10-16"
_n = [0]


@pytest.fixture
def run():
    _n[0] += 1
    name = f"map{_n[0]}"
    store.create_run(name, "test")
    return name


def issue(run, truck="V3"):
    tools = Tools(run, EVENING, DAY)
    full = {"kind": "under_target", "truck": truck, "retailer": "", "product": "", "pieces": tools.preview_plan(truck)["kg"], "detail": "", "basis": dispatch.basis_of(run, truck)}
    store.execute("insert into decisions(id, run, ts, question, options, answer, answered_by, evidence, scope) values(?,?,?,?,?,?,?,?,?)", (f"{run}-{truck}", run, 0, "q", "[]", "Approve", "test", "[]", json.dumps(full)))
    assert tools.freeze_plan(truck, "go", "test")["status"] == "frozen"
    tools.write_orders(truck)
    assert tools.issue_load_sheet(truck).get("stops")
    return tools


def close(tools, run, truck, retailer, status="delivered"):
    eid = store.add_evidence(run, "message", f"m-{truck}-{retailer}", "drop", {"sender": f"driver-{truck}", "role": "driver", "text": f"{retailer} {status}", "retailer": retailer, "simulated": True})
    assert tools.record_drop(truck, retailer, status, eid)["closed"] == retailer


def job(**kw):
    return {"truck": "V1", "status": "assigned", "driver": "D03", "how": "accepted", "report_time": "06:00", "gate": {}, "excluded": [], "round": 1, "asked": {}, "replies": [], **kw}


OUT = {"in": {"clock": "05:50"}, "out": {"clock": "06:15"}}


def test_layout_does_not_depend_on_input_order():
    routes = {f"R{i:02d}": [f"S{i}{j}" for j in range(12)] for i in range(10)}
    shuffled = dict(random.Random(4).sample(list(routes.items()), len(routes)))
    assert fleet_map.route_layout(routes) == fleet_map.route_layout(shuffled)


def test_every_route_has_its_own_angle_and_every_shop_its_own_place():
    layout = fleet_map.route_layout({f"R{i}": [f"S{j}" for j in range(20)] for i in range(26)})
    assert len({v["angle"] for v in layout.values()}) == 26
    for v in layout.values():
        places = [(s["rf"], s["lane"]) for s in v["slots"].values()]
        assert len(set(places)) == 20 and all(0 < rf < 1 for rf, _ in places)


def test_truck_stays_at_depot_until_the_gate_log_says_out():
    t = fleet_map.truck_view("V1", 100, True, 0, 0, 10, "issued", job())
    assert t["phase"] == "depot" and t["tone"] == "good"
    # A gate in is not a departure, and a saved drop cannot move a truck the dispatcher has not logged out.
    t = fleet_map.truck_view("V1", 100, True, 2, 0, 10, "issued", job(gate={"in": OUT["in"]}))
    assert t["phase"] == "depot"


def test_truck_moves_with_closed_stops_and_finishes_when_all_close():
    t = fleet_map.truck_view("V1", 100, True, 3, 0, 10, "issued", job(gate=OUT))
    assert t["phase"] == "out" and t["closed"] == 3 and t["tone"] == "good"
    assert fleet_map.truck_view("V1", 100, True, 10, 0, 10, "issued", job(gate=OUT))["phase"] == "done"


@pytest.mark.parametrize(
    "args, tone",
    [
        ((True, 0, 0, 10, "issued", job()), "good"),
        ((False, 0, 0, 10, "issued", job()), "warn"),
        ((True, 0, 0, 10, "issued", job(status="open", driver=None)), "warn"),
        ((True, 0, 0, 10, "issued", None), "warn"),
        ((True, 0, 0, 10, "issued", job(status="no_driver", driver=None)), "bad"),
        ((True, 0, 0, 10, "issued", job(status="open", driver=None, excluded=["D03"])), "bad"),
        ((True, 4, 1, 10, "issued", job(gate=OUT)), "bad"),
    ],
)
def test_truck_colour_rules(args, tone):
    assert fleet_map.truck_view("V1", 100, *args)["tone"] == tone


def test_cancelled_job_is_no_job_and_held_truck_never_leaves():
    assert fleet_map.truck_view("V1", 100, True, 0, 0, 10, "issued", job(status="cancelled"))["driver"] is None
    held = fleet_map.truck_view("V1", 100, True, 0, 0, 10, "issued", job(gate=OUT), held=True)
    assert held["phase"] == "depot" and held["tone"] == "mu"


def test_stop_state_only_closes_on_a_drop_for_this_truck():
    assert fleet_map.stop_state(None, "V1", False) == "planned"
    assert fleet_map.stop_state(None, "V1", True) == "out"
    assert fleet_map.stop_state({"truck": "V2", "status": "failed"}, "V1", True) == "out"
    assert fleet_map.stop_state({"truck": "V1", "status": "failed"}, "V1", True) == "failed"


def test_driver_board_status():
    roster = [{"id": d, "km_to_depot": 1, "max_kg": 1} for d in ("D1", "D2", "D3", "D4", "D5")]
    jobs = [
        job(truck="V1", driver="D1", excluded=["D2"]),
        job(truck="V2", status="open", driver=None, round=2, asked={"D3": 2, "D4": 2}, replies=[{"driver": "D4", "round": 2, "answer": "no"}]),
        job(truck="V3", status="cancelled", driver="D5"),
    ]
    board = {d["id"]: d for d in fleet_map.driver_board(roster, jobs, rating=lambda _: 4.0)}
    assert (board["D1"]["tone"], board["D1"]["status"], board["D1"]["truck"]) == ("good", "On job", "V1")
    assert (board["D2"]["tone"], board["D2"]["status"]) == ("bad", "No-show")
    assert (board["D3"]["tone"], board["D3"]["status"]) == ("warn", "Offered")
    assert (board["D4"]["tone"], board["D4"]["status"]) == ("bad", "Rejected")
    assert (board["D5"]["tone"], board["D5"]["status"]) == ("good", "Available")


def test_summary_counts_the_rows_it_is_given():
    base = {"phase": "depot", "held": False, "waiting": False, "no_driver": False}
    trucks = [{**base, "phase": "out"}, {**base, "phase": "out"}, {**base, "waiting": True}]
    stops = [{"state": "failed"}] * 4 + [{"state": "out"}]
    assert fleet_map.summary_line(trucks, stops, True) == "2 trucks out, 1 waiting for a driver, 4 stops failed"


def test_before_any_run_everything_is_at_the_depot_and_not_yet():
    snap = fleet_map.snapshot(None)
    assert snap["trucks"] and snap["stops"] and {d["id"] for d in snap["drivers"]} == {f"D0{i}" for i in range(1, 9)}
    assert all(t["phase"] == "depot" and t["closed"] == 0 and t["at"] is None for t in snap["trucks"])
    assert {s["state"] for s in snap["stops"]} == {"planned"}
    assert snap["schematic"] == "Schematic. Positions are invented, there is no GPS."
    assert "—" not in json.dumps(snap, ensure_ascii=False)
    assert fleet_map.snapshot(None)["stops"] == snap["stops"], "The layout must not move between polls"


def test_a_real_run_moves_the_marker_only_as_drops_close(run):
    tools = issue(run)
    fleet.tick(run, 1000)
    first = list(fleet.job_for(run, "V3")["asked"])[0]
    snap = fleet_map.snapshot(run)
    v3 = next(t for t in snap["trucks"] if t["truck"] == "V3")
    assert v3["waiting"] and v3["tone"] == "warn" and v3["phase"] == "depot"
    assert next(d for d in snap["drivers"] if d["id"] == first)["status"] == "Offered"

    fleet.driver_reply(run, first, "haan", now=1001)
    fleet.log_gate(run, "V3", "in", clock="05:50")
    stops = [s for s in fleet_map.snapshot(run)["stops"] if s["truck"] == "V3"]
    assert all(s["state"] == "planned" for s in stops)
    before = {(s["key"], s["angle"], s["rf"], s["lane"]) for s in stops}

    fleet.log_gate(run, "V3", "out", clock="06:15")
    snap = fleet_map.snapshot(run)
    v3 = next(t for t in snap["trucks"] if t["truck"] == "V3")
    assert v3["phase"] == "out" and v3["at"] is None and v3["closed"] == 0 and v3["driver"] == first
    assert {s["state"] for s in snap["stops"] if s["truck"] == "V3"} == {"out"}

    close(tools, run, "V3", stops[0]["retailer"])
    close(tools, run, "V3", stops[1]["retailer"], "failed")
    snap = fleet_map.snapshot(run)
    v3 = next(t for t in snap["trucks"] if t["truck"] == "V3")
    states = {s["retailer"]: s["state"] for s in snap["stops"] if s["truck"] == "V3"}
    assert (v3["delivered"], v3["failed"], v3["tone"]) == (1, 1, "bad") and v3["at"]
    assert states[stops[0]["retailer"]] == "delivered" and states[stops[1]["retailer"]] == "failed"
    assert {(s["key"], s["angle"], s["rf"], s["lane"]) for s in snap["stops"] if s["truck"] == "V3"} == before
    assert "1 stop failed" in snap["summary"] and "1 truck out" in snap["summary"]


def test_no_show_turns_driver_and_truck_red(run):
    issue(run)
    fleet.tick(run, 1000)
    first = list(fleet.job_for(run, "V3")["asked"])[0]
    fleet.driver_reply(run, first, "haan", now=1001)
    fleet.no_show(run, "V3", now=1002)
    snap = fleet_map.snapshot(run)
    assert next(t for t in snap["trucks"] if t["truck"] == "V3")["tone"] == "bad"
    assert next(d for d in snap["drivers"] if d["id"] == first)["status"] == "No-show"


def test_endpoint_answers_before_and_after_a_run(run):
    app = FastAPI()
    app.include_router(fleet_map.router)
    body = TestClient(app).get("/api/fleetmap", params={"run": run}).json()
    assert body["run"] == run and body["routes"] and body["schematic"].startswith("Schematic")
