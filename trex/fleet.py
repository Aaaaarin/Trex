"""Drivers and the dispatcher. The plan is already frozen by now: this only finds who carries it.

An issued plan opens a job. Drivers get the job with its pay, and accept or reject. Pay rises each round up to a
limit. If nobody accepts, the job goes to someone by default. The dispatcher then hears who comes and what to hand
over, and logs the driver in and out.

All of this is code, so a driver's reply never costs a model turn. Only a no-show or an empty roster wakes the agent.
Jobs live beside the plan, keyed by truck and version, because the plan body is hashed. Drivers, pay and gate times
are stand-ins, labelled wherever they are shown.
"""
import hashlib
import json
import re
import threading
import time
import traceback

from . import channels, dispatch, store
from .config import DATA, standin

CONFIG = json.loads((DATA / "standins" / "fleet.json").read_text(encoding="utf-8"))
ROSTER = {d["id"]: d for d in CONFIG["drivers"]}
DISPATCHER = CONFIG["dispatcher"]
YES = re.compile(r"\b(haan|han|ha|yes|ok|okay|theek|accept|le lunga|kar lunga|chalega)\b", re.I)
NO = re.compile(r"\b(nahi|nahin|no|reject|nope|mat|busy)\b", re.I)
_lock = threading.RLock()


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _clock(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _key(truck: str, version: int) -> str:
    return f"job:{truck}:v{version}"


def _save(run: str, job: dict) -> None:
    store.set_kv(run, _key(job["truck"], job["version"]), job)


def jobs(run: str) -> list[dict]:
    rows = store.rows("select value from kv where run=? and key like 'job:%'", (run,))
    return sorted((json.loads(r["value"]) for r in rows), key=lambda j: (j["truck"], j["version"]))


def job_for(run: str, truck: str) -> dict | None:
    """The job for this truck's current plan. A job on an older version does not count."""
    plan = dispatch.current_plan(run, truck)
    return store.get_kv(run, _key(truck, plan["version"])) if plan else None


def _send(run: str, role: str, recipient: str, text: str, key: str) -> None:
    """Send without moving last_live, so a phone reply to an earlier question still reaches the right role."""
    before = store.get_kv(run, "last_live")
    result = channels.send(run, role, recipient, text, key)
    if result.get("channel") == "telegram":
        store.set_kv(run, "last_live", before or {})


def _driver_role(run: str, driver: str) -> str:
    return "driver_live" if store.get_kv(run, "fleet_live_driver") == driver else "driver_pool"


def _is_live(run: str, driver: str) -> bool:
    return channels.is_live(run, _driver_role(run, driver), driver)


def _dispatcher_live(run: str) -> bool:
    return bool(channels.is_live(run, "dispatcher", DISPATCHER))


def base_pay(truck: str) -> int:
    trip = standin("vehicle_capacity_kg")[truck] / 1000 * standin("trip_cost_inr_per_tonne_capacity")
    return round(trip * CONFIG["driver_pay_share_of_trip_cost"])


def pay_ceiling(run: str, truck: str, option: str) -> dict:
    """Pay can rise only while going still beats holding the truck, and never past the cap."""
    base = base_pay(truck)
    cap = round(base * CONFIG["pay_cap_share"])
    priced = store.get_kv(run, f"options:{truck}") or {}
    net = {o["name"]: o["net_cost_inr"] for o in priced.get("options", [])}
    if option in net and "hold" in net:
        room = max(0, net["hold"] - net[option])
        return {"ceiling": min(cap, base + room), "cap": cap, "go_vs_hold_room_inr": room}
    return {"ceiling": cap, "cap": cap, "go_vs_hold_room_inr": None}


def _busy(run: str) -> set[str]:
    return {j["driver"] for j in jobs(run) if j["status"] == "assigned"}


def _ranked(run: str, job: dict) -> list[str]:
    """Nearest first, as the pay is the same for everyone in a round and empty running is the only difference."""
    free = [d for d in CONFIG["drivers"] if d["id"] not in _busy(run) and d["id"] not in job["excluded"] and d["max_kg"] >= job["kg"]]
    return [d["id"] for d in sorted(free, key=lambda d: (d["km_to_depot"] * CONFIG["deadhead_inr_per_km"], d["id"]))]


def _plan_live(run: str, job: dict) -> bool:
    plan = dispatch.current_plan(run, job["truck"])
    return bool(plan and plan["version"] == job["version"] and plan["hash"] == job["plan_hash"] and plan["status"] == "issued" and dispatch.truck_status(run, job["truck"]) not in ("held", "merged"))


def _pick_list(run: str, truck: str) -> list[dict]:
    """What the dispatcher hands over: only lines whose orders saved, by product and batch."""
    picks: dict[tuple, dict] = {}
    for row in store.get_kv(run, f"sheet:{truck}", []):
        for b in row["batches"]:
            item = picks.setdefault((row["product"], b["batch"]), {"product": row["product"], "product_name": row["product_name"], "batch": b["batch"], "pieces": 0})
            item["pieces"] += b["pieces"]
    return sorted(picks.values(), key=lambda i: -i["pieces"])


def _open_jobs(run: str, now: float) -> None:
    for truck, plan in sorted(dispatch.all_current_plans(run).items(), key=lambda kv: -kv[1]["kg"]):
        if plan["status"] != "issued" or store.get_kv(run, _key(truck, plan["version"])):
            continue
        limit = pay_ceiling(run, truck, plan["option"])
        sheet = store.get_kv(run, f"sheet:{truck}", [])
        job = {"truck": truck, "version": plan["version"], "plan_hash": plan["hash"], "option": plan["option"], "status": "open", "round": 0, "base_pay": base_pay(truck), "pay": base_pay(truck), **limit, "kg": round(sum(r["pieces"] * r["kg_each"] for r in sheet)), "stops": len({r["retailer"] for r in sheet}), "asked": {}, "replies": [], "excluded": [], "driver": None, "how": None, "report_time": None, "gate": {}, "rolled": False, "opened": now}
        _save(run, job)
        store.log(run, "job_opened", truck=truck, version=plan["version"], pay=job["pay"], ceiling=job["ceiling"], kg=job["kg"])
        _start_round(run, job, now)


def _start_round(run: str, job: dict, now: float) -> None:
    ranked = _ranked(run, job)
    if not ranked:
        return _no_driver(run, job)
    job["round"] += 1
    job["round_started"] = now
    asked = ranked[: CONFIG["offer_width"]] if job["round"] == 1 else ranked
    job["asked"] = {d: job["round"] for d in asked}
    store.add_evidence(run, "standin", f"pay:{job['truck']}:v{job['version']}:r{job['round']}", "Driver pay offer", {"truck": job["truck"], "round": job["round"], "pay_inr": job["pay"], "base_pay_inr": job["base_pay"], "ceiling_inr": job["ceiling"], "go_vs_hold_room_inr": job["go_vs_hold_room_inr"]}, standin=True)
    _save(run, job)
    store.log(run, "job_offered", truck=job["truck"], version=job["version"], round=job["round"], pay=job["pay"], drivers=asked)
    for d in asked:
        text = f"Naya kaam: gaadi {job['truck']}, {job['stops']} stop, {job['kg']} kg. Depot se {ROSTER[d]['km_to_depot']} km. Pay Rs {job['pay']} (stand-in). Haan ya nahi?"
        _send(run, _driver_role(run, d), d, text, f"{run}:offer:{job['truck']}:v{job['version']}:r{job['round']}:{d}")


def _no_driver(run: str, job: dict) -> None:
    job["status"] = "no_driver"
    _save(run, job)
    store.log(run, "job_no_driver", truck=job["truck"], version=job["version"])
    store.add_inbox(run, "fleet", "system", f"No driver is free for truck {job['truck']}. Nothing leaves until a person finds one. Tell the lead.", False, f"no_driver:{job['truck']}:v{job['version']}")


def _sim_answer(run: str, driver: str, job: dict) -> str | None:
    """Simulated drivers weigh pay against the route. Some never answer, as in life."""
    if _h(run, driver, job["truck"], job["round"], "silent") % 6 == 0:
        return None
    need = job["base_pay"] * ROSTER[driver]["min_pay_share"] * CONFIG["route_reluctance"].get(job["truck"], 1.0)
    return "haan, le lunga" if job["pay"] >= need else "nahi, itne me nahi"


def _sim_replies(run: str, job: dict, now: float) -> None:
    if now - job["round_started"] < CONFIG["sim_reply_seconds"]:
        return
    done = {(r["driver"], r["round"]) for r in job["replies"]}
    for driver in list(job["asked"]):
        if job["status"] != "open":
            return
        if (driver, job["round"]) in done or _is_live(run, driver):
            continue
        answer = _sim_answer(run, driver, job)
        if answer is None:
            job["replies"].append({"driver": driver, "round": job["round"], "answer": "silent", "text": "", "simulated": True, "pay": job["pay"]})
            _save(run, job)
        else:
            _apply_reply(run, job, driver, answer, True, now)


def _apply_reply(run: str, job: dict, driver: str, text: str, simulated: bool, now: float) -> dict:
    says_no, says_yes = bool(NO.search(text)), bool(YES.search(text))
    answer = "yes" if says_yes and not says_no else "no" if says_no and not says_yes else "unclear"
    job["replies"].append({"driver": driver, "round": job["round"], "answer": answer, "text": text, "simulated": simulated, "pay": job["pay"]})
    _save(run, job)
    store.log(run, "driver_replied", truck=job["truck"], driver=driver, answer=answer, text=text, simulated=simulated, round=job["round"], pay=job["pay"])
    if answer == "yes":
        _assign(run, job, driver, "accepted", now)
    return {"answer": answer, "status": job["status"], "driver": job["driver"]}


def _close_round(run: str, job: dict, now: float) -> None:
    answered = {r["driver"] for r in job["replies"] if r["round"] == job["round"]}
    if now - job["round_started"] < CONFIG["round_seconds"] and not set(job["asked"]) <= answered:
        return
    if job["round"] >= CONFIG["max_rounds"] or job["pay"] >= job["ceiling"]:
        return _default_assign(run, job, now)
    step = round(job["base_pay"] * (1 + CONFIG["pay_step_share"] * job["round"]))
    job["pay"] = min(job["ceiling"], step)
    store.log(run, "pay_raised", truck=job["truck"], round=job["round"] + 1, pay=job["pay"], ceiling=job["ceiling"])
    _start_round(run, job, now)


def _default_assign(run: str, job: dict, now: float) -> None:
    """Nobody accepted, so the job goes to someone. Whoever has been forced least, then whoever is nearest."""
    ranked = _ranked(run, job)
    if not ranked:
        return _no_driver(run, job)
    forced = store.get_kv(run, "forced_count", {})
    driver = min(ranked, key=lambda d: (forced.get(d, 0), ranked.index(d)))
    forced[driver] = forced.get(driver, 0) + 1
    store.set_kv(run, "forced_count", forced)
    _assign(run, job, driver, "default", now)


def _assign(run: str, job: dict, driver: str, how: str, now: float) -> None:
    if not _plan_live(run, job):
        return _cancel(run, job, "Plan changed before a driver was assigned")
    taken = sum(1 for j in jobs(run) if j["status"] == "assigned")
    report = _minutes(CONFIG["report_time"]) + taken * CONFIG["report_stagger_minutes"]
    job.update({"status": "assigned", "driver": driver, "how": how, "report_time": _clock(report), "assigned_at": now})
    _save(run, job)
    store.add_evidence(run, "standin", f"assignment:{job['truck']}:v{job['version']}:{driver}", "Driver assignment", {"truck": job["truck"], "version": job["version"], "driver": driver, "how": how, "pay_inr": job["pay"], "report_time": job["report_time"]}, standin=True)
    store.log(run, "driver_assigned", truck=job["truck"], version=job["version"], driver=driver, how=how, pay=job["pay"], report_time=job["report_time"])
    tag = f"{run}:assign:{job['truck']}:v{job['version']}:{driver}"
    forced = "Koi tayyar nahi tha, isliye yeh kaam aapko diya gaya. " if how == "default" else ""
    _send(run, _driver_role(run, driver), driver, f"{forced}Gaadi {job['truck']} aapki. Kal {job['report_time']} depot par report karo. {job['stops']} stop, {job['kg']} kg, pay Rs {job['pay']} (stand-in).", tag)
    picks = _pick_list(run, job["truck"])
    head = "; ".join(f"{p['product_name']} batch {p['batch']}: {p['pieces']}" for p in picks[:6])
    more = f"; aur {len(picks) - 6} line screen par" if len(picks) > 6 else ""
    _send(run, "dispatcher", DISPATCHER, f"Kal gaadi {job['truck']}: driver {driver}, {job['report_time']} par (stand-in). {job['stops']} stop, {job['kg']} kg. Dena hai: {head}{more}. Aane par /gate {job['truck']} in, jaane par /gate {job['truck']} out.", f"{tag}:dispatcher")
    if store.get_kv(run, "morning_started"):
        _sim_gates(run, job)


def _cancel(run: str, job: dict, why: str) -> None:
    was = job["driver"]
    job.update({"status": "cancelled", "why": why})
    _save(run, job)
    store.log(run, "job_cancelled", truck=job["truck"], version=job["version"], why=why, driver=was)
    if was:
        _send(run, _driver_role(run, was), was, f"Gaadi {job['truck']} ka kaam cancel. Kal mat aana.", f"{run}:cancel:{job['truck']}:v{job['version']}:{was}")
        _send(run, "dispatcher", DISPATCHER, f"Gaadi {job['truck']} v{job['version']} cancel, driver {was} nahi aayega.", f"{run}:cancel:{job['truck']}:v{job['version']}:dispatcher")


def _cancel_stale(run: str) -> None:
    for job in jobs(run):
        if job["status"] in ("open", "assigned") and not _plan_live(run, job):
            _cancel(run, job, "This plan version was held, merged or replaced")


def driver_reply(run: str, driver: str, text: str, simulated: bool = False, truck: str | None = None, now: float | None = None) -> dict:
    """A driver's answer. The first yes takes the job. A yes after that is told the job has gone."""
    now = now or time.time()
    with _lock:
        if driver not in ROSTER:
            return {"error": f"Unknown driver {driver}"}
        candidates = [j for j in jobs(run) if j["status"] == "open" and driver in j["asked"] and (truck is None or j["truck"] == truck)]
        if not candidates:
            gone = [j for j in jobs(run) if driver in j["asked"] and (truck is None or j["truck"] == truck)]
            store.log(run, "driver_reply_ignored", driver=driver, text=text, why="No open offer")
            if gone:
                _send(run, _driver_role(run, driver), driver, "Yeh kaam ab khula nahi hai.", f"{run}:gone:{gone[-1]['truck']}:{driver}")
            return {"error": "No open offer for this driver"}
        if driver in _busy(run):
            return {"error": f"{driver} already has a job"}
        job = max(candidates, key=lambda j: j["round_started"])
        return _apply_reply(run, job, driver, text, simulated, now)


def log_gate(run: str, truck: str, event: str, by: str = DISPATCHER, photo: bool = False, simulated: bool = False, clock: str | None = None, now: float | None = None) -> dict:
    """The dispatcher's two taps. A repeat changes nothing, and an out before an in leaves the in as not established."""
    if event not in ("in", "out"):
        return {"error": "event must be in or out"}
    with _lock:
        job = job_for(run, truck)
        if not job or job["status"] != "assigned":
            return {"error": f"{truck} has no assigned driver to log"}
        if event in job["gate"]:
            store.log(run, "gate_repeat_ignored", truck=truck, event=event)
            return {"status": "already_logged", "gate": job["gate"]}
        at = clock or demo_clock(run, now or time.time())
        entry = {"clock": at, "by": by, "photo": photo, "simulated": simulated}
        if event == "in":
            late = _minutes(at) - _minutes(job["report_time"])
            entry["late_minutes"] = late if late > CONFIG["on_time_grace_minutes"] else 0
            entry["on_time"] = entry["late_minutes"] == 0
        job["gate"][event] = entry
        _save(run, job)
        eid = store.add_evidence(run, "gate", f"gate:{truck}:v{job['version']}:{event}", f"Dispatcher logged {truck} {event}", {"truck": truck, "driver": job["driver"], **entry}, standin=simulated)
        store.log(run, "gate_logged", truck=truck, driver=job["driver"], event=event, evidence=eid, **entry)
        return {"status": "logged", "gate": job["gate"], "evidence": eid}


def demo_clock(run: str, now: float) -> str:
    """Delivery-day time for the demo, which replays October. Shown as a demo clock, never as real time."""
    started = store.get_kv(run, "morning_started") or now
    return _clock(_minutes(CONFIG["demo_clock_start"]) + (now - started) * CONFIG["demo_clock_minutes_per_second"])


def _sim_gates(run: str, job: dict) -> None:
    if _dispatcher_live(run):
        return
    report = _minutes(job["report_time"])
    draw = _h(run, job["truck"], "gate")
    arrive = report + (18 if draw % 5 == 0 else -(draw % 12))  # most arrive early, about one in five runs late
    log_gate(run, job["truck"], "in", simulated=True, clock=_clock(arrive))
    log_gate(run, job["truck"], "out", simulated=True, clock=_clock(arrive + CONFIG["gate_turnaround_minutes"]))


def morning(run: str, now: float | None = None) -> None:
    """Delivery day begins. The plan is locked, so any job still open goes to a driver now."""
    now = now or time.time()
    with _lock:
        tick(run, now, roll=False)
        if not store.get_kv(run, "morning_started"):
            store.set_kv(run, "morning_started", now)
        for job in jobs(run):
            # Play out the remaining rounds now, so a driver who would have accepted still can.
            for _ in range(CONFIG["max_rounds"] + 1):
                if job["status"] != "open":
                    break
                _sim_replies(run, job, job["round_started"] + CONFIG["sim_reply_seconds"])
                if job["status"] == "open":
                    _close_round(run, job, job["round_started"] + CONFIG["round_seconds"])
        for job in jobs(run):
            if job["status"] == "assigned" and "out" not in job["gate"]:
                if _dispatcher_live(run):
                    _send(run, "dispatcher", DISPATCHER, f"Gaadi {job['truck']}: driver {job['driver']} {job['report_time']} par aayega. /gate {job['truck']} in", f"{run}:morning:{job['truck']}:v{job['version']}")
                else:
                    _sim_gates(run, job)


def may_roll(run: str, truck: str) -> bool:
    """A truck with a job leaves only once the dispatcher has logged it out. Trucks with no job are unaffected."""
    with _lock:
        job = job_for(run, truck)
        if not job:
            return True
        if job["status"] != "assigned" or "out" not in job["gate"]:
            return False
        job["rolled"] = True
        _save(run, job)
        return True


def no_show(run: str, truck: str, now: float | None = None) -> dict:
    """The assigned driver did not come. The job reopens at the top pay without them, and a person hears about it."""
    now = now or time.time()
    with _lock:
        job = job_for(run, truck)
        if not job or job["status"] != "assigned":
            return {"error": f"{truck} has no assigned driver"}
        if "in" in job["gate"]:
            return {"error": f"{job['driver']} has already reported"}
        driver = job["driver"]
        job["excluded"].append(driver)
        job.update({"status": "open", "driver": None, "how": None, "report_time": None, "pay": job["ceiling"]})
        store.log(run, "driver_no_show", truck=truck, driver=driver)
        _send(run, "dispatcher", DISPATCHER, f"Gaadi {truck}: driver {driver} nahi aaya. Naya driver dhoond rahe hain.", f"{run}:noshow:{truck}:v{job['version']}:{driver}")
        store.add_inbox(run, "fleet", "system", f"Driver {driver} did not report for truck {truck}. Trex is offering the job again at top pay. Tell the lead.", False, f"noshow:{truck}:{driver}")
        _start_round(run, job, now)
        return {"status": job["status"], "excluded": job["excluded"]}


def tick(run: str, now: float | None = None, roll: bool = True) -> None:
    now = now or time.time()
    with _lock:
        _cancel_stale(run)
        _open_jobs(run, now)
        for job in jobs(run):
            if job["status"] == "open":
                _sim_replies(run, job, now)
        for job in jobs(run):
            if job["status"] == "open":
                _close_round(run, job, now)
        pending = store.get_kv(run, "delivery_started") and any(j["status"] == "assigned" and "out" in j["gate"] and not j["rolled"] for j in jobs(run))
    if roll and pending:
        # A live dispatcher logged a truck out, so its drops can start now. Repeat sends are skipped by key.
        from . import orchestrator

        orchestrator.run_tomorrow(run)


class Worker(threading.Thread):
    """Runs beside the agent, so offers keep moving while a model call is waiting."""

    def __init__(self, run_id: str):
        super().__init__(daemon=True, name=f"fleet-{run_id}")
        self.run_id = run_id
        self.stopped = threading.Event()

    def run(self) -> None:
        from . import desk

        while not self.stopped.wait(1.0):
            try:
                tick(self.run_id)
                desk.tick(self.run_id)
            except Exception as err:  # a fleet failure must not stop the dispatch run
                store.log(self.run_id, "fleet_error", error=repr(err), trace=traceback.format_exc()[-800:])


def route_phone(run: str, text: str, photo: bool, update_id: int) -> bool:
    """Explicit commands for one phone playing several people. Anything else takes the normal reply path."""
    parts = text.strip().split(maxsplit=2)
    if not parts or parts[0].lower() not in ("/driver", "/gate", "/shop"):
        return False
    command = parts[0].lower()
    done = f"phone:{update_id}"
    if store.get_kv(run, done):
        return True
    store.set_kv(run, done, True)
    if command == "/driver" and len(parts) == 3:
        hit = re.search(r"\bV\d+\b", parts[2])
        result = driver_reply(run, parts[1].upper(), parts[2], False, hit.group(0) if hit else None)
        store.log(run, "phone_command", command=command, result=result)
    elif command == "/gate" and len(parts) == 3:
        result = log_gate(run, parts[1].upper(), parts[2].strip().split()[0].lower(), by="phone", photo=photo)
        store.log(run, "phone_command", command=command, result=result)
    elif command == "/shop" and len(parts) == 3:
        from . import desk

        desk.handle(run, parts[1].upper(), parts[2], False, done)
    else:
        store.log(run, "phone_command", command=command, result={"error": "Use /driver D03 haan, /gate V2 in, or /shop R-120 gaadi kahan hai"})
    return True


def snapshot(run: str | None) -> dict:
    """What the screen shows. Every value here is a stand-in except the plan it points at."""
    if not run:
        return {"jobs": [], "drivers": [], "note": CONFIG["_note"]}
    all_jobs = jobs(run)
    current = [j for j in all_jobs if j["status"] != "cancelled"]
    held = {j["driver"]: j["truck"] for j in all_jobs if j["status"] == "assigned"}
    for job in current:
        job["pick_list"] = _pick_list(run, job["truck"])
        job["live_driver"] = store.get_kv(run, "fleet_live_driver") in job["asked"]
    drivers = [{"id": d["id"], "km_to_depot": d["km_to_depot"], "max_kg": d["max_kg"], "truck": held.get(d["id"])} for d in CONFIG["drivers"]]
    return {"jobs": current, "cancelled": [j for j in all_jobs if j["status"] == "cancelled"], "drivers": drivers, "forced": store.get_kv(run, "forced_count", {}), "morning_started": bool(store.get_kv(run, "morning_started")), "dispatcher_live": _dispatcher_live(run), "note": CONFIG["_note"]}
