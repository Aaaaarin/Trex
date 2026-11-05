"""Paper cover against real cover, on labelled stand-in branch data.

Real invoices stop at the distributor. Everything above it here is a stand-in shaped like the
operator's description: ideal stock is ten days of plan plus transit days, and an open purchase
order counts as cover even with no truck against it. Trex counts it only once a truck is matched.
"""
import json

from .config import DATA

_PATH = DATA / "standins" / "upstream.json"


def _data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def cover(product: str) -> dict:
    d = _data()
    row = d["branch_position"].get(product, d["branch_position"]["default"])
    per_day = row["monthly_plan_mt"] / row["days_in_month"]
    paper_total = row["stock_mt"] + row["in_transit_mt"] + row["open_po_mt"]
    matched = [v for v in d["vehicles"] if v["matched_po"] == row["po_id"]]
    real_po = row["open_po_mt"] if matched else 0.0
    ideal = per_day * (10 + row["transit_days"])
    return {
        "branch": row["branch"],
        "per_day_mt": round(per_day, 2),
        "stock_mt": row["stock_mt"],
        "in_transit_mt": row["in_transit_mt"],
        "open_po_mt": row["open_po_mt"],
        "po_id": row["po_id"],
        "po_age_days": row["po_age_days"],
        "po_expires_after_days": d["po_expiry_days"],
        "vehicle_matched": bool(matched),
        "paper_cover_days": round(paper_total / per_day, 1),
        "stock_at_branch_days": round(row["stock_mt"] / per_day, 1),
        "real_cover_days": round((row["stock_mt"] + row["in_transit_mt"] + real_po) / per_day, 1),
        "calculated_dispatch_mt": round(max(0.0, ideal - paper_total), 1),
        "plant_stock_mt": row["plant_stock_mt"],
    }


def trace(product: str) -> dict:
    c = cover(product)
    if c["stock_mt"] >= c["per_day_mt"]:
        stage, owner = "not established", "nobody yet"
    elif c["open_po_mt"] > 0 and not c["vehicle_matched"]:
        stage, owner = "open purchase order has no truck", "dispatch desk"
    elif c["plant_stock_mt"] < c["calculated_dispatch_mt"]:
        stage, owner = "plant stock is below what is needed", "plant"
    elif c["in_transit_mt"] > 0:
        stage, owner = "stock is in transit", "transporter"
    else:
        stage, owner = "no purchase order raised", "dispatch desk"
    return {**c, "stopped_at": stage, "held_by": owner}


def vehicles_for(product: str) -> list[dict]:
    d = _data()
    row = d["branch_position"].get(product, d["branch_position"]["default"])
    return [v for v in d["vehicles"] if v["destination"] == row["branch"] and v["type"] in row["allowed_vehicle_types"] and not v["matched_po"]]


def po_expiring(product: str) -> bool:
    c = cover(product)
    return c["open_po_mt"] > 0 and not c["vehicle_matched"] and c["po_age_days"] >= c["po_expires_after_days"] - 1
