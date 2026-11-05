"""The Analysis tab: real figures only, no future bills, and no wild percent on a small base."""
import json

import pandas as pd
from fastapi.testclient import TestClient

from trex import analysis
from trex.config import DEMO
from trex.ledger import Ledger, _all_lines
from trex.server import app

DAYS = [f"2026-10-{d:02d}" for d in range(1, 15)]


def line(day, amount, route="R01", shop="R-001", product="P001", kind="kirana", group="milk", invoice=None, counter=False):
    return {
        "invoice_date": day, "net_amount": amount, "route_id": route, "retailer_id": shop, "product_id": product,
        "product_name": f"{product} pack", "retailer_type_hint": kind, "group": group,
        "invoice_id": invoice or f"I-{day}-{shop}-{product}", "is_counter": counter,
    }


def frame(rows):
    return pd.DataFrame(rows)


def real():
    return analysis.snapshot(None)


def test_percent_needs_a_big_enough_base():
    assert analysis.pct_change(120000, 100000) == 20.0
    assert analysis.pct_change(90000, 100000) == -10.0
    assert analysis.pct_change(50000, 100) is None
    assert analysis.pct_change(50000, 0) is None
    assert analysis.direction(None) == "none"
    assert analysis.direction(1.0) == "flat"
    assert analysis.moved(None) == "not enough data"
    assert analysis.moved(-0.4) == "flat"


def test_windows_split_the_selling_days_and_refuse_too_few():
    last, previous = analysis.split_windows(DAYS)
    assert previous == DAYS[:7] and last == DAYS[7:]
    assert analysis.split_windows(DAYS[:9]) == (DAYS[2:9], [])


def test_too_few_days_says_not_enough_data():
    data = frame([line(d, 100000) for d in DAYS[:5]])
    view = analysis.from_lines(data, DAYS[4], "A", "P001", "Test pack")
    assert all(t["pct"] is None for t in view["tiles"])
    assert view["routes"]["rows"] == []
    assert "Not enough data" in view["trend"]["takeaway"]
    assert "Not enough data" in view["routes"]["takeaway"]


def test_routes_rank_by_rupee_change_and_small_routes_are_set_aside():
    rows = []
    for d in DAYS[:7]:
        rows += [line(d, 50000, "R01"), line(d, 50000, "R02"), line(d, 10, "R03")]
    for d in DAYS[7:]:
        rows += [line(d, 60000, "R01"), line(d, 40000, "R02"), line(d, 90000, "R03")]
    view = analysis.routes(frame(rows), DAYS[7:], DAYS[:7])
    assert [r["key"] for r in view["rows"]] == ["R01", "R02"]
    assert [r["key"] for r in view["thin"]] == ["R03"]
    assert view["rows"][0]["pct"] == 20.0 and view["rows"][1]["pct"] == -20.0
    assert "R01 is up 20%" in view["takeaway"] and "R02 is down 20%" in view["takeaway"]


def test_quiet_shops_are_regular_buyers_gone_silent_and_valued_at_their_rate():
    rows = [line(d, 900, shop="R-100") for d in DAYS[:9]]  # stopped, 9 earlier selling days
    rows += [line(d, 500, shop="R-200") for d in DAYS]  # still buying
    rows += [line(DAYS[0], 9000, shop="R-300")]  # one bill is not regular
    data = frame(rows)
    view = analysis.quiet_shops(data, analysis.selling_days(data))
    assert [r["shop"] for r in view["rows"]] == ["R-100"]
    assert view["rows"][0]["weekly_inr"] == round(900 * 9 / 9 * 7)
    assert view["rows"][0]["last_bill"] == DAYS[8]
    assert "1 regular shop" in view["takeaway"] and "estimate" in view["takeaway"]


def test_product_gap_counts_the_silent_stretch():
    data = frame([line(d, 1000, product="P006") for d in DAYS[:7]] + [line(d, 1000, product="P001") for d in DAYS])
    gap = analysis.product_gap(data, DAYS, "P006", "Butter test")
    assert gap["silent_days"] == 7 and gap["last_bill"] == DAYS[6] and gap["total_inr"] == 7000
    assert "then nothing for 7 selling days" in gap["takeaway"]


def test_real_view_matches_the_ledger_and_stops_at_the_plan_evening():
    view = real()
    lines = Ledger(DEMO["plan_evening"], DEMO["distributor"]).lines()
    days = sorted(lines["invoice_date"].unique())
    assert view["records_to"] == DEMO["plan_evening"]
    assert view["trend"]["days"][-1]["date"] == DEMO["plan_evening"]
    assert all(d["date"] <= DEMO["plan_evening"] for d in view["trend"]["days"])
    assert all(d["date"] <= DEMO["plan_evening"] for d in view["gap"]["days"])
    assert len(view["trend"]["days"]) == len(days)
    sales = next(t for t in view["tiles"] if t["key"] == "sales")
    assert sales["now"] == round(lines[lines["invoice_date"].isin(days[-7:])]["net_amount"].sum())
    assert sales["before"] == round(lines[lines["invoice_date"].isin(days[-14:-7])]["net_amount"].sum())
    assert abs(sum(r["now"] for r in view["categories"]["rows"]) - sales["now"]) <= len(view["categories"]["rows"])
    assert abs(sum(r["now"] for r in view["routes"]["rows"] + view["routes"]["thin"]) - sales["now"]) <= 30


def test_real_view_ignores_bills_after_the_plan_evening():
    """A later day exists in the file, and none of its value may reach the screen."""
    everything = _all_lines()
    later = everything[(everything["distributor"] == DEMO["distributor"]) & (everything["invoice_date"] > DEMO["plan_evening"])]
    assert len(later), "the file should hold later bills for this test to mean anything"
    text = json.dumps(real()["trend"])
    assert not any(str(d) in text for d in later["invoice_date"].unique())


def test_no_wild_percent_and_no_masked_alias_or_em_dash_on_screen():
    view = real()
    for row in view["routes"]["rows"] + view["products"]["rising"] + view["products"]["falling"]:
        assert row["pct"] is not None
    assert all(r["before"] >= analysis.MIN_BASE_INR for r in view["routes"]["rows"])
    assert all(r["before"] >= analysis.MIN_PRODUCT_BASE_INR for r in view["products"]["rising"] + view["products"]["falling"])
    text = json.dumps(view, ensure_ascii=False)
    aliases = Ledger(DEMO["plan_evening"], DEMO["distributor"]).lines()["retailer_alias"].unique()
    assert not any(a in text for a in aliases)
    assert "—" not in text and "–" not in text


def test_takeaways_come_from_the_data():
    view = real()
    fall = view["routes"]["rows"][-1]
    assert fall["key"] in view["routes"]["takeaway"]
    quiet = view["quiet_shops"]
    assert quiet["value_inr"] == sum(r["weekly_inr"] for r in quiet["rows"])
    assert analysis.inr_short(quiet["value_inr"]) in quiet["takeaway"]
    assert all(r["last_bill"] < view["windows"]["last"]["days"][-analysis.QUIET_DAYS] for r in quiet["rows"])


def test_truck_load_reads_the_board_and_the_label_never_rounds_up_to_the_target():
    load = analysis.truck_load({"target": 0.8, "trucks": [
        {"truck": "V1", "fill": 0.797, "kg": 1000.4, "stops": 3},
        {"truck": "V2", "fill": 0.9, "kg": 2000, "stops": 5},
    ]})
    assert load["rows"][0]["fill_label"] == "79%" and load["rows"][0]["at_target"] is False
    assert load["takeaway"] == "1 of 2 trucks reaches 80%. V1 is lowest at 79%."


def test_the_route_serves_the_view():
    r = TestClient(app).get("/api/analysis")
    assert r.status_code == 200
    body = r.json()
    assert body["records_to"] == DEMO["plan_evening"]
    assert body["trucks"]["rows"]
