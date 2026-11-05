"""Estimate days of stock left on a retailer's shelf as a range, never a single number.

Retailers do not report stock. Bills are the only evidence, and most pairs have very little.
"""
from dataclasses import asdict, dataclass

import pandas as pd

from .ledger import Ledger

SIMILAR_GAP_DAYS = 7.0


@dataclass
class Belief:
    retailer_id: str
    product_id: str
    low_days: float
    high_days: float
    confidence: str
    bills: int
    days_since_last: int | None
    last_pieces: float | None
    evidence_refs: list[str]
    note: str

    def dict(self) -> dict:
        return asdict(self)


def estimate(ledger: Ledger, retailer_id: str, product_id: str) -> Belief:
    d = ledger.lines()
    h = d[(d["retailer_id"] == retailer_id) & (d["product_id"] == product_id)].sort_values("invoice_date")
    today = pd.Timestamp(ledger.as_of)
    days = h.groupby("invoice_date", as_index=False).agg(pieces=("pieces", "sum"), ref=("evidence_ref", "first"))
    refs = days["ref"].tolist()[-5:]
    if days.empty:
        return Belief(retailer_id, product_id, 0, 14, "none", 0, None, None, [], "Never billed here, so stock is unknown")
    stamps = pd.to_datetime(days["invoice_date"])
    since = int((today - stamps.iloc[-1]).days)
    last = float(days["pieces"].iloc[-1])
    if len(days) >= 3:
        gaps = stamps.diff().dt.days.dropna()
        mean, spread = float(gaps.mean()), float(gaps.std() or 0)
        low, high = max(0.0, mean - spread - since), max(0.0, mean + spread - since)
        conf = "medium" if spread <= mean * 0.5 else "low"
        note = f"Own buying gap about {mean:.0f} days"
    elif len(days) == 2:
        gap = float((stamps.iloc[1] - stamps.iloc[0]).days)
        low, high = max(0.0, gap * 0.5 - since), max(0.0, gap * 1.5 - since)
        conf = "low"
        note = "Only one buying gap on record"
    else:
        low, high = 0.0, max(0.0, SIMILAR_GAP_DAYS * 2 - since)
        conf = "very low"
        note = "One bill only, range uses a weekly buying habit"
    return Belief(retailer_id, product_id, round(low, 1), round(high, 1), conf, len(days), since, round(last, 1), refs, note)


def overrides(belief: Belief, stated_days: tuple[float, float], message_ref: str) -> Belief:
    """A retailer's own reply beats any estimate."""
    belief.low_days, belief.high_days = stated_days
    belief.confidence = "reported"
    belief.evidence_refs = [message_ref, *belief.evidence_refs]
    belief.note = "Retailer replied"
    return belief
