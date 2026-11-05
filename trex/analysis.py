"""The Analysis tab: what is going up and what is going down.

Everything except the truck load comes from real masked invoices read through the ledger, up to the plan evening, so
no future bill can reach this screen. Every figure and every takeaway line is worked out here from those rows. A change
worked out on too small a base comes back as None, and the screen shows "not enough data" instead of a wild percent.
"""
from functools import lru_cache

import pandas as pd
from fastapi import APIRouter

from . import state, store
from .config import DEMO
from .ledger import Ledger

WINDOW = 7  # selling days on each side of the comparison
QUIET_DAYS = 5  # selling days with no bill before a regular shop counts as stopped
REGULAR_BILL_DAYS = 3  # a shop is regular with bills on this many days before the quiet stretch
MIN_BASE_INR = 25000  # smallest earlier sales a route, category or type needs for a percent
MIN_PRODUCT_BASE_INR = 10000
MIN_PRODUCT_DAYS = 3  # a product billed once or twice earlier gives a wild percent
MIN_COUNT_BASE = 10  # smallest earlier count of shops, bills or products
FLAT_PCT = 2.0  # a smaller change than this reads as flat
LOW_DAY_SHARE = 0.5  # a day under this share of the usual day counts as a low day
TOP = 5
TYPE_NAMES = {"kirana": "Kirana", "horeca": "HoReCa", "modern": "Store", "outlet": "Outlet", "wholesale": "Wholesale"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
NOT_ENOUGH = "Not enough data to compare"

router = APIRouter(prefix="/api")


def short_date(day: str) -> str:
    d = pd.Timestamp(day)
    return f"{d.day} {MONTHS[d.month - 1]}"


def day_date(day: str) -> str:
    return f"{pd.Timestamp(day).strftime('%a')} {short_date(day)}"


def date_range(days: list[str]) -> str:
    if not days:
        return ""
    first, last = pd.Timestamp(days[0]), pd.Timestamp(days[-1])
    if first.month == last.month:
        return f"{first.day} to {short_date(days[-1])}"
    return f"{short_date(days[0])} to {short_date(days[-1])}"


def inr_short(value: float) -> str:
    """The same wording as the rest of the dashboard: 85,000 in full, then lakh."""
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a < 100000:
        return f"{sign}₹{round(a):,}"
    return f"{sign}₹{f'{a / 100000:.2f}'.rstrip('0').rstrip('.')} lakh"


def pct_change(now: float, before: float, min_base: float = MIN_BASE_INR) -> float | None:
    """Percent change, or None when the earlier figure is too small to mean anything."""
    if before < min_base or before <= 0:
        return None
    return round((now - before) / before * 100, 2)


def direction(pct: float | None) -> str:
    if pct is None:
        return "none"
    if pct >= FLAT_PCT:
        return "up"
    if pct <= -FLAT_PCT:
        return "down"
    return "flat"


def moved(pct: float | None) -> str:
    """Plain words for a change: up 18%, down 9%, flat. A rounded zero never reads as up or down."""
    if pct is None:
        return "not enough data"
    if direction(pct) == "flat":
        return "flat"
    return f"{'up' if pct > 0 else 'down'} {abs(round(pct))}%"


def selling_days(lines: pd.DataFrame) -> list[str]:
    return sorted(str(d) for d in lines["invoice_date"].unique())


def split_windows(days: list[str], size: int = WINDOW) -> tuple[list[str], list[str]]:
    """The last `size` selling days and the `size` before them. Too few days leaves the earlier window empty."""
    if len(days) < size * 2:
        return days[-size:], []
    return days[-size:], days[-size * 2:-size]


def compare(lines: pd.DataFrame, column: str, last: list[str], previous: list[str], min_base: float = MIN_BASE_INR, min_days: int = 0) -> list[dict]:
    """Sales by `column` in each window, sorted by rupee change, largest rise first."""
    now = lines[lines["invoice_date"].isin(last)].groupby(column)["net_amount"].sum()
    before_rows = lines[lines["invoice_date"].isin(previous)].groupby(column)
    before = before_rows["net_amount"].sum()
    before_days = before_rows["invoice_date"].nunique()
    total = float(now.sum())
    rows = []
    for key in sorted(set(now.index) | set(before.index)):
        n, b = float(now.get(key, 0.0)), float(before.get(key, 0.0))
        pct = pct_change(n, b, min_base) if int(before_days.get(key, 0)) >= min_days else None
        rows.append({
            "key": str(key), "now": round(n), "before": round(b), "change": round(n - b), "pct": pct,
            "direction": direction(pct), "share": round(n / total, 4) if total else 0.0,
        })
    return sorted(rows, key=lambda r: -r["change"])


def _extremes(rows: list[dict]) -> tuple[dict | None, dict | None]:
    risers = [r for r in rows if r["direction"] == "up"]
    fallers = [r for r in rows if r["direction"] == "down"]
    return (risers[0] if risers else None), (fallers[-1] if fallers else None)


def _best_and_worst(rows: list[dict], name=lambda r: r["label"]) -> str:
    rise, fall = _extremes(rows)
    parts = []
    if rise:
        parts.append(f"{name(rise)} is {moved(rise['pct'])}, the biggest rise")
    if fall:
        parts.append(f"{name(fall)} is {moved(fall['pct'])}, the biggest fall")
    if not parts:
        return NOT_ENOUGH if not any(r["pct"] is not None for r in rows) else "Nothing moved much between the two weeks"
    return ". ".join(parts) + "."


def tiles(lines: pd.DataFrame, last: list[str], previous: list[str]) -> list[dict]:
    now, before = lines[lines["invoice_date"].isin(last)], lines[lines["invoice_date"].isin(previous)]
    out = []

    def tile(key: str, label: str, n: float, b: float, base: float, money: bool = False) -> None:
        pct = pct_change(n, b, base)
        out.append({"key": key, "label": label, "now": round(n), "before": round(b), "pct": pct, "direction": direction(pct), "money": money})

    tile("sales", f"Sales, last {WINDOW} selling days", float(now["net_amount"].sum()), float(before["net_amount"].sum()), MIN_BASE_INR, True)
    tile("shops", "Active shops", now["retailer_id"].nunique(), before["retailer_id"].nunique(), MIN_COUNT_BASE)
    tile("bills", "Bills", now["invoice_id"].nunique(), before["invoice_id"].nunique(), MIN_COUNT_BASE)
    tile("products", "Products sold", now["product_id"].nunique(), before["product_id"].nunique(), MIN_COUNT_BASE)
    return out


def trend(lines: pd.DataFrame, last: list[str], previous: list[str]) -> dict:
    by_day = lines.groupby("invoice_date").agg(sales=("net_amount", "sum"), bills=("invoice_id", "nunique"))
    days = [{"date": str(d), "label": day_date(str(d)), "sales": round(float(r.sales)), "bills": int(r.bills), "window": "last" if d in last else "previous" if d in previous else "older"} for d, r in by_day.iterrows()]
    sales = {d["date"]: d["sales"] for d in days}
    now, before = sum(sales[d] for d in last), sum(sales[d] for d in previous)
    pct = pct_change(now, before)
    avg_last = round(now / len(last)) if last else 0
    avg_previous = round(before / len(previous)) if previous else 0
    if pct is None:
        takeaway = f"{NOT_ENOUGH}: {len(days)} selling days on record, {WINDOW * 2} are needed."
    else:
        takeaway = f"Sales are {moved(pct)}: {inr_short(now)} in the last {WINDOW} selling days against {inr_short(before)} in the {WINDOW} before."
    return {
        "days": days, "pct": pct, "direction": direction(pct), "avg_last": avg_last, "avg_previous": avg_previous,
        "takeaway": takeaway, "note": _low_day_note(sales, last, previous), "low_days": _low_days(sales),
    }


def _low_days(sales: dict[str, int]) -> list[str]:
    if not sales:
        return []
    usual = pd.Series(list(sales.values())).median()
    return [d for d, v in sorted(sales.items()) if v < usual * LOW_DAY_SHARE]


def _low_day_note(sales: dict[str, int], last: list[str], previous: list[str]) -> str:
    """One unusual day can decide the headline, so say so and show the comparison without it."""
    low = [d for d in _low_days(sales) if d in last or d in previous]
    if not low or not previous:
        return ""
    keep_last = [sales[d] for d in last if d not in low]
    keep_previous = [sales[d] for d in previous if d not in low]
    named = ", ".join(f"{day_date(d)} at {inr_short(sales[d])}" for d in low)
    if not keep_last or not keep_previous:
        return f"Low day: {named}."
    now, before = sum(keep_last) / len(keep_last), sum(keep_previous) / len(keep_previous)
    return f"Low day: {named}. Leaving it out, an average day is {moved(pct_change(now, before))}, {inr_short(now)} against {inr_short(before)}."


def routes(lines: pd.DataFrame, last: list[str], previous: list[str]) -> dict:
    counter_share = lines.groupby("route_id")["is_counter"].mean()
    rows = compare(lines, "route_id", last, previous)
    for r in rows:
        r["counter"] = bool(counter_share.get(r["key"], 0) > 0.5)
        r["label"] = r["key"]
    ranked = [r for r in rows if r["pct"] is not None]
    thin = [r for r in rows if r["pct"] is None]
    rises = sum(r["direction"] == "up" for r in ranked)
    falls = sum(r["direction"] == "down" for r in ranked)
    rise, fall = _extremes(ranked)
    if not ranked:
        takeaway = NOT_ENOUGH
    else:
        bits = [f"{r['label']} is {moved(r['pct'])}" for r in (rise, fall) if r]
        takeaway = f"{' and '.join(bits) or 'No route moved much'}. {rises} of {len(ranked)} routes rose, {falls} fell."
    return {"rows": ranked, "thin": thin, "min_base": MIN_BASE_INR, "takeaway": takeaway}


def products(lines: pd.DataFrame, last: list[str], previous: list[str]) -> dict:
    names = lines.drop_duplicates("product_id").set_index("product_id")["product_name"]
    rows = compare(lines, "product_id", last, previous, MIN_PRODUCT_BASE_INR, MIN_PRODUCT_DAYS)
    for r in rows:
        r["label"] = str(names.get(r["key"], r["key"]))
    rising = [r for r in rows if r["direction"] == "up"][:TOP]
    falling = [r for r in rows if r["direction"] == "down"][-TOP:][::-1]
    left_out = sum(r["pct"] is None for r in rows)
    return {
        "rising": rising, "falling": falling, "left_out": left_out, "min_base": MIN_PRODUCT_BASE_INR, "min_days": MIN_PRODUCT_DAYS,
        "takeaway": _best_and_worst(rows),
    }


def mix(lines: pd.DataFrame, last: list[str], previous: list[str], column: str, namer) -> dict:
    rows = compare(lines, column, last, previous)
    for r in rows:
        r["label"] = namer(r["key"])
    return {"rows": sorted(rows, key=lambda r: -r["now"]), "takeaway": _best_and_worst(rows)}


def quiet_shops(lines: pd.DataFrame, days: list[str], quiet: int = QUIET_DAYS, regular: int = REGULAR_BILL_DAYS) -> dict:
    """Regular buyers with no bill in the last `quiet` selling days, and what they used to buy in a week."""
    recent = set(days[-quiet:])
    earlier = days[:-quiet]
    rows = []
    if earlier:
        for shop, own in lines.groupby("retailer_id"):
            billed = set(own["invoice_date"])
            if billed & recent or len(billed) < regular:
                continue
            latest = own.sort_values("invoice_date").iloc[-1]
            rows.append({
                "shop": str(shop), "type": TYPE_NAMES.get(latest["retailer_type_hint"], str(latest["retailer_type_hint"]).capitalize()),
                "route": str(latest["route_id"]), "last_bill": str(latest["invoice_date"]), "last_bill_label": short_date(str(latest["invoice_date"])),
                "bill_days": len(billed), "weekly_inr": round(float(own["net_amount"].sum()) / len(earlier) * WINDOW),
            })
    rows.sort(key=lambda r: (-r["weekly_inr"], r["shop"]))
    value = sum(r["weekly_inr"] for r in rows)
    if not earlier:
        takeaway = NOT_ENOUGH
    elif rows:
        shops = "1 regular shop has" if len(rows) == 1 else f"{len(rows)} regular shops have"
        takeaway = f"{shops} no bill in the last {quiet} selling days. About {inr_short(value)} a week at their earlier rate, an estimate."
    else:
        takeaway = f"No regular shop has gone {quiet} selling days without a bill."
    return {"rows": rows, "value_inr": value, "quiet_days": quiet, "regular_days": regular, "window": WINDOW, "takeaway": takeaway}


def product_gap(lines: pd.DataFrame, days: list[str], product: str, label: str) -> dict:
    """Bills for one product by selling day, and how many selling days have passed since the last one."""
    billed = lines[lines["product_id"] == product].groupby("invoice_date")["net_amount"].sum()
    rows = [{"date": d, "label": day_date(d), "billed_inr": round(float(billed.get(d, 0.0)))} for d in days]
    silent = 0
    for row in reversed(rows):
        if row["billed_inr"] > 0:
            break
        silent += 1
    total = sum(r["billed_inr"] for r in rows)
    last_bill = max((r["date"] for r in rows if r["billed_inr"] > 0), default=None)
    if not last_bill:
        takeaway = f"{label} has no bills on record."
    elif silent:
        day_word = "selling day" if silent == 1 else "selling days"
        takeaway = f"{label} was last billed on {short_date(last_bill)}, then nothing for {silent} {day_word}. It billed {inr_short(total)} before that."
    else:
        takeaway = f"{label} billed {inr_short(total)}, the latest on {short_date(last_bill)}."
    return {"product": product, "label": label, "days": rows, "silent_days": silent, "total_inr": total, "last_bill": last_bill, "takeaway": takeaway}


def _fill_label(fill: float, target: float) -> str:
    """A load under target never reads as the target, the same rule as the Trucks screen."""
    shown = round(fill * 100)
    if fill < target and shown >= target * 100:
        shown = int(fill * 100)
    return f"{shown}%"


def truck_load(board: dict) -> dict:
    target = board["target"]
    rows = [{"truck": t["truck"], "fill": t["fill"], "fill_label": _fill_label(t["fill"], target), "kg": round(t["kg"]), "stops": t["stops"], "at_target": t["fill"] >= target} for t in board["trucks"]]
    goal = f"{round(target * 100)}%"
    if not rows:
        takeaway = "No trucks on the board yet."
    else:
        low = min(rows, key=lambda r: r["fill"])
        hit = sum(r["at_target"] for r in rows)
        count = f"{hit} of {len(rows)} trucks reach" if hit != 1 else f"1 of {len(rows)} trucks reaches"
        takeaway = f"{count} {goal}. {low['truck']} is lowest at {low['fill_label']}."
    return {"rows": rows, "target": target, "target_label": goal, "takeaway": takeaway}


def from_lines(lines: pd.DataFrame, as_of: str, distributor: str, gap_product: str, gap_label: str) -> dict:
    """Everything that comes from invoices. Pure, so a test can hand it any rows."""
    days = selling_days(lines)
    last, previous = split_windows(days)
    return {
        "distributor": distributor,
        "records_to": as_of,
        "records_to_label": short_date(as_of),
        "selling_days": len(days),
        "windows": {
            "size": WINDOW,
            "last": {"days": last, "label": date_range(last)},
            "previous": {"days": previous, "label": date_range(previous)},
        },
        "tiles": tiles(lines, last, previous),
        "trend": trend(lines, last, previous),
        "routes": routes(lines, last, previous),
        "products": products(lines, last, previous),
        "categories": mix(lines, last, previous, "group", lambda k: k.capitalize()),
        "retailer_types": mix(lines, last, previous, "retailer_type_hint", lambda k: TYPE_NAMES.get(k, k.capitalize())),
        "quiet_shops": quiet_shops(lines, days),
        "gap": product_gap(lines, days, gap_product, gap_label),
    }


@lru_cache(maxsize=4)
def _invoice_view(as_of: str, distributor: str) -> dict:
    """Invoices do not change while the app runs, so this is worked out once."""
    return from_lines(Ledger(as_of, distributor).lines(), as_of, distributor, state.BUTTER, state.butter_chart()["label"])


def snapshot(run: str | None) -> dict:
    return {**_invoice_view(DEMO["plan_evening"], DEMO["distributor"]), "trucks": truck_load(state.board(run))}


@router.get("/analysis")
def get_analysis(run: str | None = None) -> dict:
    found = store.rows("select id from runs order by created desc limit 1")
    return snapshot(run or (found[0]["id"] if found else None))
