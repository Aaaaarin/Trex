"""The dispatch lead's web app. Run: python -m uvicorn trex.server:app --port 8000"""
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import orchestrator, orders_api, state, store
from .config import DEMO

WEB = Path(__file__).parent / "web"
app = FastAPI(title="Trex")
app.mount("/static", StaticFiles(directory=WEB), name="static")


class Request(BaseModel):
    text: str


class Decision(BaseModel):
    answer: str
    by: str = "dispatch lead"


class Fault(BaseModel):
    fault: str | None = None
    times: int = 1


class Live(BaseModel):
    retailer: str | None = None
    roles: list[str] = ["driver", "approver"]


class Inject(BaseModel):
    sender: str
    role: str
    text: str
    photo: bool = False


@app.on_event("startup")
def startup() -> None:
    orchestrator.resume_all()


def _latest() -> str | None:
    found = store.rows("select id from runs order by created desc limit 1")
    return found[0]["id"] if found else None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/state")
def get_state(run: str | None = None) -> dict:
    return jsonable_encoder(state.snapshot(run or _latest()))


@app.post("/api/request")
def post_request(body: Request) -> dict:
    run = f"run{int(time.time())}"
    orchestrator.start_run(run, body.text)
    return {"run": run}


@app.post("/api/decision/{decision_id}")
def post_decision(decision_id: str, body: Decision, run: str | None = None) -> dict:
    target = run or _latest()
    orchestrator.answer_decision(target, decision_id, body.answer, body.by)
    return {"ok": True}


@app.post("/api/fault")
def post_fault(body: Fault, run: str | None = None) -> dict:
    target = run or _latest()
    if not target:
        raise HTTPException(400, "No run yet")
    orders_api.inject(target, body.fault, body.times)
    store.log(target, "fault_set", fault=body.fault, times=body.times)
    return {"ok": True}


@app.post("/api/live")
def post_live(body: Live, run: str | None = None) -> dict:
    target = run or _latest()
    live = store.get_kv(target, "live", {})
    live["recipients"] = [body.retailer] if body.retailer else []
    live["roles"] = body.roles
    store.set_kv(target, "live", live)
    return live


@app.post("/api/tomorrow")
def post_tomorrow(run: str | None = None) -> dict:
    return orchestrator.run_tomorrow(run or _latest())


@app.post("/api/inject")
def post_inject(body: Inject, run: str | None = None) -> dict:
    """Testing aid: an update arriving late, out of order or twice."""
    target = run or _latest()
    added = store.add_inbox(target, body.sender, body.role, body.text, True, f"inject:{body.sender}:{body.text}", {"photo": body.photo})
    if target in orchestrator.RUNNERS:
        orchestrator.RUNNERS[target].wake.set()
    return {"added": added}


@app.get("/api/evidence/{evidence_id:path}")
def get_evidence(evidence_id: str) -> dict:
    e = store.get_evidence(evidence_id)
    if not e:
        raise HTTPException(404, "No such evidence")
    return jsonable_encoder(e)


@app.get("/api/retailer/{retailer}")
def get_retailer(retailer: str, run: str | None = None) -> dict:
    return jsonable_encoder(state.retailer_view(run or _latest(), retailer))


@app.post("/api/reset")
def post_reset() -> dict:
    """Clear every run, ready for a clean demo."""
    for runner in orchestrator.RUNNERS.values():
        runner.stop()
    orchestrator.RUNNERS.clear()
    for table in ("runs", "events", "evidence", "outbox", "inbox", "turns", "decisions", "kv", "test_orders"):
        store.execute(f"delete from {table}")
    return {"ok": True, "plan_date": DEMO["plan_date"]}
