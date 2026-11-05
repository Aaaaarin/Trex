"""The order line, the driver's screen and the dispatcher's screen, without a model or a phone."""
import json
import time

import pytest

from trex import channels, dispatch, fleet, orchestrator, phone, store


class Quiet:
    """A stand-in runner, so no agent or model starts."""

    def __init__(self):
        self.wake = type("W", (), {"set": lambda self: None})()


@pytest.fixture(autouse=True)
def clean():
    store.execute("delete from runs")
    yield
    store.execute("delete from runs")
    store.execute("delete from kv where key like 'on_screen:%'")


@pytest.fixture
def no_agent(monkeypatch):
    started = []
    monkeypatch.setattr(phone, "Agent", lambda run: type("A", (), {"start": lambda self, text: (store.create_run(run, text), started.append(text))})())
    monkeypatch.setattr(orchestrator, "ensure_runner", lambda run, client=None: Quiet())
    return started


def test_first_call_adds_to_the_plan_and_starts_a_run(no_agent):
    shop = phone.shops()[0]["id"]
    result = phone.place_order(shop, "P004", 25, "case")
    assert result["status"] == "added"
    assert result["truck"] == "V2"
    assert result["pieces"] == 350
    assert len(no_agent) == 1 and "V2" in no_agent[0]
    assert store.get_kv(phone._latest(), "live")["recipients"] == [shop]
    view = phone._shop_view(shop)
    assert view["steps"]["order"] and view["steps"]["truck"] == "V2"


def test_call_refuses_bad_product_and_quantity(no_agent):
    shop = phone.shops()[0]["id"]
    assert phone.place_order(shop, "NOPE", 5)["status"] == "refused"
    assert phone.place_order(shop, "P004", 0)["status"] == "refused"
    assert phone._latest() is None


def test_typed_order_is_read_without_a_model():
    assert phone.text_order("25 peti paneer chahiye")["quantity"] == 25
    assert phone.text_order("25 peti paneer chahiye")["unit"] == "case"
    assert phone.text_order("gaadi kahan hai") is None


def _open_job(run: str, asked: list[str]) -> dict:
    job = {"truck": "V2", "version": 1, "plan_hash": "h", "option": "go", "status": "open", "round": 1, "base_pay": 100, "pay": 100, "ceiling": 135, "cap": 135, "go_vs_hold_room_inr": None, "kg": 2000, "stops": 3, "asked": {d: 1 for d in asked}, "replies": [], "excluded": [], "driver": None, "how": None, "report_time": None, "gate": {}, "rolled": False, "opened": time.time(), "round_started": time.time() - 60}
    fleet._save(run, job)
    return job


def test_simulated_drivers_wait_for_a_driver_on_screen():
    run = "r-wait"
    job = _open_job(run, ["D01", "D02"])
    channels.mark_on_screen("driver", "D02")
    assert fleet._waiting_on_person(run, job, time.time())
    fleet._sim_replies(run, job, time.time())
    assert job["replies"] == []
    job["replies"].append({"driver": "D02", "round": 1, "answer": "no", "text": "nahi", "simulated": False, "pay": 100})
    assert not fleet._waiting_on_person(run, job, time.time())


def test_the_wait_ends():
    run = "r-limit"
    job = _open_job(run, ["D02"])
    channels.mark_on_screen("driver", "D02")
    assert not fleet._waiting_on_person(run, job, job["round_started"] + 500)


def test_no_screen_means_no_wait():
    run = "r-gone"
    job = _open_job(run, ["D05"])
    store.set_kv("_global", "on_screen:driver:D05", time.time() - 60)
    assert not fleet._waiting_on_person(run, job, time.time())


def test_public_visitor_needs_the_key(monkeypatch):
    from fastapi.testclient import TestClient

    from trex.server import app

    monkeypatch.setattr(phone, "PUBLIC_KEY", "k1")
    client = TestClient(app, base_url="https://abc.example.com")
    assert client.get("/call").status_code == 404
    assert client.get("/call?key=k1").status_code == 200
    assert client.get("/gate").status_code == 200
    assert TestClient(app, base_url="http://127.0.0.1").get("/call").status_code == 200


def test_driver_screen_shows_the_job_and_the_callers_stop(no_agent):
    shop = phone.shops()[0]["id"]
    phone.place_order(shop, "P004", 25, "case")
    run = phone._latest()
    store.set_kv(run, "sheet:V2", [{"retailer": shop, "product": "P004", "product_name": "Paneer", "pieces": 350, "kg_each": 1.0, "batches": []}])
    job = {**_open_job(run, ["D02"]), "status": "assigned", "driver": "D02", "how": "accepted", "report_time": "06:00", "gate": {"out": {"clock": "06:10"}}}
    fleet._save(run, job)
    view = phone.driver_view("D02")
    assert view["job"]["truck"] == "V2"
    assert view["job"]["my_stops"] == [{"id": shop, "name": phone._alias(shop), "state": "open"}]
    assert phone.post_drop(phone.Drop(driver="D02", retailer=shop, delivered=True))["added"]
    assert phone.driver_view("D02")["job"]["my_stops"][0]["state"] == "sent"


def test_fleet_api_carries_the_stand_in_note():
    from fastapi.testclient import TestClient

    from trex.server import app

    note = TestClient(app).get("/api/fleet").json()["note"]
    assert "several real trucks" in note and "invented" in note


def test_a_tap_on_the_dispatcher_screen_is_not_logged_as_simulated(no_agent):
    shop = phone.shops()[0]["id"]
    phone.place_order(shop, "P004", 25, "case")
    run = phone._latest()
    job = {**_open_job(run, ["D02"]), "status": "assigned", "driver": "D02", "how": "accepted", "report_time": "06:00"}
    fleet._save(run, job)
    store.execute("insert into plans(run, truck, version, status, data, hash, ts) values(?,?,?,?,?,?,?)", (run, "V2", 1, "issued", json.dumps({"lines": [], "option": "go", "fill": 0.8, "kg": 2000, "unplaced": []}), "h", time.time()))
    result = phone.post_gate(phone.GateTap(truck="V2", event="in"))
    assert result["gate"]["in"]["simulated"] is False
