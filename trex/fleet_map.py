"""A schematic fleet map. There is no GPS and every location is masked, so no position here is real.

Spokes and dots are placed by a hash so they never move between reloads. What is real is the state painted on them:
a truck sits at the depot until the gate log says it left, then moves only as its stops close. Nothing here writes.
"""
import hashlib
import math

from fastapi import APIRouter

from . import dispatch, fleet, history, state, store
from .config import DEMO

SCHEMATIC = "Schematic. Positions are invented, there is no GPS."
STANDIN = "Stand-in: drivers, ratings, km from depot and gate times are invented until the operator confirms them."
LANES = 3
_slots_cache: dict[str, list[str]] = {}
_sheet_cache: dict[str, tuple[object, dict]] = {}

router = APIRouter(prefix="/api")


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def route_layout(route_retailers: dict[str, list[str]]) -> dict[str, dict]:
    """Each route gets an angle, and each of its shops a place on the spoke. Input order never matters."""
    order = sorted(route_retailers, key=lambda r: (_h("route", r), r))
    out = {}
    for i, route in enumerate(order):
        shops = sorted(set(route_retailers[route]), key=lambda s: (_h(route, s), s))
        rows = max(1, math.ceil(len(shops) / LANES))
        slots = {}
        for j, shop in enumerate(shops):
            row, lane = j // LANES, j % LANES - 1
            slots[shop] = {"rf": round((row + 0.5) / rows, 4), "lane": lane + (0.25 if row % 2 else -0.25)}
        out[route] = {"angle": round(360 * i / len(order), 2), "slots": slots}
    return out


def stop_state(drop: dict | None, truck: str, left: bool) -> str:
    """Closed only by a drop saved for this truck. Open stops are 'out' once the truck has left, 'planned' before."""
    if drop and drop.get("truck") == truck and drop.get("status") in ("delivered", "failed"):
        return drop["status"]
    return "out" if left else "planned"


def run_order(stops: list[dict]) -> list[dict]:
    """The order a truck is drawn through its stops: around the depot, in and out along each spoke in turn."""
    angles = sorted({s["angle"] for s in stops})
    return sorted(stops, key=lambda s: (s["angle"], s["rf"] if angles.index(s["angle"]) % 2 == 0 else -s["rf"], s["retailer"]))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def truck_view(truck: str, kg: int, at_target: bool, delivered: int, failed: int, total: int, plan_status: str | None, job: dict | None, held: bool = False) -> dict:
    """Where a truck is and what colour it wears, from saved state alone. A closed stop needs a logged gate out or no job."""
    job = job if job and job.get("status") != "cancelled" else None
    assigned = bool(job and job["status"] == "assigned")
    closed = delivered + failed
    gate_out = assigned and "out" in job["gate"]
    left = not held and total > 0 and (gate_out or (job is None and closed > 0))
    phase = "depot" if not left else "done" if closed >= total else "out"
    waiting = not held and plan_status == "issued" and (job is None or job["status"] == "open")
    no_driver = bool(job and job["status"] == "no_driver")
    no_show = job["excluded"][-1] if job and job["status"] == "open" and job.get("excluded") else None
    if phase == "out":
        where = f"{closed} of {total} stops closed"
    elif phase == "done":
        where = f"All {total} stops closed"
    elif assigned and "in" in job["gate"]:
        where = f"Logged in at {job['gate']['in']['clock']}, not out yet"
    elif assigned:
        where = f"At depot, report {job['report_time']}"
    elif job and job["status"] == "open":
        where = f"Offering, round {job['round']}"
    elif plan_status == "issued":
        where = "Waiting for a driver"
    elif plan_status:
        where = "Plan not issued yet"
    else:
        where = "No plan yet"
    if held:
        tone, label = "mu", "Held, not leaving"
    elif no_driver:
        tone, label = "bad", "No driver free"
    elif no_show:
        tone, label = "bad", f"{no_show} no-show, offering again"
    elif failed:
        tone, label = "bad", f"{where}, {_plural(failed, 'stop')} failed"
    elif waiting or not at_target:
        tone, label = "warn", f"{where}, under target" if not at_target else where
    else:
        tone, label = "good", where
    return {
        "truck": truck,
        "driver": job["driver"] if assigned else None,
        "how": job["how"] if assigned else None,
        "kg": kg,
        "at_target": at_target,
        "phase": phase,
        "tone": tone,
        "label": label,
        "stops": total,
        "closed": closed,
        "delivered": delivered,
        "failed": failed,
        "waiting": waiting,
        "no_driver": no_driver,
        "no_show": no_show,
        "held": held,
    }


def driver_board(roster: list[dict], jobs: list[dict], rating=history.rating) -> list[dict]:
    """One row per driver. A driver who said no to an open job shows red until that job closes."""
    jobs = [j for j in jobs if j["status"] != "cancelled"]
    out = []
    for d in roster:
        did = d["id"]
        on = [j["truck"] for j in jobs if j["status"] == "assigned" and j["driver"] == did]
        missed = [j["truck"] for j in jobs if did in j.get("excluded", [])]
        opened = [j for j in jobs if j["status"] == "open" and did in j["asked"]]
        said_no = [j["truck"] for j in opened if any(r["driver"] == did and r["round"] == j["round"] and r["answer"] == "no" for r in j["replies"])]
        offered = [j["truck"] for j in opened if j["truck"] not in said_no]
        if on:
            tone, status, truck = "good", "On job", on[0]
        elif missed:
            tone, status, truck = "bad", "No-show", missed[0]
        elif said_no:
            tone, status, truck = "bad", "Rejected", said_no[0]
        elif offered:
            tone, status, truck = "warn", "Offered", offered[0]
        else:
            tone, status, truck = "good", "Available", None
        out.append({"id": did, "rating": rating(did), "km_to_depot": d["km_to_depot"], "max_kg": d["max_kg"], "tone": tone, "status": status, "truck": truck, "standin": True})
    return out


def summary_line(trucks: list[dict], stops: list[dict], has_run: bool) -> str:
    """One plain sentence, counted from the same rows the map draws."""
    count = lambda **kw: sum(1 for t in trucks if all(t[k] == v for k, v in kw.items()))
    parts = []
    out, done = count(phase="out"), count(phase="done")
    if out:
        parts.append(f"{_plural(out, 'truck')} out")
    if done:
        parts.append(f"{_plural(done, 'truck')} finished")
    at_depot = count(phase="depot", held=False, waiting=False, no_driver=False)
    if at_depot:
        parts.append(f"{_plural(at_depot, 'truck')} at the depot")
    if count(waiting=True):
        parts.append(f"{count(waiting=True)} waiting for a driver")
    if count(no_driver=True):
        parts.append(f"{count(no_driver=True)} with no driver")
    if count(held=True):
        parts.append(f"{count(held=True)} held")
    states = [s["state"] for s in stops]
    if states.count("delivered"):
        parts.append(f"{_plural(states.count('delivered'), 'stop')} delivered")
    if states.count("failed"):
        parts.append(f"{_plural(states.count('failed'), 'stop')} failed")
    if not has_run:
        parts.append(f"{_plural(len(stops), 'stop')} on tomorrow's plan")
        parts.append("no run yet")
    return ", ".join(parts) if parts else "Nothing planned for tomorrow"


def _ledger_slots() -> dict[str, list[str]]:
    """Every route and shop in the masked bills, so a shop keeps its place on any night, with or without a stop."""
    if not _slots_cache:
        from .ledger import _all_lines

        d = _all_lines()
        d = d[d["distributor"] == DEMO["distributor"]]
        for route, rows in d.groupby("route_id"):
            _slots_cache[str(route)] = sorted(rows["retailer_id"].unique().tolist())
    return _slots_cache


def _rows_for(run: str | None, truck: str, issued: bool) -> list[dict]:
    """An issued plan's stops come from its load sheet. Before that, from tonight's plan, cached until the plan moves."""
    if issued and run:
        saved = store.get_kv(run, f"sheet:{truck}", [])
        if saved:
            return saved
    night = state._night(run)
    key = run or "_"
    if key not in _sheet_cache or _sheet_cache[key][0] is not night:
        _sheet_cache[key] = (night, {})
    sheets = _sheet_cache[key][1]
    if truck not in sheets:
        sheets[truck] = [r for r in night.load_sheet(truck) if r["pieces"] > 0]
    return sheets[truck]


def snapshot(run: str | None) -> dict:
    board = state.board(run)
    plans = dispatch.all_current_plans(run) if run else {}
    drops = state._drops(run) if run else {}
    jobs = fleet.jobs(run) if run else []
    summaries = {t["truck"]: t for t in board["trucks"]}
    held_now = {t for t in plans if dispatch.truck_status(run, t) == "held"}
    for truck in sorted(held_now - set(summaries)):
        summaries[truck] = {"truck": truck, "kg": plans[truck]["kg"], "at_target": False}

    rows_by_truck = {t: _rows_for(run, t, bool(plans.get(t) and plans[t]["status"] == "issued")) for t in summaries if t not in held_now}
    routes = {r: list(s) for r, s in _ledger_slots().items()}
    for rows in rows_by_truck.values():
        for row in rows:
            routes.setdefault(row["route"], [])
            if row["retailer"] not in routes[row["route"]]:
                routes[row["route"]].append(row["retailer"])
    layout = route_layout(routes)

    trucks, stops = [], []
    for name in sorted(summaries):
        t = summaries[name]
        plan = plans.get(name)
        job = fleet.job_for(run, name) if run and plan else None
        placed = {}
        for row in rows_by_truck.get(name, []):
            placed.setdefault(row["retailer"], row["route"])
        closed = [drops[s]["status"] for s in placed if s in drops and drops[s].get("truck") == name]
        view = truck_view(name, t["kg"], t["at_target"], closed.count("delivered"), closed.count("failed"), len(placed), plan["status"] if plan else None, job, name in held_now)
        left = view["phase"] != "depot"
        own = [
            {"key": f"{name}|{shop}", "truck": name, "retailer": shop, "route": route, "angle": layout[route]["angle"], **layout[route]["slots"][shop], "state": stop_state(drops.get(shop), name, left), "simulated": bool(drops[shop]["simulated"]) if shop in drops else None}
            for shop, route in placed.items()
        ]
        ordered = run_order(own)
        # Stop k of the drawn order after k stops closed, so the marker moves in proportion and never ahead of the drops.
        view["at"] = ordered[view["closed"] - 1]["key"] if left and view["closed"] else None
        view["routes"] = sorted(set(placed.values()))
        trucks.append(view)
        stops.extend(sorted(own, key=lambda s: s["key"]))

    on_truck = {}
    for s in stops:
        on_truck.setdefault(s["route"], set()).add(s["truck"])
    return {
        "run": run,
        "plan_date": board["plan_date"],
        "schematic": SCHEMATIC,
        "standin": STANDIN,
        "summary": summary_line(trucks, stops, bool(run)),
        "routes": sorted(({"route": r, "angle": v["angle"], "trucks": sorted(on_truck.get(r, []))} for r, v in layout.items()), key=lambda r: r["angle"]),
        "stops": stops,
        "trucks": trucks,
        "drivers": driver_board(fleet.CONFIG["drivers"], jobs),
        "merged": [{"truck": m["truck"], "into": m["into"]} for m in board["merged"]],
    }


@router.get("/fleetmap")
def get_fleetmap(run: str | None = None) -> dict:
    """The screen polls this before any run exists, so an empty night is a normal answer."""
    found = store.rows("select id from runs order by created desc limit 1")
    return snapshot(run or (found[0]["id"] if found else None))
