"""What the agent can do. Quantities come only from the solver, and every send passes the source check."""
import hashlib
import json

import pandas as pd

from . import channels, gate, orders_api, store, upstream
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
    _tool("price_options", "Price go, sell to fill, merge and hold for one truck in rupees.", {"truck": _S}),
    _tool("plan_offers", "Who to ask to fill this truck. Returns only as many retailers as are needed to reach the target, so nobody is asked needlessly.", {"truck": _S}),
    _tool("add_request", "Add a retailer's request to the plan. evidence_id must be the message it came from.", {"retailer": _S, "product": _S, "pieces": _I, "evidence_id": _S}),
    _tool("send_offer", "Send one retailer an offer from plan_offers, in short natural Hinglish. Sent at most once.", {"retailer": _S, "product": _S, "pieces": _I, "text": _S, "claims": _CLAIMS}),
    _tool("accept_offer", "Record a retailer's yes and put it on the truck. evidence_id must be that retailer's reply. Pieces cannot exceed the offer.", {"retailer": _S, "product": _S, "pieces": _I, "evidence_id": _S}),
    _tool("send_message", "Send a short message to a person. role is retailer, salesman, driver or approver.", {"role": _S, "recipient": _S, "text": _S, "claims": _CLAIMS}),
    _tool("request_decision", "Ask a person for one exception, with evidence. Use only when the answer changes the result. Then end your turn and wait.", {"question": _S, "options": {"type": "array", "items": _S}, "claims": _CLAIMS}),
    _tool("finalize_truck", "Lock a truck's decision. Going under the target load needs an approved decision first.", {"truck": _S, "option": _S, "reason": _S}),
    _tool("write_orders", "Write this truck's orders to the order system. Safe to call again: nothing is written twice.", {"truck": _S}),
    _tool("issue_load_sheet", "Issue the load sheet with batches, and tell the driver and each retailer what is coming, including partials.", {"truck": _S}),
    _tool("record_drop", "Close a delivery stop. evidence_id must be the driver's own message. status is delivered or failed.", {"retailer": _S, "status": _S, "evidence_id": _S}),
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
        sig = json.dumps(self._extras(), sort_keys=True)
        if not self._cached or self._cached[0] != sig:
            self._cached = (sig, Night(self.evening, self.plan_date, self.distributor, self._extras()))
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

    def find_records(self, text: str) -> dict:
        known = merge_aliases().get(text.lower().strip())
        retailers = self.ledger.find_retailer(known or text)
        products = self.ledger.find_product(text)
        for p in products:
            p["evidence_id"] = store.add_evidence(self.run, "catalog", p["product_id"], p["product_name"], p)
        return {"retailers": retailers, "products": products, "note": "Retailer types are inferred from names and packs, not recorded"}

    def truck_board(self) -> dict:
        trucks = self.night().truck_summary()
        eid = self._standin_evidence(f"board:{len(self._extras())}", "Truck loads, with capacity a stand-in", trucks)
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
        p = self.night().price_options(truck)
        p["evidence_id"] = self._standin_evidence(f"options:{truck}:{len(self._extras())}", f"Priced options for {truck}", p)
        if store.get_kv(self.run, f"options:{truck}") is None:
            store.set_kv(self.run, f"options:{truck}", p)
        store.set_kv(self.run, f"options_latest:{truck}", p)
        store.set_kv(self.run, "focus", truck)
        if store.get_kv(self.run, f"start_fill:{truck}") is None:
            store.set_kv(self.run, f"start_fill:{truck}", p["fill"])
        return p

    def plan_offers(self, truck: str) -> dict:
        night = self.night()
        me = next(t for t in night.truck_summary() if t["truck"] == truck)
        gap = max(0.0, standin("fill_target") * me["capacity_kg"] - me["kg"])
        candidates = night.offers(truck, limit=20)
        chosen, kg = [], 0.0
        for o in candidates:
            if kg >= gap * 1.1:
                break
            chosen.append(o)
            kg += o["kg"]
        for o in chosen:
            o["evidence_id"] = store.add_evidence(self.run, "estimate", f"offer:{o['retailer']}:{o['product']}", f"Why offer {o['product']} to {o['retailer']}", o)
        data = {"truck": truck, "needs_kg": round(gap), "asking": len(chosen), "not_asking": len(candidates) - len(chosen), "offers": chosen, "max_discount_inr_per_pack": standin("max_discount_inr_per_pack"), "note": "Offer whole cases. Offer a discount only where discount_inr_per_piece is above zero"}
        data["discount_evidence_id"] = self._standin_evidence("max_discount", "Allowed discount, stand-in", {"max_discount_inr_per_pack": standin("max_discount_inr_per_pack")})
        known = store.get_kv(self.run, f"offers:{truck}", {})
        known.update({o["retailer"]: o for o in chosen if o["retailer"] not in known})
        store.set_kv(self.run, f"offers:{truck}", known)
        return data

    def _add_extra(self, retailer: str, product: str, pieces: int, source: str, evidence_id: str, route: str | None = None) -> dict:
        r = self._retailer(retailer)
        route = route or r["route_id"]
        truck = self._truck_for_route(route)
        extras = [e for e in self._extras() if not (e["retailer"] == retailer and e["product"] == product and e["source"] == source)]
        extras.append({"truck": truck, "route": route, "retailer": retailer, "retailer_type": r["retailer_type_hint"], "product": product, "pieces": int(pieces), "source": source, "evidence": evidence_id})
        store.set_kv(self.run, "extras", extras)
        me = next(t for t in self.night().truck_summary() if t["truck"] == truck)
        store.log(self.run, "load_changed", truck=truck, fill=me["fill"], kg=me["kg"], retailer=retailer, product=product, pieces=int(pieces), source=source)
        return {"truck": truck, "fill": me["fill"], "kg": me["kg"], "evidence_id": self._standin_evidence(f"load:{truck}:{len(extras)}", f"Load of {truck}", me)}

    def add_request(self, retailer: str, product: str, pieces: int, evidence_id: str) -> dict:
        if store.get_evidence(evidence_id) is None:
            return {"error": "That evidence does not exist"}
        night = self.night()
        promised = sum(ln.pieces for ln in night.lines if ln.product == product)
        stock = night.stock.get(product)
        if stock is not None and promised + pieces > stock:
            data = {"product": product, "stock_pieces": stock, "already_promised_pieces": promised, "requested_pieces": pieces, "standin": True}
            eid = self._standin_evidence(f"scarcity:{product}", f"Stock against promises for {product}", data)
            approved = store.rows("select id from decisions where run=? and answer is not null and lower(answer) like 'approve%' and evidence like ?", (self.run, f"%{eid}%"))
            if approved:
                return self._add_extra(retailer, product, pieces, "request", evidence_id)
            return {"needs_decision": True, "reason": "This takes stock already promised to other retailers. Use request_decision with these numbers, then end your turn. Call add_request again after approval.", "evidence_id": eid, **data}
        return self._add_extra(retailer, product, pieces, "request", evidence_id)

    def send_offer(self, retailer: str, product: str, pieces: int, text: str, claims: list[dict]) -> dict:
        gate.check(self.run, text, claims)
        group = self.ledger.products().set_index("product_id").loc[product, "group"]
        key = f"{self.run}:offer:{retailer}:{product}"
        route = None
        for rows in store.rows("select value from kv where run=? and key like 'offers:%'", (self.run,)):
            route = (json.loads(rows["value"]).get(retailer) or {}).get("route") or route
        return channels.send(self.run, "retailer", retailer, text, key, {"retailer": retailer, "product": product, "pieces": int(pieces), "group": group, "route": route})

    def accept_offer(self, retailer: str, product: str, pieces: int, evidence_id: str) -> dict:
        e = store.get_evidence(evidence_id)
        if e is None or e["kind"] != "message" or e["data"].get("sender") != retailer:
            return {"error": "evidence_id must be this retailer's own reply"}
        sent = store.rows("select data from outbox where key=?", (f"{self.run}:offer:{retailer}:{product}",))
        if not sent:
            return {"error": "No offer of this product was sent to this retailer"}
        offered = json.loads(sent[0]["data"])["pieces"]
        if pieces > offered:
            return {"error": f"Retailer accepted more than offered ({offered}). Ask a person"}
        sent_offer = json.loads(sent[0]["data"])
        return self._add_extra(retailer, product, pieces, "offer", evidence_id, sent_offer.get("route"))

    def send_message(self, role: str, recipient: str, text: str, claims: list[dict]) -> dict:
        gate.check(self.run, text, claims)
        key = f"{self.run}:msg:{recipient}:" + hashlib.sha256(text.encode()).hexdigest()[:12]
        return channels.send(self.run, role, recipient, text, key)

    def request_decision(self, question: str, options: list[str], claims: list[dict]) -> dict:
        check = gate.check(self.run, question, claims)
        did = hashlib.sha256(f"{self.run}:{question}".encode()).hexdigest()[:10]
        store.execute("insert or ignore into decisions(id, run, ts, question, options, evidence) values(?,?,strftime('%s','now'),?,?,?)", (did, self.run, question, json.dumps(options), json.dumps(claims)))
        store.log(self.run, "decision_requested", id=did, question=question, options=options, used_standin=check["used_standin"])
        channels.send(self.run, "approver", "approver", question + "\nOptions: " + ", ".join(options), f"{self.run}:decision:{did}")
        return {"status": "waiting_for_person", "decision_id": did, "instruction": "End your turn now. The answer will arrive as a new message."}

    def finalize_truck(self, truck: str, option: str, reason: str) -> dict:
        me = next(t for t in self.night().truck_summary() if t["truck"] == truck)
        target = standin("fill_target")
        if option in ("go", "sell_to_fill") and me["fill"] < target:
            approved = store.rows("select id from decisions where run=? and answer is not null and lower(answer) like 'approve%'", (self.run,))
            if not approved:
                return {"error": f"Load is {me['fill']:.0%}, under the {target:.0%} target. Sell more, or get an approved decision first"}
        decided = store.get_kv(self.run, "decided", {})
        decided[truck] = {"option": option, "reason": reason, "fill": me["fill"], "kg": me["kg"]}
        store.set_kv(self.run, "decided", decided)
        store.log(self.run, "truck_decided", truck=truck, option=option, reason=reason, fill=me["fill"])
        return {"status": "locked", **decided[truck]}

    def write_orders(self, truck: str) -> dict:
        sheet = self.night().load_sheet(truck)
        counts: dict[str, int] = {}
        problems = []
        for row in sheet:
            key = f"{self.run}:{truck}:{row['retailer']}:{row['product']}"
            result = orders_api.write_safely(self.run, key, {"truck": truck, "retailer": row["retailer"], "product": row["product"], "pieces": row["pieces"], "batches": row["batches"]})
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            if result["status"] in ("failed", "conflict"):
                problems.append(result)
        store.log(self.run, "orders_written", truck=truck, counts=counts)
        return {"truck": truck, "counts": counts, "total_saved_in_system": orders_api.count(self.run), "lines": len(sheet), "problems": problems}

    def issue_load_sheet(self, truck: str) -> dict:
        night = self.night()
        sheet = night.load_sheet(truck)
        store.set_kv(self.run, f"sheet:{truck}", sheet)
        stops: dict[str, list[dict]] = {}
        for row in sheet:
            stops.setdefault(row["retailer"], []).append(row)
        for retailer, items in stops.items():
            short = [i for i in items if i["pieces"] < i["ordered"]]
            body = "; ".join(f"{i['product_name']}: {i['pieces']}" + (f" of {i['ordered']}" if i["pieces"] < i["ordered"] else "") for i in items[:6])
            text = f"Kal gaadi {truck} aa rahi hai. {body}." + (" Kuch maal kam hai, baaki agli gaadi se." if short else "")
            channels.send(self.run, "retailer", retailer, text, f"{self.run}:sheet:{truck}:{retailer}")
        channels.send(self.run, "driver", f"driver-{truck}", f"Load sheet {truck}: {len(stops)} stops, {sum(i['pieces'] for i in sheet)} pieces. Send a photo at each drop.", f"{self.run}:driver:{truck}")
        pending = sorted(stops)
        store.set_kv(self.run, f"pending_drops:{truck}", pending)
        store.log(self.run, "load_sheet_issued", truck=truck, stops=len(stops), lines=len(sheet), partial=sum(1 for i in sheet if i["pieces"] < i["ordered"]))
        return {"truck": truck, "stops": len(stops), "lines": len(sheet), "partial_lines": sum(1 for i in sheet if i["pieces"] < i["ordered"]), "instruction": "End your turn. Driver confirmations arrive as new messages."}

    def record_drop(self, retailer: str, status: str, evidence_id: str) -> dict:
        e = store.get_evidence(evidence_id)
        if e is None or e["kind"] != "message" or e["data"].get("role") != "driver":
            return {"error": "A drop closes only on the driver's own message. 'Dispatched' is not proof"}
        drops = store.get_kv(self.run, "drops", {})
        drops[retailer] = {"status": status, "evidence": evidence_id, "photo": bool(e["data"].get("photo"))}
        store.set_kv(self.run, "drops", drops)
        store.log(self.run, "drop_recorded", retailer=retailer, status=status, photo=drops[retailer]["photo"])
        return {"closed": retailer, "status": status, "total_closed": len(drops)}

    def log_miss(self, route: str, product: str, stage: str, held_by_role: str, evidence_ids: list[str]) -> dict:
        misses = store.get_kv(self.run, "misses", [])
        misses = [m for m in misses if not (m["route"] == route and m["product"] == product)]
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
        except (ValueError, KeyError, StopIteration, IndexError) as err:
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
