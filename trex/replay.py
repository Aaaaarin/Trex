"""Replay October day by day with no future data, and score it. Run: python -m trex.replay

Capacity, cost and stock are stand-ins, so these are simulation results, not measured savings.
"""
import json
import sys

import pandas as pd

from .config import ROOT, standin
from .ledger import Ledger, _all_lines
from .planning import Night


def selling_days(distributor: str) -> list[str]:
    d = _all_lines()
    return sorted(d[d["distributor"] == distributor]["invoice_date"].unique())


def gaps_as_seen(distributor: str = "A", min_gap: int = 3, min_share: float = 0.5) -> dict:
    """Flag a regular product the evening it reaches min_gap selling days with no bills, using only past data."""
    days = selling_days(distributor)
    flagged, actual_ends = [], []
    d = _all_lines()
    d = d[d["distributor"] == distributor]
    for product, h in d.groupby("product_id"):
        sold = sorted(h["invoice_date"].unique())
        if len(sold) < min_share * len(days):
            continue
        index = {x: i for i, x in enumerate(days)}
        positions = [index[x] for x in sold]
        for a, b in zip(positions, positions[1:]):
            gap = b - a - 1
            if gap >= min_gap:
                before = h[h["invoice_date"] <= days[a]]
                rate = before["net_amount"].sum() / max(a + 1, 1)
                flagged.append(
                    {
                        "product": product,
                        "name": h["product_name"].iloc[0],
                        "flag_evening": days[a + min_gap],
                        "gap_selling_days": gap,
                        "selling_days_left_after_flag": gap - min_gap,
                        "value_at_normal_rate_inr": round(rate * gap),
                        "value_after_flag_inr": round(rate * (gap - min_gap)),
                    }
                )
    flagged.sort(key=lambda g: -g["value_at_normal_rate_inr"])
    return {
        "gaps": len(flagged),
        "value_at_normal_rate_inr": sum(g["value_at_normal_rate_inr"] for g in flagged),
        "value_after_flag_inr": sum(g["value_after_flag_inr"] for g in flagged),
        "top": flagged[:8],
    }


def truck_days(distributor: str = "A", start: str = "2026-10-05", end: str = "2026-10-31") -> dict:
    """Actual load against stand-in capacity, and what offers could add under the same limits."""
    days = selling_days(distributor)
    target = standin("fill_target")
    actual_ok = planned_ok = total = 0
    rows = []
    for i, day in enumerate(days):
        if i == 0 or day < start or day > end:
            continue
        evening = days[i - 1]
        try:
            night = Night(evening, day, distributor)
        except ValueError:
            continue
        for t in night.truck_summary():
            if t["truck"] not in standin("vehicle_capacity_kg"):
                continue
            total += 1
            with_offers = t["kg"] + sum(o["kg"] for o in night.offers(t["truck"], limit=20))
            reach = min(1.0, with_offers / t["capacity_kg"])
            actual_ok += t["fill"] >= target
            planned_ok += reach >= target
            rows.append({"date": day, "truck": t["truck"], "fill": t["fill"], "reachable_fill": round(reach, 3)})
    return {"truck_days": total, "at_target_as_billed": int(actual_ok), "could_reach_target_with_offers": int(planned_ok), "rows": rows, "note": "Reachable assumes every offered retailer says yes, so it is an upper limit"}


def stock_range_check(distributor: str = "A", evening: str = "2026-10-15", horizon_days: int = 7) -> dict:
    """How often a retailer predicted to run low within the horizon actually billed again."""
    from .belief import estimate

    ledger = Ledger(evening, distributor)
    d = ledger.lines()
    d = d[~d["is_counter"]]
    pairs = d.groupby(["retailer_id", "product_id"])["invoice_id"].nunique()
    pairs = pairs[pairs >= 3].sort_values(ascending=False).head(400)
    end = (pd.Timestamp(evening) + pd.Timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    later = ledger._future(end)
    bought = set(zip(later["retailer_id"], later["product_id"]))
    hit = miss = false_low = quiet = 0
    for retailer, product in pairs.index:
        b = estimate(ledger, retailer, product)
        low_soon = b.low_days <= horizon_days
        real = (retailer, product) in bought
        if low_soon and real:
            hit += 1
        elif low_soon and not real:
            false_low += 1
        elif real:
            miss += 1
        else:
            quiet += 1
    # Baseline: everything is bought weekly.
    baseline_right = sum((r, p) in bought for r, p in pairs.index)
    n = max(1, len(pairs))
    return {"pairs": int(len(pairs)), "predicted_and_bought": hit, "predicted_not_bought": false_low, "not_predicted_but_bought": miss, "not_predicted_not_bought": quiet, "accuracy": round((hit + quiet) / n, 3), "baseline_everyone_buys_weekly": round(baseline_right / n, 3)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    out = {"gaps": gaps_as_seen(), "trucks": truck_days(), "stock_ranges": stock_range_check()}
    path = ROOT / "data" / "replay_results.json"
    path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    brief = {k: v for k, v in out["trucks"].items() if k != "rows"}
    print(json.dumps({"gaps": {k: v for k, v in out["gaps"].items() if k != "top"}, "trucks": brief, "stock_ranges": out["stock_ranges"]}, indent=1))


if __name__ == "__main__":
    main()
