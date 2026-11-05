import pytest

from trex.belief import estimate
from trex.ledger import Ledger, _all_lines
from trex.normalize import case_pack, group_of, pack_kg
from trex.planning import Night
from trex.solver import Line, solve

EVENING, DAY = "2026-10-15", "2026-10-16"


def test_month_totals_match_masking():
    d = _all_lines()
    assert len(d) == 23282
    assert round(d["net_amount"].sum()) == 57400874


def test_ledger_never_returns_the_future():
    assert Ledger(EVENING).lines()["invoice_date"].max() <= EVENING


def test_orders_for_past_date_is_refused():
    with pytest.raises(ValueError):
        Ledger(EVENING).orders_for("2026-10-10")


def test_pack_parsing():
    assert pack_kg("Toned Milk 12x1 Ltr TP") == 1
    assert pack_kg("Butter 30x500 Gm CP") == 0.5
    assert case_pack("Toned Milk 12x1 Ltr TP") == 12
    assert case_pack("Butter 8x8x200 Gm Tub") == 64
    assert group_of("Malai Paneer Block 14x1 Kg", "Frozen Products") == "frozen"
    assert group_of("Butter 30x500 Gm CP", "Milk Products") == "butter"


def test_partial_first_gives_every_shelf_something():
    lines = [Line(f"k{i}", "T", "R", f"r{i}", "kirana", "P", "P", 10, 1.0, 100.0, "order proxy") for i in range(4)]
    result = solve(lines, {"P": 20}, {"T": 1000})
    alloc = result["allocated"]
    assert sum(alloc.values()) == 20
    assert all(v >= 5 for v in alloc.values())


def test_hotel_comes_first_when_stock_is_scarce():
    hotel = Line("h", "T", "R", "h", "horeca", "P", "P", 10, 1.0, 100.0, "order proxy")
    shop = Line("k", "T", "R", "k", "kirana", "P", "P", 10, 1.0, 100.0, "order proxy")
    alloc = solve([hotel, shop], {"P": 10}, {"T": 1000})["allocated"]
    assert alloc["h"] >= alloc["k"]
    assert alloc["k"] > 0


def test_truck_capacity_holds():
    lines = [Line(f"k{i}", "T", "R", f"r{i}", "kirana", f"P{i}", "P", 100, 1.0, 10.0, "order proxy") for i in range(3)]
    alloc = solve(lines, {}, {"T": 150})["allocated"]
    assert sum(alloc.values()) <= 150


def test_same_input_same_plan():
    a = Night(EVENING, DAY).solved()["allocated"]
    b = Night(EVENING, DAY).solved()["allocated"]
    assert a == b


def test_hero_truck_matches_plan():
    night = Night(EVENING, DAY)
    trucks = {t["truck"]: t for t in night.truck_summary()}
    assert 2200 <= trucks["V2"]["kg"] <= 2350
    assert 0.65 <= trucks["V2"]["fill"] <= 0.72


def test_options_are_priced_and_add_up():
    p = Night(EVENING, DAY).price_options("V2")
    names = [o["name"] for o in p["options"]]
    assert {"go", "sell_to_fill", "hold"} <= set(names)
    for o in p["options"]:
        assert o["net_cost_inr"] == o["truck_cost_inr"] + o["margin_lost_inr"] + o["expiry_risk_inr"] + o["discounts_inr"] - o["extra_margin_inr"]


def test_belief_is_wide_with_little_history():
    ledger = Ledger(EVENING)
    none = estimate(ledger, "R-999", "P999")
    assert none.bills == 0 and none.high_days - none.low_days >= 14
