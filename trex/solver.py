"""Set every quantity. The LLM never does this arithmetic.

Value falls with each extra unit a retailer gets, so empty shelves are served first and partial
orders come naturally. Same input, same answer: one worker, fixed seed.
"""
import math
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .config import standin

TIER_FIRST_SHARE = 0.5
TIER_FIRST_WEIGHT = 3
TYPE_WEIGHT = {"horeca": 15, "modern": 12, "outlet": 12, "wholesale": 8, "kirana": 10}


@dataclass
class Line:
    key: str
    truck: str
    route: str
    retailer: str
    retailer_type: str
    product: str
    product_name: str
    pieces: int
    kg_per_piece: float
    value_per_piece: float
    source: str  # "order proxy", "request" or "offer"
    evidence: str = ""


def solve(lines: list[Line], stock: dict[str, int], capacity: dict[str, float], seconds: float = 5.0) -> dict:
    """Return allocated pieces per line. Falls back to a greedy plan if the solver cannot finish."""
    model = cp_model.CpModel()
    first, rest = {}, {}
    objective = []
    for i, ln in enumerate(lines):
        head = max(1, math.ceil(ln.pieces * TIER_FIRST_SHARE)) if ln.pieces else 0
        first[i] = model.new_int_var(0, head, f"a{i}")
        rest[i] = model.new_int_var(0, max(0, ln.pieces - head), f"b{i}")
        value = max(1, round(ln.value_per_piece))
        w = TYPE_WEIGHT.get(ln.retailer_type, 10)
        objective.append(first[i] * value * w * TIER_FIRST_WEIGHT)
        objective.append(rest[i] * value * w)
    for product in {ln.product for ln in lines}:
        idx = [i for i, ln in enumerate(lines) if ln.product == product]
        if product in stock:
            model.add(sum(first[i] + rest[i] for i in idx) <= stock[product])
    for truck in {ln.truck for ln in lines}:
        idx = [i for i, ln in enumerate(lines) if ln.truck == truck]
        cap = round(capacity[truck] * 100)
        model.add(sum((first[i] + rest[i]) * round(lines[i].kg_per_piece * 100) for i in idx) <= cap)
    model.maximize(sum(objective))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 7
    status = solver.solve(model)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        alloc = {lines[i].key: int(solver.value(first[i]) + solver.value(rest[i])) for i in range(len(lines))}
        method = "optimal" if status == cp_model.OPTIMAL else "best found in time limit"
    else:
        alloc = greedy(lines, stock, capacity)
        method = "greedy fallback"
    return {"allocated": alloc, "method": method}


def greedy(lines: list[Line], stock: dict[str, int], capacity: dict[str, float]) -> dict:
    left = dict(stock)
    room = dict(capacity)
    alloc = {ln.key: 0 for ln in lines}
    order = sorted(lines, key=lambda x: (-TYPE_WEIGHT.get(x.retailer_type, 10), x.key))
    for share in (TIER_FIRST_SHARE, 1.0):
        for ln in order:
            want = math.ceil(ln.pieces * share) - alloc[ln.key]
            can = min(want, left.get(ln.product, want), int(room[ln.truck] / max(ln.kg_per_piece, 0.001)))
            if can > 0:
                alloc[ln.key] += can
                if ln.product in left:
                    left[ln.product] -= can
                room[ln.truck] -= can * ln.kg_per_piece
    return alloc


def assign_batches(allocations: list[dict], batches: list[dict]) -> list[dict]:
    """Oldest stock goes to whoever may take it, fastest-turning retailer types first."""
    min_share = standin("min_life_share")
    by_product: dict[str, list[dict]] = {}
    for b in sorted(batches, key=lambda x: x["days_left"]):
        by_product.setdefault(b["product_id"], []).append(dict(b))
    speed = {"horeca": 0, "wholesale": 1, "modern": 2, "outlet": 3, "kirana": 4}
    out = []
    for a in sorted(allocations, key=lambda x: (speed.get(x["retailer_type"], 9), x["retailer"])):
        need = a["pieces"]
        parts = []
        for b in by_product.get(a["product"], []):
            if need <= 0:
                break
            if b["pieces"] <= 0 or b["share_left"] < min_share.get(a["retailer_type"], 0.2):
                continue
            take = min(need, int(b["pieces"]))
            b["pieces"] -= take
            need -= take
            parts.append({"batch": b["batch_id"], "pieces": take, "days_left": int(b["days_left"])})
        out.append({**a, "batches": parts, "no_batch_pieces": need})
    return out
