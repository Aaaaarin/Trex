"""Frozen dispatch plans and the validator nothing can skip.

A plan is built once and frozen. Orders, batches and driver instructions all read that same version.
A later reply cannot change an issued plan: it becomes a proposed revision for a person to review.
The validator recomputes every limit from source data, and never trusts the solver or the model.
"""
import hashlib
import json
import time

from . import store
from .config import standin
from .planning import Night

LOCKED = ("frozen", "committed", "issued")
OK_WRITES = ("saved", "saved_after_error", "already_saved")


def _plan_row(row: dict) -> dict:
    plan = json.loads(row["data"])
    plan.update({"status": row["status"], "version": row["version"], "hash": row["hash"]})
    return plan


def current_plan(run: str, truck: str) -> dict | None:
    rows = store.rows("select * from plans where run=? and truck=? and status!='superseded' order by version desc limit 1", (run, truck))
    return _plan_row(rows[0]) if rows else None


def all_current_plans(run: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in store.rows("select * from plans where run=? and status!='superseded' order by version", (run,)):
        out[row["truck"]] = _plan_row(row)
    return out


def set_plan_status(run: str, truck: str, version: int, status: str) -> None:
    store.execute("update plans set status=? where run=? and truck=? and version=?", (status, run, truck, version))
    store.log(run, "plan_status", truck=truck, version=version, status=status)


def truck_status(run: str, truck: str) -> str | None:
    return store.get_kv(run, f"truck_status:{truck}")


def open_reservations(run: str) -> list[dict]:
    now = time.time()
    ttl = standin("offer_ttl_seconds")
    for r in store.rows("select key, run, retailer, product, ts from reservations where run=? and status='open'", (run,)):
        if now - r["ts"] > ttl:
            store.execute("update reservations set status='expired' where key=?", (r["key"],))
            store.log(run, "reservation_expired", retailer=r["retailer"], product=r["product"])
    return store.rows("select * from reservations where run=? and status='open'", (run,))


def night_for(run: str | None, evening: str, plan_date: str, distributor: str) -> Night:
    """Rebuild tonight's position. Frozen plans stay exactly as approved, and reserved stock stays reserved."""
    if not run:
        return Night(evening, plan_date, distributor)
    plans = {t: p for t, p in all_current_plans(run).items() if p["status"] in LOCKED}
    reserved: dict[str, int] = {}
    for r in open_reservations(run):
        reserved[r["product"]] = reserved.get(r["product"], 0) + r["pieces"]
    held = {k.split(":", 1)[1] for k in [r["key"] for r in store.rows("select key from kv where run=? and key like 'truck_status:%' and value=?", (run, json.dumps("held")))]}
    return Night(
        evening,
        plan_date,
        distributor,
        extras=store.get_kv(run, "extras", []),
        merges=store.get_kv(run, "merges", {}),
        held=held,
        locked={t: {ln["key"]: ln["pieces"] for ln in p["lines"]} for t, p in plans.items()},
        locked_batches=_batch_use(plans),
        reserved=reserved,
    )


def batch_key(product: str, batch: str) -> str:
    """Batch codes repeat across products in real records, so a batch is only unique within a product."""
    return f"{product}|{batch}"


def _batch_use(plans: dict[str, dict]) -> dict[str, int]:
    used: dict[str, int] = {}
    for plan in plans.values():
        for ln in plan["lines"]:
            for b in ln["batches"]:
                key = batch_key(ln["product"], b["batch"])
                used[key] = used.get(key, 0) + b["pieces"]
    return used


def basis_of(run: str, truck: str | None = None) -> str:
    """Fingerprint of what a truck's plan rests on. If it changes, an earlier load approval no longer applies.

    A change on another truck does not touch it, so one truck cannot cancel another's approval.
    """
    extras = store.get_kv(run, "extras", [])
    merges = store.get_kv(run, "merges", {})
    if truck:
        extras = [e for e in extras if e["truck"] == truck or merges.get(e["truck"]) == truck]
        merges = {k: v for k, v in merges.items() if truck in (k, v)}
    payload = {"extras": extras, "merges": merges}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def find_approval(run: str, kind: str, **scope) -> dict | None:
    """An approval counts only for the exact action it named. A broad or different approval does not."""
    for d in store.rows("select * from decisions where run=? and answer is not null and lower(answer) like 'approve%'", (run,)):
        s = json.loads(d.get("scope") or "{}")
        if s.get("kind") == kind and all(s.get(k) == v for k, v in scope.items()):
            return d
    return None


def build(run: str, night: Night, truck: str, option: str, reason: str) -> dict:
    result = night.solved()
    rows = night.load_sheet(truck, result)
    lines, unplaced = [], []
    for row in rows:
        placed = sum(b["pieces"] for b in row["batches"])
        if placed < row["pieces"]:
            unplaced.append({"retailer": row["retailer"], "product": row["product"], "pieces": row["pieces"] - placed, "why": "No batch with enough life left for this retailer"})
        if placed <= 0:
            continue
        lines.append({"key": row["key"], "retailer": row["retailer"], "retailer_type": row["retailer_type"], "route": row["route"], "product": row["product"], "product_name": row["product_name"], "ordered": row["ordered"], "pieces": placed, "kg_each": row["kg_each"], "batches": row["batches"], "source": row["source"], "evidence": row["evidence"]})
    kg = sum(ln["pieces"] * ln["kg_each"] for ln in lines)
    capacity = night.capacity[truck]
    return {"truck": truck, "option": option, "reason": reason, "lines": lines, "unplaced": unplaced, "kg": round(kg, 1), "capacity_kg": capacity, "fill": round(kg / capacity, 3), "target": standin("fill_target"), "basis": basis_of(run, truck), "solver": result["method"]}


def validate(run: str, plan: dict, night: Night) -> list[str]:
    """Check a plan from source data. Any failure blocks orders and promises."""
    problems: list[str] = []
    truck = plan["truck"]
    status = truck_status(run, truck)
    if status in ("held", "merged"):
        problems.append(f"Truck {truck} is {status}, so it cannot dispatch")
    if plan["option"] not in ("go", "sell_to_fill"):
        problems.append(f"Option {plan['option']} is not a dispatch option. Use its own tool")
    if not plan["lines"]:
        problems.append("The plan has no lines")

    plans = {t: p for t, p in all_current_plans(run).items() if p["status"] in LOCKED and t != truck}
    plans[truck] = plan
    by_product: dict[str, int] = {}
    by_batch: dict[str, int] = {}
    for p in plans.values():
        for ln in p["lines"]:
            by_product[ln["product"]] = by_product.get(ln["product"], 0) + ln["pieces"]
            for b in ln["batches"]:
                key = batch_key(ln["product"], b["batch"])
                by_batch[key] = by_batch.get(key, 0) + b["pieces"]
    stock = night.stock_table
    product_stock = stock.groupby("product_id")["pieces"].sum().to_dict()
    batch_rows = {batch_key(r["product_id"], r["batch_id"]): r for r in stock.to_dict("records")}
    for product, pieces in by_product.items():
        if product not in product_stock:
            problems.append(f"{product} has no stock record")
        elif pieces > product_stock[product]:
            problems.append(f"{product}: planned {pieces}, stock {int(product_stock[product])}")
    for batch, pieces in by_batch.items():
        if batch not in batch_rows:
            problems.append(f"Batch {batch} has no stock record")
        elif pieces > batch_rows[batch]["pieces"]:
            problems.append(f"Batch {batch}: planned {pieces}, stock {int(batch_rows[batch]['pieces'])}")

    min_share = standin("min_life_share")
    kg = 0.0
    for ln in plan["lines"]:
        kg += ln["pieces"] * ln["kg_each"]
        if ln["pieces"] <= 0:
            problems.append(f"{ln['retailer']} {ln['product']}: no quantity")
        if ln["pieces"] > ln["ordered"]:
            problems.append(f"{ln['retailer']} {ln['product']}: planned {ln['pieces']}, more than {ln['ordered']} requested")
        if sum(b["pieces"] for b in ln["batches"]) != ln["pieces"]:
            problems.append(f"{ln['retailer']} {ln['product']}: batches do not add up to the quantity")
        for b in ln["batches"]:
            row = batch_rows.get(batch_key(ln["product"], b["batch"]))
            if row is None:
                continue
            if row["days_left"] <= 0:
                problems.append(f"Batch {b['batch']} has no life left")
            if row["share_left"] < min_share.get(ln["retailer_type"], 0.2):
                problems.append(f"Batch {b['batch']} has too little life left for {ln['retailer']}")
        if ln["source"] == "offer":
            e = store.get_evidence(ln["evidence"])
            if e is None or e["data"].get("sender") != ln["retailer"]:
                problems.append(f"{ln['retailer']} {ln['product']}: offer has no reply from that retailer")
        if ln["source"] == "request":
            e = store.get_evidence(ln["evidence"])
            if e is None:
                problems.append(f"{ln['retailer']} {ln['product']}: request has no source")

    capacity = standin("vehicle_capacity_kg").get(truck)
    if capacity is None:
        problems.append(f"Truck {truck} has no capacity record")
    elif kg > capacity + 0.5:
        problems.append(f"Load {kg:.0f} kg is over capacity {capacity} kg")
    elif kg / capacity < standin("fill_target") and not find_approval(run, "under_target", truck=truck, basis=plan["basis"]):
        problems.append(f"Load is {kg / capacity:.1%}, under the {standin('fill_target'):.0%} target, with no approval for this exact plan")

    for e in store.get_kv(run, "extras", []):
        if e["source"] == "request" and e.get("needs_approval") and not find_approval(run, "take_promised_stock", retailer=e["retailer"], product=e["product"], pieces=e["pieces"]):
            problems.append(f"{e['retailer']} {e['product']}: taking promised stock has no approval")
    return problems


def freeze(run: str, night: Night, truck: str, option: str, reason: str) -> dict:
    plan = build(run, night, truck, option, reason)
    problems = validate(run, plan, night)
    if problems:
        store.log(run, "plan_rejected", truck=truck, problems=problems[:10])
        return {"error": "The dispatch validator refused this plan", "problems": problems[:10], "kg": plan["kg"], "fill": plan["fill"], "unplaced": plan["unplaced"][:10]}
    version = 1 + (store.rows("select coalesce(max(version),0) v from plans where run=? and truck=?", (run, truck))[0]["v"])
    body = json.dumps(plan, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{truck}:{version}:{body}".encode()).hexdigest()[:12]
    store.execute("insert into plans(run, truck, version, status, data, hash, ts) values(?,?,?,?,?,?,?)", (run, truck, version, "frozen", body, digest, time.time()))
    store.log(run, "plan_frozen", truck=truck, version=version, hash=digest, fill=plan["fill"], kg=plan["kg"], lines=len(plan["lines"]), option=option, unplaced=len(plan["unplaced"]))
    return {"status": "frozen", "truck": truck, "version": version, "hash": digest, "fill": plan["fill"], "kg": plan["kg"], "lines": len(plan["lines"]), "unplaced": plan["unplaced"][:10]}


def checked_plan(run: str, night: Night, truck: str) -> tuple[dict | None, str | None]:
    """Return the frozen plan only if it is untouched and still valid. Every downstream tool starts here."""
    plan = current_plan(run, truck)
    if plan is None:
        return None, f"No frozen plan for {truck}. Use freeze_plan first"
    body = {k: v for k, v in plan.items() if k not in ("status", "version", "hash")}
    digest = hashlib.sha256(f"{truck}:{plan['version']}:{json.dumps(body, sort_keys=True, default=str)}".encode()).hexdigest()[:12]
    if digest != plan["hash"]:
        return None, f"Plan {truck} v{plan['version']} does not match its recorded hash"
    problems = validate(run, body, night)
    if problems:
        store.log(run, "plan_rejected", truck=truck, problems=problems[:10], at="downstream check")
        return None, "The dispatch validator refused this plan: " + "; ".join(problems[:5])
    return plan, None
