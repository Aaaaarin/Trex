"""Web routes for drivers, the dispatcher and the retailer desk."""
import time

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from . import desk, fleet, store

router = APIRouter(prefix="/api")


class DriverReply(BaseModel):
    driver: str
    text: str
    truck: str | None = None


class Gate(BaseModel):
    truck: str
    event: str
    photo: bool = False


class DeskMessage(BaseModel):
    retailer: str
    text: str


class FleetLive(BaseModel):
    driver: str | None = None
    dispatcher: bool = False


def _latest() -> str | None:
    found = store.rows("select id from runs order by created desc limit 1")
    return found[0]["id"] if found else None


def _target(run: str | None) -> str:
    target = run or _latest()
    if not target:
        raise HTTPException(400, "No run yet")
    return target


@router.get("/fleet")
def get_fleet(run: str | None = None) -> dict:
    """The screen polls this before any run exists, so an empty answer is a normal one."""
    target = run or _latest()
    return jsonable_encoder({"run": target, **fleet.snapshot(target), "desk": desk.snapshot(target)})


@router.post("/fleet/driver")
def post_driver(body: DriverReply, run: str | None = None) -> dict:
    """Stand in for a driver from the screen. Labelled simulated, like every other reply not from the phone."""
    return jsonable_encoder(fleet.driver_reply(_target(run), body.driver, body.text, True, body.truck))


@router.post("/fleet/gate")
def post_gate(body: Gate, run: str | None = None) -> dict:
    return jsonable_encoder(fleet.log_gate(_target(run), body.truck, body.event, photo=body.photo, simulated=True))


@router.post("/fleet/no_show/{truck}")
def post_no_show(truck: str, run: str | None = None) -> dict:
    return jsonable_encoder(fleet.no_show(_target(run), truck))


@router.post("/fleet/live")
def post_fleet_live(body: FleetLive, run: str | None = None) -> dict:
    """Put one driver and the dispatcher on the team phone. Everyone else stays simulated."""
    target = _target(run)
    roles = [r for r in store.get_kv(target, "fleet_live_roles", []) if r not in ("driver_live", "dispatcher")]
    if body.driver:
        roles.append("driver_live")
    if body.dispatcher:
        roles.append("dispatcher")
    store.set_kv(target, "fleet_live_driver", body.driver)
    store.set_kv(target, "fleet_live_roles", roles)
    return {"driver": body.driver, "roles": roles}


@router.post("/desk")
def post_desk(body: DeskMessage, run: str | None = None) -> dict:
    """A shop's message typed on the screen, labelled simulated."""
    return jsonable_encoder(desk.handle(_target(run), body.retailer.upper(), body.text, True, f"web:{body.retailer}:{body.text}:{int(time.time() // 5)}"))
