"""Build the night's plan and price each truck decision in rupees.

A lost sale is never reduced by a later bill: a customer who walked away does not come back.
"""
import math

import pandas as pd

from .belief import estimate
from .config import standin
from .ledger import Ledger
from .solver import Line, assign_batches, solve
from .standins import stock_by_product, stock_position


MAX_OFFER_KG = 90


def _lines_from_orders(orders: pd.DataFrame) -> list[Line]:
    g = orders.groupby(["vehicle_id", "route_id", "retailer_id", "retailer_type_hint", "product_id", "product_name"], as_index=False).agg(
        pieces=("pieces", "sum"), kg=("kg", "sum"), net=("net_amount", "sum"), ref=("evidence_ref", "first")
    )
    lines = []
    for r in g.itertuples():
        pieces = max(1, math.ceil(r.pieces))
        lines.append(Line(f"{r.vehicle_id}|{r.retailer_id}|{r.product_id}|order", r.vehicle_id, r.route_id, r.retailer_id, r.retailer_type_hint, r.product_id, r.product_name, pieces, r.kg / max(r.pieces, 0.1), r.net / max(r.pieces, 0.1), "order proxy", r.ref))
    return lines


class Night:
    """One evening's decision: every truck, line and stock position as of that evening."""

    def __init__(self, evening: str, plan_date: str, distributor: str = "A", extras: list[dict] | None = None, merges: dict | None = None, held: set | None = None, locked: dict | None = None, locked_batches: dict | None = None, reserved: dict | None = None):
        self.ledger = Ledger(evening, distributor)
        self.plan_date = plan_date
        self.orders = self.ledger.orders_for(plan_date)
        self.stock_table = stock_position(self.ledger, plan_date)
        self.stock = stock_by_product(self.stock_table)
        self.capacity = standin("vehicle_capacity_kg")
        self.lines = _lines_from_orders(self.orders)
        self.held = held or set()
        self.locked = locked or {}
        self.locked_batches = locked_batches or {}
        self.reserved = reserved or {}
        for e in extras or []:
            self.add_extra(e)
        for ln in self.lines:
            if merges and ln.truck in merges:
                ln.truck = merges[ln.truck]

    def _product(self, product_id: str) -> dict:
        p = self.ledger.products()
        return p[p["product_id"] == product_id].iloc[0].to_dict()

    def add_extra(self, e: dict) -> None:
        p = self._product(e["product"])
        value = float(p["mrp"]) * 0.87 * 1.05
        self.lines.append(Line(f"{e['truck']}|{e['retailer']}|{e['product']}|{e['source']}", e["truck"], e["route"], e["retailer"], e["retailer_type"], e["product"], p["product_name"], int(e["pieces"]), float(p["pack_kg"] or 0), value, e["source"], e.get("evidence", "")))

    def solved(self) -> dict:
        """Allocate only what is still open. Frozen plans keep their exact quantities, and held trucks get none."""
        allocated: dict[str, int] = {}
        left = dict(self.stock)
        open_lines = []
        for ln in self.lines:
            if ln.truck in self.held:
                allocated[ln.key] = 0
            elif ln.truck in self.locked:
                pieces = self.locked[ln.truck].get(ln.key, 0)
                allocated[ln.key] = pieces
                left[ln.product] = left.get(ln.product, 0) - pieces
            else:
                open_lines.append(ln)
        result = solve(open_lines, left, self.capacity)
        allocated.update(result["allocated"])
        return {"allocated": allocated, "method": result["method"]}

    def truck_summary(self, result: dict | None = None) -> list[dict]:
        result = result or self.solved()
        out = []
        for truck in sorted({ln.truck for ln in self.lines}):
            lines = [ln for ln in self.lines if ln.truck == truck]
            kg = sum(result["allocated"][ln.key] * ln.kg_per_piece for ln in lines)
            short = [ln for ln in lines if result["allocated"][ln.key] < ln.pieces]
            short_value = sum((ln.pieces - result["allocated"][ln.key]) * ln.value_per_piece for ln in lines)
            out.append(
                {
                    "truck": truck,
                    "routes": sorted({ln.route for ln in lines}),
                    "stops": len({ln.retailer for ln in lines}),
                    "kg": round(kg),
                    "capacity_kg": self.capacity[truck],
                    "fill": round(kg / self.capacity[truck], 3),
                    "partial_lines": len(short),
                    "unserved_value_inr": round(short_value),
                }
            )
        return out

    def load_sheet(self, truck: str, result: dict | None = None) -> list[dict]:
        result = result or self.solved()
        rows = [
            {"key": ln.key, "retailer": ln.retailer, "retailer_type": ln.retailer_type, "route": ln.route, "product": ln.product, "product_name": ln.product_name, "ordered": ln.pieces, "pieces": result["allocated"][ln.key], "kg_each": ln.kg_per_piece, "source": ln.source, "evidence": ln.evidence}
            for ln in self.lines
            if ln.truck == truck and result["allocated"][ln.key] > 0
        ]
        batches = []
        for b in self.stock_table.to_dict("records"):
            b = dict(b)
            b["pieces"] = b["pieces"] - self.locked_batches.get(f"{b['product_id']}|{b['batch_id']}", 0)
            batches.append(b)
        return assign_batches(rows, batches)

    def expiry_risk(self, truck: str) -> dict:
        """Stock on this truck's products that cannot sell in time at the rate seen so far."""
        products = {ln.product for ln in self.lines if ln.truck == truck}
        d = self.ledger.lines()
        days_seen = max(1, d["invoice_date"].nunique())
        rate = d.groupby("product_id")["pieces"].sum() / days_seen
        price = d.groupby("product_id")["mrp"].median() * 0.87
        at_risk = []
        for b in self.stock_table[self.stock_table["product_id"].isin(products)].itertuples():
            per_day = float(rate.get(b.product_id, 0))
            if per_day <= 0:
                continue
            needed = b.pieces / per_day
            if needed > b.days_left and not b.suspect_batch_date:
                unsold = b.pieces - per_day * max(0, b.days_left)
                at_risk.append({"product": b.product_id, "product_name": b.product_name, "batch": b.batch_id, "pieces": int(b.pieces), "days_left": int(b.days_left), "days_needed": round(needed, 1), "value_inr": round(unsold * float(price.get(b.product_id, 0)))})
        return {"total_inr": sum(a["value_inr"] for a in at_risk), "batches": sorted(at_risk, key=lambda a: -a["value_inr"])}

    def _at_risk_products(self, truck: str) -> dict[str, dict]:
        risk: dict[str, dict] = {}
        for b in self.expiry_risk(truck)["batches"]:
            risk.setdefault(b["product"], b)
        return risk

    def free_stock(self) -> dict[str, int]:
        """Stock not yet promised to anyone tonight. An offer must never sell what is already promised."""
        promised: dict[str, int] = {}
        for ln in self.lines:
            if ln.truck not in self.held:
                promised[ln.product] = promised.get(ln.product, 0) + ln.pieces
        return {p: q - promised.get(p, 0) - self.reserved.get(p, 0) for p, q in self.stock.items()}

    def offers(self, truck: str, limit: int = 12, already_asked: set[str] | None = None) -> list[dict]:
        """Who on this truck's routes could take more. Ageing stock first, then the biggest regular buyers.

        Each shop is asked once, and offers never exceed unpromised stock.
        """
        already_asked = already_asked or set()
        free = self.free_stock()
        min_share = standin("min_life_share")
        usable: dict[tuple[str, str], int] = {}
        for b in self.stock_table.itertuples():
            for kind, share in min_share.items():
                if b.share_left >= share and b.days_left > 0:
                    left = int(b.pieces) - self.locked_batches.get(f"{b.product_id}|{b.batch_id}", 0)
                    usable[(b.product_id, kind)] = usable.get((b.product_id, kind), 0) + max(0, left)
        routes = {ln.route for ln in self.lines if ln.truck == truck}
        ordering = {(ln.retailer, ln.product) for ln in self.lines}
        risk = self._at_risk_products(truck)
        d = self.ledger.lines()
        d = d[d["route_id"].isin(routes) & (~d["is_counter"])]
        pair = d.groupby(["retailer_id", "retailer_type_hint", "route_id", "product_id", "product_name", "case_pack"], as_index=False, dropna=False).agg(bills=("invoice_id", "nunique"), kg=("kg", "sum"), pieces=("pieces", "sum"), net=("net_amount", "sum"), ref=("evidence_ref", "last"))
        pair = pair[pair["kg"] > 0]
        pair["kg_per_bill"] = pair["kg"] / pair["bills"]
        if pair.empty:
            return []
        pair = pair.loc[pd.Series([(r, p) not in ordering for r, p in zip(pair["retailer_id"], pair["product_id"])], index=pair.index)]
        if pair.empty:
            return []
        pair = pair[pair["product_id"].map(lambda p: free.get(p, 0) > 0) & ~pair["retailer_id"].isin(already_asked)]
        if pair.empty:
            return []
        pair["ageing"] = pair["product_id"].isin(risk)
        pair["score"] = pair["kg_per_bill"].clip(upper=MAX_OFFER_KG) * (pair["bills"] >= 2).map({True: 1.0, False: 0.4}) * pair["ageing"].map({True: 1.5, False: 1.0})
        # Up to two ageing offers go first, if the stock still has days left to sell.
        sellable = {p for p, b in risk.items() if b["days_left"] >= 1}
        pair["ageing_first"] = pair["product_id"].isin(sellable)
        top_ageing = pair[pair["ageing_first"]].sort_values("score", ascending=False).drop_duplicates("retailer_id").head(2).index
        pair["rank"] = 0
        pair.loc[top_ageing, "rank"] = 1
        pair = pair.sort_values(["rank", "score"], ascending=False)
        offers, seen = [], set()
        max_off = standin("max_discount_inr_per_pack")
        for r in pair.itertuples():
            if r.retailer_id in seen:
                continue
            belief = estimate(self.ledger, r.retailer_id, r.product_id)
            if belief.low_days > 3:
                continue
            scale = min(1.0, MAX_OFFER_KG / max(r.kg_per_bill, 0.1))  # never ask one shop to fill a truck
            pack = max(int(r.case_pack), 1)
            pieces = max(1, math.ceil(r.pieces / r.bills * scale))
            cases = max(1, round(pieces / pack))
            pieces = cases * pack if pack > 1 else pieces
            room = min(free.get(r.product_id, 0), usable.get((r.product_id, r.retailer_type_hint), 0))
            if pieces > room:
                cases = room // pack
                pieces = cases * pack
                if pieces <= 0:
                    continue
            free[r.product_id] -= pieces
            usable[(r.product_id, r.retailer_type_hint)] -= pieces
            seen.add(r.retailer_id)
            kg_each = r.kg / max(r.pieces, 0.1)
            offers.append({"retailer": r.retailer_id, "retailer_type": r.retailer_type_hint, "route": r.route_id, "product": r.product_id, "product_name": r.product_name, "pieces": pieces, "cases": cases if pack > 1 else None, "pieces_per_case": pack, "kg": round(pieces * kg_each, 1), "value_inr": round(pieces * r.net / max(r.pieces, 0.1)), "ageing_stock": bool(r.ageing), "discount_inr_per_piece": max_off if r.ageing else 0, "days_left": risk[r.product_id]["days_left"] if r.ageing else None, "cover_low_days": belief.low_days, "cover_high_days": belief.high_days, "confidence": belief.confidence, "bills": int(r.bills), "why": ("Ageing batch. " if r.ageing else "") + belief.note, "evidence": r.ref})
            if len(offers) >= limit:
                break
        return offers

    def price_options(self, truck: str) -> dict:
        target = standin("fill_target")
        margin = standin("margin_rate")
        loss = standin("lost_sale_chance")
        merge_extra = standin("merge_extra_cost_inr")
        per_tonne = standin("trip_cost_inr_per_tonne_capacity")
        result = self.solved()
        summary = {t["truck"]: t for t in self.truck_summary(result)}
        me = summary[truck]
        trip = self.capacity[truck] / 1000 * per_tonne
        waste = lambda fill: round(trip * max(0.0, target - fill) / target)
        lines = [ln for ln in self.lines if ln.truck == truck]
        value = sum(result["allocated"][ln.key] * ln.value_per_piece for ln in lines)
        short_margin = round(sum((ln.pieces - result["allocated"][ln.key]) * ln.value_per_piece * margin * loss.get(ln.retailer_type, 0.6) for ln in lines))
        hold_margin = round(sum(result["allocated"][ln.key] * ln.value_per_piece * margin * loss.get(ln.retailer_type, 0.6) for ln in lines))
        max_asks = standin("max_questions_per_truck")
        offers = self.offers(truck, limit=max_asks)
        gap_kg = max(0.0, target * me["capacity_kg"] - me["kg"])
        reachable = sum(o["kg"] for o in offers) >= gap_kg
        chosen, added = [], 0.0
        for o in offers:
            if added >= gap_kg:
                break
            chosen.append(o)
            added += o["kg"]
        accept = standin("offer_acceptance_rate")
        sell_fill = min(1.0, (me["kg"] + added * accept) / me["capacity_kg"])
        extra_value = sum(o["value_inr"] for o in chosen) * accept
        discounts = round(sum(o["pieces"] * o["discount_inr_per_piece"] for o in chosen) * accept)
        # Expiry cost here is only what this decision changes: ageing stock these offers would have moved.
        moved_ageing = sum(o["value_inr"] for o in chosen if o["ageing_stock"])
        one_day_ageing = sum(b["value_inr"] for b in self.expiry_risk(truck)["batches"]) / 30
        expiry = round(moved_ageing)
        expiry_hold = round(moved_ageing + one_day_ageing)

        # A merge also fills the host, so the empty space it saves on the host counts in its favour.
        def waste_of(t: dict, fill: float) -> float:
            return t["capacity_kg"] / 1000 * per_tonne * max(0.0, target - fill) / target

        def merge_cost(t: dict) -> float:
            return merge_extra + waste_of(t, (t["kg"] + me["kg"]) / t["capacity_kg"]) - waste_of(t, t["fill"])

        others = [t for t in summary.values() if t["truck"] != truck and t["truck"] not in self.held and t["truck"] not in self.locked and (t["kg"] + me["kg"]) <= t["capacity_kg"]]
        merge = None
        if others:
            host = min(others, key=merge_cost)
            merged_fill = (host["kg"] + me["kg"]) / host["capacity_kg"]
            late_margin = round(hold_margin * 0.25)
            saved = round(waste_of(host, host["fill"]) - waste_of(host, merged_fill))
            merge = {"name": "merge", "label": f"Merge into {host['truck']}", "with_truck": host["truck"], "load": round(merged_fill, 3), "truck_cost_inr": round(merge_cost(host)), "margin_lost_inr": late_margin + short_margin, "expiry_risk_inr": expiry, "discounts_inr": 0, "extra_margin_inr": 0, "note": f"One truck fewer. Saves about Rs {saved} of empty space on {host['truck']}. A quarter of stops arrive late. Needs a person's approval"}

        options = [
            {"name": "go", "label": "Go now", "load": me["fill"], "truck_cost_inr": waste(me["fill"]), "margin_lost_inr": short_margin, "expiry_risk_inr": expiry, "discounts_inr": 0, "extra_margin_inr": 0, "note": "Needs a person's approval, as it is under target" if me["fill"] < target else "Truck is full enough"},
            {"name": "sell_to_fill", "label": "Sell to fill", "load": round(sell_fill, 3), "truck_cost_inr": waste(sell_fill), "margin_lost_inr": short_margin, "expiry_risk_inr": 0, "discounts_inr": discounts, "extra_margin_inr": round(extra_value * margin), "note": f"Ask {len(chosen)} retailers, expect about {round(added * accept)} of {round(added)} kg", "offers": chosen},
            {"name": "hold", "label": "Hold one day", "load": me["fill"], "truck_cost_inr": 0, "margin_lost_inr": hold_margin, "expiry_risk_inr": expiry_hold, "discounts_inr": 0, "extra_margin_inr": 0, "note": "All of tomorrow's sales wait. Needs a person's approval"},
        ]
        if merge:
            options.insert(2, merge)
        if not reachable:
            options = [o for o in options if o["name"] != "sell_to_fill"]
        for o in options:
            o["net_cost_inr"] = o["truck_cost_inr"] + o["margin_lost_inr"] + o["expiry_risk_inr"] + o["discounts_inr"] - o["extra_margin_inr"]
        best = min(options, key=lambda o: o["net_cost_inr"])
        return {"truck": truck, "fill": me["fill"], "kg": me["kg"], "capacity_kg": me["capacity_kg"], "stops": me["stops"], "needs_kg_for_target": round(gap_kg), "target_reachable_by_offers": reachable, "route_value_inr": round(value), "options": options, "recommended": best["name"], "solver": result["method"], "standins": ["capacity", "trip cost", "margin", "lost sale chance", "shelf life", "stock", "orders are next-day bills"]}
