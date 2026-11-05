"""History behind the phone screens. A shop's bills are real and masked. Driver and depot history is invented.

Every invented block says so with standin: True, and the screens show that as a label. Totals are worked out from the
rows, so a tile can never disagree with the list under it.
"""
import hashlib
import json

from fastapi import APIRouter, HTTPException

from .config import DATA, DEMO
from .ledger import Ledger

STORE = json.loads((DATA / "standins" / "history.json").read_text(encoding="utf-8"))
DELIVERY = ("delivered", "delivered", "delivered", "delivered", "delivered", "delivered", "late", "late", "short")

router = APIRouter(prefix="/api/history")


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def rating(driver: str) -> float:
    """The average of a driver's invented trip stars. Unknown drivers rate lowest, so a gap never jumps the queue."""
    rows = STORE["drivers"].get(driver, {}).get("trips", [])
    return round(sum(r["stars"] for r in rows) / len(rows), 2) if rows else 0.0


def shop(retailer: str) -> dict:
    """Real bills up to the plan evening, so nothing from the plan day or later shows. Delivery status is invented."""
    lines = Ledger(DEMO["plan_evening"], DEMO["distributor"]).lines()
    own = lines[lines["retailer_id"] == retailer]
    if own.empty:
        raise ValueError(f"No bills for {retailer}")
    bills = []
    for invoice, rows in own.groupby("invoice_id"):
        top = rows.sort_values("net_amount", ascending=False)["product_name"].head(2).tolist()
        bills.append({"invoice": invoice, "date": rows["invoice_date"].iloc[0], "amount_inr": round(float(rows["net_amount"].sum())), "lines": len(rows), "top_products": top, "delivery": DELIVERY[_h(retailer, invoice) % len(DELIVERY)]})
    bills.sort(key=lambda b: (b["date"], b["invoice"]), reverse=True)
    total = sum(b["amount_inr"] for b in bills)
    usual = own.groupby(["product_id", "product_name"], as_index=False).agg(value=("net_amount", "sum"), bills=("invoice_id", "nunique")).sort_values(["value", "product_id"], ascending=[False, True]).head(5)
    products = [{"product": r.product_id, "name": r.product_name, "amount_inr": round(float(r.value)), "share": round(float(r.value) / max(total, 1), 3), "bills": int(r.bills)} for r in usual.itertuples()]
    return {"retailer": retailer, "name": own["retailer_alias"].iloc[0], "records_to": DEMO["plan_evening"], "bills_count": len(bills), "total_inr": total, "average_inr": round(total / len(bills)), "last_bill": bills[0]["date"], "bills": bills, "usual": products, "delivery_standin": True}


def driver(driver_id: str) -> dict:
    record = STORE["drivers"].get(driver_id)
    if record is None:
        raise ValueError(f"No history for {driver_id}")
    trips = record["trips"]
    earned: dict[str, int] = {}
    for trip in trips:
        earned[trip["date"]] = earned.get(trip["date"], 0) + trip["pay_inr"]
    days = sorted({d["date"] for depot in STORE["depots"].values() for d in depot["days"]} | set(earned))
    count = len(trips)
    return {
        "driver": driver_id,
        "standin": True,
        "note": STORE["_note"],
        "as_of": STORE["as_of"],
        "rating": rating(driver_id),
        "trips_count": count,
        "earned_inr": sum(t["pay_inr"] for t in trips),
        "on_time_pct": round(100 * sum(t["on_time"] for t in trips) / count) if count else 0,
        "acceptance_pct": round(100 * count / record["offers_seen"]) if record["offers_seen"] else 0,
        "kg": sum(t["kg"] for t in trips),
        "earned_by_day": [{"date": d, "amount_inr": earned.get(d, 0)} for d in days],
        "trips": sorted(trips, key=lambda t: t["date"], reverse=True),
    }


def depot(depot_id: str) -> dict:
    record = STORE["depots"].get(depot_id)
    if record is None:
        raise ValueError(f"No history for {depot_id}")
    days = []
    for day in record["days"]:
        entries = day["entries"]
        days.append({"date": day["date"], "trucks_in": len(entries), "trucks_out": len(entries), "late": sum(1 for e in entries if e["late_minutes"]), "pick_lists": len(entries), "pick_lines": sum(e["pick_lines"] for e in entries), "entries": entries})
    out = sum(d["trucks_out"] for d in days)
    late = sum(d["late"] for d in days)
    return {"depot": depot_id, "standin": True, "note": STORE["_note"], "as_of": STORE["as_of"], "trucks_in": sum(d["trucks_in"] for d in days), "trucks_out": out, "late": late, "on_time_pct": round(100 * (out - late) / out) if out else 0, "pick_lists": sum(d["pick_lists"] for d in days), "pick_lines": sum(d["pick_lines"] for d in days), "days": list(reversed(days))}


def _answer(fn, key: str) -> dict:
    try:
        return fn(key)
    except ValueError as err:
        raise HTTPException(404, str(err))


@router.get("/shop/{retailer}")
def get_shop(retailer: str) -> dict:
    return _answer(shop, retailer.upper())


@router.get("/driver/{driver_id}")
def get_driver(driver_id: str) -> dict:
    return _answer(driver, driver_id.upper())


@router.get("/depot/{depot_id}")
def get_depot(depot_id: str) -> dict:
    return _answer(depot, depot_id.upper())
