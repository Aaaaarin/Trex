"""What the agent can do. Quantities come only from the solver, and every send passes the source check.

Dispatch tools read one frozen plan version. Nothing here can change an issued plan.
"""
import hashlib
import json
import time

import pandas as pd

from . import channels, dispatch, gate, orders_api, store, upstream
from .belief import estimate
from .config import standin
from .ledger import Ledger, merge_aliases
from .planning import Night

_CLAIMS = {
    "type": "array",
    "description": "One entry per number in the text. value is the number as written, evidence_ids are ids returned by earlier tools.",
    "items": {
        "type": "object",
        "properties": {"value": {"type": "number"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["value", "evidence_ids"],
        "additionalProperties": False,
    },
}


def _tool(name: str, description: str, props: dict | None = None) -> dict:
    props = props or {}
    return {"name": name, "description": description, "strict": True, "input_schema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}


_S = {"type": "string"}
_I = {"type": "integer"}
TOOLS = [
    _tool("find_records", "Resolve messy words, Hinglish and misspellings to known retailers and products. Use before acting on any name.", {"text": _S}),
    _tool("truck_board", "Tomorrow's trucks with load, stops and shortages, recalculated from the latest orders and accepted offers."),
    _tool("billing_gap", "Real invoice history for a product: last bill, selling days with no bills, earlier buyers, and whether the other distributor kept selling.", {"product": _S}),
    _tool("trace_upstream", "Follow a shortage from distributor to branch, transit, open purchase order, vehicle and plant. Stand-in data, and say so.", {"product": _S}),
    _tool("match_vehicle", "Find a reported vehicle of the right type for the open purchase order on a product. Stand-in data.", {"product": _S}),
    _tool("retailer_stock", "Estimated days of stock left for one retailer and product, as a range with its evidence. Never treat it as a fact.", {"retailer": _S, "product": _S}),
    _tool("price_options", "Price go, sell to fill, merge and hold for one truck in rupees, from stand-in costs.", {"truck": _S}),
    _tool("plan_offers", "Who to ask to fill this truck. Each shop is asked once, offers fit unpromised stock, and only as many are asked as needed.", {"truck": _S}),
    _tool("add_request", "Add a retailer's request to the plan. evidence_id must be the message it came from. If it takes promised stock, it returns needs_decision with the exact scope to ask about.", {"retailer": _S, "product": _S, "pieces": _I, "evidence_id": _S}),
    _tool("send_offer", "Send one retailer an offer from plan_offers, in short natural Hinglish. Sent once. Reserves the stock until the reply or expiry. discount_inr_per_piece must not exceed what plan_offers allowed.", {"retailer": _S, "product": _S, "pieces": _I, "discount_inr_per_piece": _I, "text": _S, "claims": _CLAIMS}),
    _tool("accept_offer", "Record a retailer's yes. evidence_id must be that retailer's reply. Pieces cannot exceed the offer. If the plan is committed it becomes a proposed revision.", {"retailer": _S, "product": _S, "pieces": _I, "evidence_id": _S}),
    _tool("release_offer", "Release reserved stock after a no or an unclear reply. evidence_id is the reply.", {"retailer": _S, "product": _S, "reason": _S, "evidence_id": _S}),
    _tool("send_message", "Send a short message to a person. role is retailer, salesman, driver or approver.", {"role": _S, "recipient": _S, "text": _S, "claims": _CLAIMS}),
    _tool("request_decision", "Ask a person one exact question. kind is under_target, take_promised_stock, add_stop, hold, merge, discount or clarify. Fill truck, retailer, product and pieces exactly as the action needs them, using empty text or 0 where they do not apply. For under_target, pieces is the kg from preview_plan. For merge, detail is the host truck. Then end your turn.", {"kind": _S, "truck": _S, "retailer": _S, "product": _S, "pieces": _I, "detail": _S, "question": _S, "options": {"type": "array", "items": _S}, "claims": _CLAIMS}),
    _tool("preview_plan", "See exactly what would ship for this truck after batch checks, without freezing anything. Use this figure, not truck_board, before asking to go under target.", {"truck": _S}),
    _tool("freeze_plan", "Build and validate this truck's dispatch plan and freeze it as a numbered version. option is go or sell_to_fill. The validator refuses anything over stock or capacity, without valid batches, or under target with no exact approval.", {"truck": _S, "option": _S, "reason": _S}),
    _tool("hold_truck", "Hold a truck for a later day and tell its retailers. Needs an approved hold decision for this truck. Nothing else dispatches it afterwards.", {"truck": _S, "reason": _S}),
    _tool("merge_truck", "Move this truck's stops onto another truck. Needs an approved merge decision naming the host. Then freeze the host's plan.", {"truck": _S, "into": _S, "reason": _S}),
    _tool("write_orders", "Write the frozen plan's orders to the order system. Safe to call again: nothing is written twice. Lines that fail stay blocked from promises.", {"truck": _S}),
    _tool("issue_load_sheet", "Issue the frozen plan: tell the driver and retailers what is coming. Lines whose orders were not saved are not promised.", {"truck": _S}),
    _tool("record_drop", "Close one stop on an issued plan. evidence_id must be this truck's driver's message about this stop. status is delivered or failed.", {"truck": _S, "retailer": _S, "status": _S, "evidence_id": _S}),
    _tool("log_miss", "Record a shortage in the miss ledger. Use stage 'not established' if records cannot show it.", {"route": _S, "product": _S, "stage": _S, "held_by_role": _S, "evidence_ids": {"type": "array", "items": _S}}),
    _tool("report_to_lead", "Tell the dispatch lead something. This is the only way your words reach them.", {"text": _S, "claims": _CLAIMS}),
]


class Tools:
    def __init__(self, run: str, evening: str, plan_date: str, distributor: str = "A"):
        self.run, self.evening, self.plan_date, self.distributor = run, evening, plan_date, distributor
        self.ledger = Ledger(evening, distributor)
        self._cached: tuple[str, Night] | None = None

    def _extras(self) -> list[dict]:
        return store.get_kv(self.run, "extras", [])

    def night(self) -> Night:
        reserved = sorted((r["key"], r["pieces"]) for r in dispatch.open_reservations(self.run))
        plans = {t: (p["version"], p["status"]) for t, p in dispatch.all_current_plans(self.run).items()}
        statuses = store.rows("select key, value from kv where run=? and (key like 'truck_status:%' or key='merges')", (self.run,))
        sig = json.dumps([self._extras(), reserved, plans, statuses], sort_keys=True, default=str)
        if not self._cached or self._cached[0] != sig:
            self._cached = (sig, dispatch.night_for(self.run, self.evening, self.plan_date, self.distributor))
        return self._cached[1]

    def _standin_evidence(self, ref: str, summary: str, data) -> str:
        return store.add_evidence(self.run, "standin", ref, summary, data, standin=True)

    def _retailer(self, retailer: str) -> dict:
        rows = self.ledger.retailers()
        hit = rows[rows["retailer_id"] == retailer]
        if hit.empty:
            raise ValueError(f"Unknown retailer {retailer}. Use find_records")
        return hit.iloc[0].to_dict()

    def _truck_for_route(self, route: str) -> str:
        weights: dict[str, float] = {}
        for ln in self.night().lines:
            if ln.route == route:
                weights[ln.truck] = weights.get(ln.truck, 0) + ln.pieces * ln.kg_per_piece
        if not weights:
            raise ValueError(f"No truck serves route {route} tomorrow")
        return max(weights, key=weights.get)

    def _serving_route(self, retailer: str) -> str:
        """A shop can be billed on several routes. Use its most-billed route with a truck tomorrow."""
        d = self.ledger.lines()
        routes = d[d["retailer_id"] == retailer]["route_id"].value_counts().index.tolist()
        served = {ln.route for ln in self.night().lines}
        for route in routes:
            if route in served:
                return route
        raise ValueError(f"No truck serves {retailer} tomorrow on any of its routes: {routes}")

    def find_records(self, text: str) -> dict:
        known = merge_aliases().get(text.lower().strip())
        retailers = self.ledger.find_retailer(known or text)
        products = self.ledger.find_product(text)
        for p in products:
            p["evidence_id"] = store.add_evidence(self.run, "catalog", p["product_id"], p["product_name"], p)
        return {"retailers": retailers, "products": products, "note": "Retailer types are inferred from names and packs, not recorded"}

    def truck_board(self) -> dict:
        trucks = self.night().truck_summary()
        eid = self._standin_evidence(f"board:{dispatch.basis_of(self.run)}", "Truck loads, with capacity a stand-in", trucks)
        return {"trucks": trucks, "evidence_id": eid, "standins": ["capacity is each truck's own peak load", "orders are next-day bills"]}

    def billing_gap(self, product: str) -> dict:
        d = self.ledger.lines()
        days = sorted(d["invoice_date"].unique())
        p = d[d["product_id"] == product]
        if p.empty:
            return {"error": "No bills for this product"}
        by_day = p.groupby("invoice_date")["net_amount"].sum().round().to_dict()
        last = max(by_day)
        gap_days = [x for x in days if x > last]
        before = p[p["invoice_date"] <= last]
        other = Ledger(self.evening, "B" if self.distributor == "A" else "A").lines()
        other_sold = other[(other["product_name"] == p["product_name"].iloc[0]) & (other["invoice_date"].isin(gap_days))]["net_amount"].sum()
        data = {"product": product, "product_name": p["product_name"].iloc[0], "last_bill_date": last, "selling_days_without_bills": len(gap_days), "earlier_buyers": int(before["retailer_id"].nunique()), "billed_per_day_inr": by_day, "other_distributor_billed_in_gap_inr": round(float(other_sold)), "sample_lines": before.tail(3)["evidence_ref"].tolist(), "note": "Invoices cannot prove a stock-out"}
        data["evidence_id"] = store.add_evidence(self.run, "invoices", f"gap:{product}", f"Billing history of {product}", data)
        return data

    def trace_upstream(self, product: str) -> dict:
        t = upstream.trace(product)
        t["evidence_id"] = self._standin_evidence(f"trace:{product}", f"Upstream position of {product}, stand-in", t)
        t["standin"] = True
        return t

    def match_vehicle(self, product: str) -> dict:
        v = upstream.vehicles_for(product)
        data = {"vehicles": v, "po_about_to_expire": upstream.po_expiring(product), "standin": True}
        data["evidence_id"] = self._standin_evidence(f"vehicles:{product}", f"Vehicles for {product}, stand-in", data)
        return data

    def retailer_stock(self, retailer: str, product: str) -> dict:
        data = estimate(self.ledger, retailer, product).dict()
        data["evidence_id"] = store.add_evidence(self.run, "estimate", f"{retailer}:{product}", f"Stock estimate for {retailer}", data)
        return data

    def price_options(self, truck: str) -> dict:
        problem = self._not_open(truck)
        if problem:
            return {"error": problem}
        p = self.night().price_options(truck)
        p["evidence_id"] = self._standin_evidence(f"options:{truck}:{dispatch.basis_of(self.run)}", f"Priced options for {truck}", p)
        if store.get_kv(self.run, f"options:{truck}") is None:
            store.set_kv(self.run, f"options:{truck}", p)
        store.set_kv(self.run, f"options_latest:{truck}", p)
        store.set_kv(self.run, "focus", truck)
        if store.get_kv(self.run, f"start_fill:{truck}") is None:
            store.set_kv(self.run, f"start_fill:{truck}", p["fill"])
        return p

    def plan_offers(self, truck: str) -> dict:
        plan = dispatch.current_plan(self.run, truck)
        if plan and plan["status"] in dispatch.LOCKED:
            return {"error": f"{truck} v{plan['version']} is already frozen, so no more offers are needed"}
        night = self.night()
        me = next(t for t in night.truck_summary() if t["truck"] == truck)
        gap = max(0.0, standin("fill_target") * me["capacity_kg"] - me["kg"])
        asked = set(store.get_kv(self.run, f"offers:{truck}", {}))
        room = max(0, standin("max_questions_per_truck") - len(asked))
        candidates = night.offers(truck, limit=room, already_asked=asked) if room else []
        if sum(o["kg"] for o in candidates) < gap:
            store.log(self.run, "selling_stopped", truck=truck, needs_kg=round(gap), reachable_kg=round(sum(o["kg"] for o in candidates)))
            return {"truck": truck, "needs_kg": round(gap), "target_reachable": False, "offers": [], "note": "Even if every suitable shop said yes, this truck would not reach target within the limit on questions. Do not sell. Compare go under target, merge and hold with price_options, then ask a person."}
        chosen, kg = [], 0.0
        for o in candidates:
            if kg >= gap * 1.1:
                break
            chosen.append(o)
            kg += o["kg"]
        for o in chosen:
            o["evidence_id"] = store.add_evidence(self.run, "estimate", f"offer:{o['retailer']}:{o['product']}", f"Why offer {o['product']} to {o['retailer']}", o)
        data = {"truck": truck, "needs_kg": round(gap), "asking": len(chosen), "not_asking": len(candidates) - len(chosen), "offers": chosen, "max_discount_inr_per_pack": standin("max_discount_inr_per_pack"), "note": "Offer whole cases. Offer a discount only where discount_inr_per_piece is above zero. If nobody suitable is left, stop selling and price what remains: going under target can be right"}
        data["discount_evidence_id"] = self._standin_evidence("max_discount", "Allowed discount, stand-in", {"max_discount_inr_per_pack": standin("max_discount_inr_per_pack")})
        if channels.live_chat() and chosen and not store.get_kv(self.run, "live", {}).get("recipients"):
            store.set_kv(self.run, "live", {"recipients": [chosen[0]["retailer"]], "roles": ["approver", "driver"]})
        known = store.get_kv(self.run, f"offers:{truck}", {})
        known.update({o["retailer"]: o for o in chosen if o["retailer"] not in known})
        store.set_kv(self.run, f"offers:{truck}", known)
        return data

    def _own_routes(self, retailer: str) -> list[str]:
        d = self.ledger.lines()
        return d[d["retailer_id"] == retailer]["route_id"].value_counts().index.tolist()

    def _add_extra(self, retailer: str, product: str, pieces: int, source: str, evidence_id: str, route: str | None = None, needs_approval: bool = False, truck: str | None = None) -> dict:
        r = self._retailer(retailer)
        route = route or self._serving_route(retailer)
        truck = truck or self._truck_for_route(route)
        if dispatch.truck_status(self.run, truck) == "held":
            return {"error": f"{truck} is held, so nothing can be added to it"}
        plan = dispatch.current_plan(self.run, truck)
        change = {"truck": truck, "route": route, "retailer": retailer, "retailer_type": r["retailer_type_hint"], "product": product, "pieces": int(pieces), "source": source, "evidence": evidence_id, "needs_approval": needs_approval}
        if plan and plan["status"] in ("committed", "issued"):
            revisions = store.get_kv(self.run, f"revisions:{truck}", [])
            if not any(x["retailer"] == retailer and x["product"] == product for x in revisions):
                revisions.append({**change, "against_version": plan["version"], "ts": time.time()})
                store.set_kv(self.run, f"revisions:{truck}", revisions)
                store.log(self.run, "revision_proposed", truck=truck, version=plan["version"], retailer=retailer, product=product, pieces=int(pieces))
            return {"status": "proposed_revision", "truck": truck, "version": plan["version"], "applied": False, "note": f"Plan v{plan['version']} is committed and has not changed. Tell the lead about this proposed revision."}
        if plan and plan["status"] == "frozen":
            dispatch.set_plan_status(self.run, truck, plan["version"], "superseded")
            decided = store.get_kv(self.run, "decided", {})
            decided.pop(truck, None)
            store.set_kv(self.run, "decided", decided)
        extras = [e for e in self._extras() if not (e["retailer"] == retailer and e["product"] == product and e["source"] == source)]
        extras.append(change)
        store.set_kv(self.run, "extras", extras)
        me = next(t for t in self.night().truck_summary() if t["truck"] == truck)
        store.log(self.run, "load_changed", truck=truck, fill=me["fill"], kg=me["kg"], retailer=retailer, product=product, pieces=int(pieces), source=source)
        return {"truck": truck, "fill": me["fill"], "kg": me["kg"], "evidence_id": self._standin_evidence(f"load:{truck}:{dispatch.basis_of(self.run)}", f"Load of {truck}", me)}

    def _no_truck(self, retailer: str, product: str, pieces: int, routes: list[str]) -> dict:
        """Nothing runs on this shop's route tomorrow. A person picks a truck, since distance between routes is not on record."""
        room = [{"truck": t["truck"], "fill": t["fill"], "room_kg": t["capacity_kg"] - t["kg"]} for t in self.night().truck_summary() if not dispatch.truck_status(self.run, t["truck"]) and t["capacity_kg"] > t["kg"]]
        room.sort(key=lambda t: t["fill"])
        data = {"retailer": retailer, "product": product, "requested_pieces": int(pieces), "shop_routes": routes, "trucks_with_room": room, "standin": True}
        eid = self._standin_evidence(f"no_truck:{retailer}:{product}:{pieces}", f"No truck on {retailer}'s route tomorrow", data)
        store.log(self.run, "no_truck_on_route", retailer=retailer, product=product, routes=routes)
        return {"needs_decision": True, "reason": "No truck runs on this shop's route tomorrow, so nothing is promised yet. Distance between routes is not on record, so say so. Use request_decision with this scope and truck set to the truck you suggest, then end your turn. Call add_request again with the same quantity after approval.", "scope": {"kind": "add_stop", "retailer": retailer, "product": product, "pieces": int(pieces)}, "evidence_id": eid, **data}

    def add_request(self, retailer: str, product: str, pieces: int, evidence_id: str) -> dict:
        if store.get_evidence(evidence_id) is None:
            return {"error": "That evidence does not exist"}
        night = self.night()
        free = night.free_stock().get(product)
        if free is None:
            return {"error": f"{product} has no stock record, so it cannot be promised"}
        routes = self._own_routes(retailer)
        carrier = None
        if not {ln.route for ln in night.lines} & set(routes):
            approval = dispatch.find_approval(self.run, "add_stop", retailer=retailer, product=product, pieces=int(pieces))
            if not approval:
                return self._no_truck(retailer, product, pieces, routes)
            carrier = json.loads(approval["scope"])["truck"]
            problem = self._not_open(carrier)
            if problem:
                return {"error": f"The approved truck cannot take this stop. {problem}"}
        if pieces > free:
            data = {"product": product, "retailer": retailer, "unpromised_stock_pieces": free, "requested_pieces": int(pieces), "standin": True}
            eid = self._standin_evidence(f"scarcity:{retailer}:{product}:{pieces}", f"Stock against promises for {product}", data)
            if dispatch.find_approval(self.run, "take_promised_stock", retailer=retailer, product=product, pieces=int(pieces)):
                return self._add_extra(retailer, product, pieces, "request", evidence_id, routes[0] if carrier else None, True, carrier)
            return {"needs_decision": True, "reason": "This takes stock already promised to other retailers. Use request_decision with exactly this scope, then end your turn. Call add_request again with the same quantity after approval.", "scope": {"kind": "take_promised_stock", "retailer": retailer, "product": product, "pieces": int(pieces)}, "evidence_id": eid, **data}
        return self._add_extra(retailer, product, pieces, "request", evidence_id, routes[0] if carrier else None, False, carrier)

    def send_offer(self, retailer: str, product: str, pieces: int, discount_inr_per_piece: int, text: str, claims: list[dict]) -> dict:
        gate.check(self.run, text, claims)
        planned = None
        truck = None
        for row in store.rows("select key, value from kv where run=? and key like 'offers:%'", (self.run,)):
            found = json.loads(row["value"]).get(retailer)
            if found and found["product"] == product:
                planned, truck = found, row["key"].split(":", 1)[1]
        if planned is None:
            return {"error": "Only offers from plan_offers can be sent"}
        if pieces > planned["pieces"]:
            return {"error": f"The plan offers {planned['pieces']} pieces, not {pieces}"}
        if discount_inr_per_piece > planned["discount_inr_per_piece"]:
            return {"error": f"Allowed discount is {planned['discount_inr_per_piece']}. A larger one needs a person"}
        key = f"{self.run}:offer:{retailer}:{product}"
        if store.rows("select key from outbox where key=?", (key,)):
            return {"status": "already_sent", "key": key}
        free = self.night().free_stock().get(product, 0)
        if pieces > free:
            return {"error": f"Only {free} pieces are free now. Another offer or order holds the rest"}
        store.execute("insert or ignore into reservations(key, run, truck, retailer, product, pieces, status, ts, discount) values(?,?,?,?,?,?,?,?,?)", (key, self.run, truck, retailer, product, int(pieces), "open", time.time(), int(discount_inr_per_piece)))
        store.log(self.run, "stock_reserved", retailer=retailer, product=product, pieces=int(pieces))
        group = self.ledger.products().set_index("product_id").loc[product, "group"]
        return channels.send(self.run, "retailer", retailer, text, key, {"retailer": retailer, "product": product, "pieces": int(pieces), "group": group, "route": planned["route"], "discount": int(discount_inr_per_piece)})

    def accept_offer(self, retailer: str, product: str, pieces: int, evidence_id: str) -> dict:
        e = store.get_evidence(evidence_id)
        if e is None or e["kind"] != "message" or e["data"].get("sender") != retailer:
            return {"error": "evidence_id must be this retailer's own reply"}
        key = f"{self.run}:offer:{retailer}:{product}"
        sent = store.rows("select data from outbox where key=?", (key,))
        if not sent:
            return {"error": "No offer of this product was sent to this retailer"}
        offer = json.loads(sent[0]["data"])
        if pieces > offer["pieces"]:
            return {"error": f"Retailer accepted more than offered ({offer['pieces']}). Ask a person"}
        reservation = store.rows("select status from reservations where key=?", (key,))
        status = reservation[0]["status"] if reservation else None
        if status in ("declined", "accepted"):
            return {"error": f"This offer is already {status}"}
        store.execute("update reservations set status='accepted' where key=?", (key,))
        return self._add_extra(retailer, product, pieces, "offer", evidence_id, offer.get("route"))

    def release_offer(self, retailer: str, product: str, reason: str, evidence_id: str) -> dict:
        key = f"{self.run}:offer:{retailer}:{product}"
        if not store.rows("select key from reservations where key=?", (key,)):
            return {"error": "No reservation for this offer"}
        store.execute("update reservations set status='declined' where key=? and status='open'", (key,))
        store.log(self.run, "stock_released", retailer=retailer, product=product, reason=reason, evidence=evidence_id)
        return {"released": True}

    def send_message(self, role: str, recipient: str, text: str, claims: list[dict]) -> dict:
        gate.check(self.run, text, claims)
        key = f"{self.run}:msg:{recipient}:" + hashlib.sha256(text.encode()).hexdigest()[:12]
        return channels.send(self.run, role, recipient, text, key)

    def request_decision(self, kind: str, truck: str, retailer: str, product: str, pieces: int, detail: str, question: str, options: list[str], claims: list[dict]) -> dict:
        check = gate.check(self.run, question, claims)
        scope = {"kind": kind, "truck": truck, "retailer": retailer, "product": product, "pieces": int(pieces), "detail": detail, "basis": dispatch.basis_of(self.run, truck or None)}
        did = hashlib.sha256(json.dumps(scope, sort_keys=True).encode() + self.run.encode()).hexdigest()[:10]
        store.execute("insert or ignore into decisions(id, run, ts, question, options, evidence, scope) values(?,?,?,?,?,?,?)", (did, self.run, time.time(), question, json.dumps(options), json.dumps(claims), json.dumps(scope)))
        store.log(self.run, "decision_requested", id=did, question=question, options=options, scope=scope, used_standin=check["used_standin"])
        channels.send(self.run, "approver", "approver", question + "\nOptions: " + ", ".join(options), f"{self.run}:decision:{did}")
        return {"status": "waiting_for_person", "decision_id": did, "scope": scope, "instruction": "End your turn now. The answer will arrive as a new message. It applies only to this exact scope."}

    def preview_plan(self, truck: str) -> dict:
        plan = dispatch.build(self.run, self.night(), truck, "go", "preview")
        problems = dispatch.validate(self.run, plan, self.night())
        data = {"truck": truck, "kg": round(plan["kg"]), "fill": plan["fill"], "capacity_kg": plan["capacity_kg"], "lines": len(plan["lines"]), "unplaced": plan["unplaced"][:10], "validator_problems": problems[:10], "note": "For under_target, set pieces to this kg. The approval applies only to this exact load."}
        data["evidence_id"] = self._standin_evidence(f"preview:{truck}:{dispatch.basis_of(self.run, truck)}", f"Previewed load of {truck}", data)
        return data

    def freeze_plan(self, truck: str, option: str, reason: str) -> dict:
        existing = dispatch.current_plan(self.run, truck)
        if existing and existing["status"] in dispatch.LOCKED:
            return {"status": "already_frozen", "truck": truck, "version": existing["version"], "fill": existing["fill"], "kg": existing["kg"]}
        for r in store.rows("select key, retailer, product from reservations where run=? and truck=? and status='open'", (self.run, truck)):
            store.execute("update reservations set status='closed' where key=?", (r["key"],))
            store.log(self.run, "stock_released", retailer=r["retailer"], product=r["product"], reason="No reply before the plan froze")
        result = dispatch.freeze(self.run, self.night(), truck, option, reason)
        if result.get("status") == "frozen":
            decided = store.get_kv(self.run, "decided", {})
            decided[truck] = {"option": option, "reason": reason, "fill": result["fill"], "kg": result["kg"], "version": result["version"]}
            store.set_kv(self.run, "decided", decided)
            store.log(self.run, "truck_decided", truck=truck, option=option, reason=reason, fill=result["fill"], version=result["version"])
        return result

    def _not_open(self, truck: str) -> str | None:
        """A truck can only be priced, held or merged if it has stops tonight and has not been held or merged already."""
        status = dispatch.truck_status(self.run, truck)
        if status:
            return f"{truck} is already {status}"
        if truck not in {t["truck"] for t in self.night().truck_summary()}:
            return f"{truck} has no stops tonight"
        return None

    def hold_truck(self, truck: str, reason: str) -> dict:
        problem = self._not_open(truck)
        if problem:
            return {"error": problem}
        if truck in store.get_kv(self.run, "merges", {}).values():
            return {"error": f"{truck} is carrying a merged route, so holding it would strand those stops"}
        approval = dispatch.find_approval(self.run, "hold", truck=truck)
        if not approval:
            return {"error": f"Holding {truck} needs an approved hold decision for this truck"}
        plan = dispatch.current_plan(self.run, truck)
        if plan and plan["status"] in ("committed", "issued"):
            return {"error": f"{truck} v{plan['version']} is already committed. It cannot be held silently"}
        night = self.night()
        stops = sorted({ln.retailer for ln in night.lines if ln.truck == truck})
        store.set_kv(self.run, f"truck_status:{truck}", "held")
        decided = store.get_kv(self.run, "decided", {})
        decided[truck] = {"option": "hold", "reason": reason, "approval": approval["id"]}
        store.set_kv(self.run, "decided", decided)
        for retailer in stops:
            channels.send(self.run, "retailer", retailer, f"Kal gaadi {truck} nahi aayegi. Nayi date jaldi batayenge.", f"{self.run}:hold:{truck}:{retailer}")
        store.log(self.run, "truck_held", truck=truck, reason=reason, stops=len(stops))
        return {"status": "held", "truck": truck, "retailers_told": len(stops)}

    def merge_truck(self, truck: str, into: str, reason: str) -> dict:
        problem = self._not_open(truck) or self._not_open(into)
        if problem:
            return {"error": problem}
        if truck in store.get_kv(self.run, "merges", {}).values():
            return {"error": f"{truck} is already carrying a merged route, so it cannot merge away"}
        if not dispatch.find_approval(self.run, "merge", truck=truck, detail=into):
            return {"error": f"Merging {truck} into {into} needs an approved merge decision naming {into}"}
        for t in (truck, into):
            plan = dispatch.current_plan(self.run, t)
            if plan and plan["status"] in ("committed", "issued"):
                return {"error": f"{t} v{plan['version']} is already committed"}
        board = {t["truck"]: t for t in self.night().truck_summary()}
        if board[truck]["kg"] + board[into]["kg"] > board[into]["capacity_kg"]:
            return {"error": f"{into} cannot carry both loads"}
        stops = sorted({ln.retailer for ln in self.night().lines if ln.truck == truck})
        merges = store.get_kv(self.run, "merges", {})
        merges[truck] = into
        store.set_kv(self.run, "merges", merges)
        store.set_kv(self.run, f"truck_status:{truck}", "merged")
        if store.get_kv(self.run, "focus") == truck:
            store.set_kv(self.run, "focus", into)
        for t in (truck, into):
            plan = dispatch.current_plan(self.run, t)
            if plan and plan["status"] == "frozen":
                dispatch.set_plan_status(self.run, t, plan["version"], "superseded")
        decided = store.get_kv(self.run, "decided", {})
        decided[truck] = {"option": "merge", "into": into, "reason": reason}
        decided.pop(into, None)
        store.set_kv(self.run, "decided", decided)
        for retailer in stops:
            channels.send(self.run, "retailer", retailer, f"Kal aapka maal gaadi {into} se aayega, thoda late ho sakta hai.", f"{self.run}:merge:{truck}:{retailer}")
        store.log(self.run, "truck_merged", truck=truck, into=into, reason=reason, stops=len(stops))
        return {"status": "merged", "truck": truck, "into": into, "retailers_told": len(stops), "instruction": f"Freeze {into}'s plan next. Preview it first, as the merged load may still be under target"}

    def write_orders(self, truck: str) -> dict:
        plan, problem = dispatch.checked_plan(self.run, self.night(), truck)
        if problem:
            return {"error": problem}
        results = store.get_kv(self.run, f"order_results:{truck}:{plan['version']}", {})
        if plan["status"] == "frozen":
            dispatch.set_plan_status(self.run, truck, plan["version"], "committed")
        counts: dict[str, int] = {}
        for ln in plan["lines"]:
            key = f"{self.run}:{truck}:v{plan['version']}:{ln['retailer']}:{ln['product']}"
            if results.get(key) in dispatch.OK_WRITES:
                counts["already_saved"] = counts.get("already_saved", 0) + 1
                continue
            result = orders_api.write_safely(self.run, key, {"truck": truck, "version": plan["version"], "plan_hash": plan["hash"], "retailer": ln["retailer"], "product": ln["product"], "pieces": ln["pieces"], "batches": ln["batches"]})
            results[key] = result["status"]
            store.set_kv(self.run, f"order_results:{truck}:{plan['version']}", results)
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        blocked = [k.rsplit(":", 2)[1:] for k, v in results.items() if v not in dispatch.OK_WRITES]
        store.log(self.run, "orders_written", truck=truck, version=plan["version"], counts=counts, blocked=len(blocked))
        return {"truck": truck, "version": plan["version"], "counts": counts, "total_saved_in_system": orders_api.count(self.run), "lines": len(plan["lines"]), "blocked_lines": blocked, "note": "Blocked lines were not saved, so they will not be promised. Call write_orders again to retry." if blocked else ""}

    def issue_load_sheet(self, truck: str) -> dict:
        plan, problem = dispatch.checked_plan(self.run, self.night(), truck)
        if problem:
            return {"error": problem}
        results = store.get_kv(self.run, f"order_results:{truck}:{plan['version']}", {})
        if not results:
            return {"error": "Write this plan's orders first"}
        keyed = lambda ln: f"{self.run}:{truck}:v{plan['version']}:{ln['retailer']}:{ln['product']}"
        good = [ln for ln in plan["lines"] if results.get(keyed(ln)) in dispatch.OK_WRITES]
        blocked = [ln for ln in plan["lines"] if results.get(keyed(ln)) not in dispatch.OK_WRITES]
        for ln in blocked:
            store.log(self.run, "promise_blocked", truck=truck, retailer=ln["retailer"], product=ln["product"], pieces=ln["pieces"], reason="Order was not saved")
        if not good:
            return {"error": "No order was saved, so nothing can be promised"}
        sheet = [{**ln, "plan_version": plan["version"]} for ln in good]
        store.set_kv(self.run, f"sheet:{truck}", sheet)
        stops: dict[str, list[dict]] = {}
        for row in sheet:
            stops.setdefault(row["retailer"], []).append(row)
        for retailer, items in stops.items():
            short = [i for i in items if i["pieces"] < i["ordered"]]
            body = "; ".join(f"{i['product_name']}: {i['pieces']}" + (f" of {i['ordered']}" if i["pieces"] < i["ordered"] else "") for i in items[:6])
            text = f"Kal gaadi {truck} aa rahi hai. {body}." + (" Kuch maal kam hai, baaki agli gaadi se." if short else "")
            channels.send(self.run, "retailer", retailer, text, f"{self.run}:sheet:{truck}:v{plan['version']}:{retailer}")
        channels.send(self.run, "driver", f"driver-{truck}", f"Load sheet {truck} v{plan['version']}: {len(stops)} stops, {sum(i['pieces'] for i in sheet)} pieces. Trex will ask for a photo at each drop on the delivery day.", f"{self.run}:driver:{truck}:v{plan['version']}")
        dispatch.set_plan_status(self.run, truck, plan["version"], "issued")
        store.log(self.run, "load_sheet_issued", truck=truck, version=plan["version"], stops=len(stops), lines=len(sheet), partial=sum(1 for i in sheet if i["pieces"] < i["ordered"]), blocked=len(blocked))
        return {"truck": truck, "version": plan["version"], "stops": len(stops), "lines": len(sheet), "partial_lines": sum(1 for i in sheet if i["pieces"] < i["ordered"]), "blocked_lines": [[ln["retailer"], ln["product"]] for ln in blocked], "instruction": "End your turn. Driver confirmations arrive as new messages."}

    def record_drop(self, truck: str, retailer: str, status: str, evidence_id: str) -> dict:
        plan = dispatch.current_plan(self.run, truck)
        if not plan or plan["status"] != "issued":
            return {"error": f"{truck} has no issued plan, so no stop can close"}
        sheet = store.get_kv(self.run, f"sheet:{truck}", [])
        if retailer not in {row["retailer"] for row in sheet}:
            return {"error": f"{retailer} is not a promised stop on {truck} v{plan['version']}"}
        e = store.get_evidence(evidence_id)
        if e is None or e["kind"] != "message" or e["data"].get("role") != "driver":
            return {"error": "A drop closes only on the driver's own message. 'Dispatched' is not proof"}
        if e["data"].get("sender") != f"driver-{truck}":
            return {"error": f"This message came from {e['data'].get('sender')}, not {truck}'s driver"}
        about = e["data"].get("retailer")
        if about != retailer and retailer not in e["data"].get("text", ""):
            return {"error": f"This message is not about stop {retailer}"}
        if status not in ("delivered", "failed"):
            return {"error": "status must be delivered or failed"}
        drops = store.get_kv(self.run, "drops", {})
        drops[retailer] = {"status": status, "evidence": evidence_id, "photo": bool(e["data"].get("photo")), "truck": truck, "plan_version": plan["version"]}
        store.set_kv(self.run, "drops", drops)
        store.log(self.run, "drop_recorded", retailer=retailer, status=status, photo=drops[retailer]["photo"], truck=truck, version=plan["version"])
        return {"closed": retailer, "status": status, "total_closed": len(drops)}

    def log_miss(self, route: str, product: str, stage: str, held_by_role: str, evidence_ids: list[str]) -> dict:
        misses = store.get_kv(self.run, "misses", [])
        misses = [m for m in misses if not (m["route"] == route and m["product"] == product and m["stage"] == stage)]
        misses.append({"route": route, "product": product, "stage": stage, "held_by": held_by_role, "evidence": evidence_ids})
        store.set_kv(self.run, "misses", misses)
        store.log(self.run, "miss_logged", route=route, product=product, stage=stage, held_by=held_by_role)
        return {"logged": True, "open_misses": len(misses)}

    def report_to_lead(self, text: str, claims: list[dict]) -> dict:
        check = gate.check(self.run, text, claims)
        store.log(self.run, "report", text=text, claims=claims, used_standin=check["used_standin"])
        return {"shown": True}

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool. Failures go back to the agent as results, so it can recover."""
        store.log(self.run, "tool_call", name=name, args=args)
        try:
            result = getattr(self, name)(**args)
            ok = not (isinstance(result, dict) and result.get("error"))
        except gate.Blocked as err:
            gate.record_block(self.run, args.get("text") or args.get("question") or "", str(err))
            result, ok = {"error": f"Blocked by the source check: {err}"}, False
        except (ValueError, KeyError, StopIteration, IndexError, TypeError) as err:
            store.log(self.run, "tool_error", name=name, error=repr(err))
            result, ok = {"error": repr(err)}, False
        store.log(self.run, "tool_result", name=name, ok=ok, result=_short(result))
        return json.dumps(result, default=_json_default), not ok


def _json_default(value):
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _short(result) -> str:
    return json.dumps(result, default=_json_default)[:600]
