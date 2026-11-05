"""History on the phone screens: real bills stay real, invented figures say so, and ratings order the offers."""
import json

import pytest

from trex import fleet, history, phone
from trex.config import DATA, DEMO
from trex.ledger import Ledger


def test_shop_history_matches_the_real_bills():
    shop = phone.shops()[0]["id"]
    view = history.shop(shop)
    lines = Ledger(DEMO["plan_evening"], DEMO["distributor"]).lines()
    own = lines[lines["retailer_id"] == shop]
    assert view["bills_count"] == own["invoice_id"].nunique()
    assert abs(view["total_inr"] - own["net_amount"].sum()) <= view["bills_count"]
    assert view["total_inr"] == sum(b["amount_inr"] for b in view["bills"])
    assert all(b["date"] <= DEMO["plan_evening"] for b in view["bills"])
    assert view["delivery_standin"] is True


def test_every_driver_has_invented_history_that_adds_up():
    for driver in fleet.ROSTER:
        view = history.driver(driver)
        assert view["standin"] is True
        assert view["trips_count"] > 0
        assert view["earned_inr"] == sum(t["pay_inr"] for t in view["trips"])
        assert sum(d["amount_inr"] for d in view["earned_by_day"]) == view["earned_inr"]
        assert 0 < view["on_time_pct"] <= 100 and 0 < view["acceptance_pct"] <= 100
        assert 1 <= view["rating"] <= 5
        assert all(t["kg"] <= fleet.ROSTER[driver]["max_kg"] for t in view["trips"])


def test_depot_history_is_fourteen_days_and_adds_up():
    view = history.depot("BR-1")
    assert view["standin"] is True
    assert len(view["days"]) == 14
    assert view["trucks_out"] == sum(d["trucks_out"] for d in view["days"])
    assert view["late"] == sum(d["late"] for d in view["days"])


def test_unknown_ids_are_refused():
    for fn, key in ((history.shop, "R-000"), (history.driver, "D99"), (history.depot, "BR-9")):
        with pytest.raises(ValueError):
            fn(key)


def test_better_rated_drivers_are_offered_first_and_the_order_is_stable():
    job = {"excluded": [], "kg": 1000}
    ranked = fleet._ranked("r-rank", job)
    assert ranked[0] == "D02"
    ratings = [history.rating(d) for d in ranked]
    assert ratings == sorted(ratings, reverse=True)
    assert fleet._ranked("r-rank", job) == ranked


def test_equal_ratings_fall_back_to_nearest(monkeypatch):
    monkeypatch.setattr(history, "rating", lambda driver: 4.0)
    ranked = fleet._ranked("r-tie", {"excluded": [], "kg": 1000})
    assert ranked == sorted(ranked, key=lambda d: (fleet.ROSTER[d]["km_to_depot"], d))


def test_ranking_still_respects_truck_weight_and_exclusions():
    ranked = fleet._ranked("r-limit", {"excluded": ["D02"], "kg": 5000})
    assert "D02" not in ranked
    assert all(fleet.ROSTER[d]["max_kg"] >= 5000 for d in ranked)


def test_history_file_has_no_em_dash_and_rebuilds_the_same():
    from importlib import util

    spec = util.spec_from_file_location("make_history", DATA.parent / "scripts" / "make_history.py")
    maker = util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    text = (DATA / "standins" / "history.json").read_text(encoding="utf-8")
    assert "—" not in text
    assert json.loads(text) == maker.build()


def test_routes_answer():
    from fastapi.testclient import TestClient

    from trex.server import app

    client = TestClient(app)
    assert client.get("/api/history/driver/D02").json()["standin"] is True
    assert client.get("/api/history/depot/BR-1").json()["depot"] == "BR-1"
    assert client.get("/api/history/driver/D99").status_code == 404
