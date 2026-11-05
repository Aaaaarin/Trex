"""The only way to read invoices. It enforces the as-of date, so plans never see the future.

Next-day bills stand in for tonight's orders, because no order data exists yet. They are the one
deliberate exception, reached only through `orders_for`, and are labelled wherever they appear.
Later bills are reachable only by the replay scorer and the reply simulator, through `_future`.
"""
import difflib
import re
from functools import lru_cache

import pandas as pd

from .config import DATA
from .normalize import add_measures

SYNONYMS = {
    "makhan": "butter", "makkhan": "butter", "doodh": "milk", "dudh": "milk", "malai": "cream",
    "ghee": "ghee", "paneer": "paneer", "panir": "paneer", "cheese": "cheese", "ice cream": "ice cream",
    "icecream": "ice cream", "chocolate": "chocolate", "dahi": "curd", "kulfi": "kulfi",
}
UNIT_WORDS = {"peti": "case", "petti": "case", "dabba": "pack", "packet": "pack", "pkt": "pack", "kilo": "kg", "kg": "kg", "litre": "l", "ltr": "l"}


@lru_cache(maxsize=1)
def _all_lines() -> pd.DataFrame:
    d = pd.read_csv(DATA / "masked" / "invoice_lines.csv")
    d = add_measures(d)
    d["line_no"] = d.groupby("invoice_id").cumcount() + 1
    d["evidence_ref"] = d["invoice_id"] + ":" + d["line_no"].astype(str)
    return d


class Ledger:
    def __init__(self, as_of: str, distributor: str = "A"):
        self.as_of = as_of
        self.distributor = distributor

    def lines(self) -> pd.DataFrame:
        d = _all_lines()
        return d[(d["distributor"] == self.distributor) & (d["invoice_date"] <= self.as_of)]

    def orders_for(self, plan_date: str) -> pd.DataFrame:
        """Stand-in for tonight's orders: the bills dated the plan day. Labelled as a proxy."""
        if plan_date <= self.as_of:
            raise ValueError("plan date must be after the as-of date")
        d = _all_lines()
        return d[(d["distributor"] == self.distributor) & (d["invoice_date"] == plan_date) & (~d["is_counter"])]

    def _future(self, end: str) -> pd.DataFrame:
        """Hidden from planning code. Used only to simulate replies and to score replays."""
        d = _all_lines()
        return d[(d["distributor"] == self.distributor) & (d["invoice_date"] > self.as_of) & (d["invoice_date"] <= end)]

    def retailers(self) -> pd.DataFrame:
        d = _all_lines()
        d = d[d["distributor"] == self.distributor]
        return d.drop_duplicates("retailer_id")[["retailer_id", "retailer_alias", "retailer_type_hint", "route_id"]]

    def products(self) -> pd.DataFrame:
        d = _all_lines()
        d = d[d["distributor"] == self.distributor]
        return d.groupby(["product_id", "product_name", "group", "category", "case_pack", "pack_kg", "mrp"], as_index=False, dropna=False).agg(net=("net_amount", "sum")).sort_values("net", ascending=False).drop_duplicates("product_id")

    def find_retailer(self, text: str, limit: int = 3) -> list[dict]:
        rows = self.retailers()
        code = re.search(r"r-?\s?(\d{3})", text.lower())
        if code:
            rows_hit = rows[rows["retailer_id"] == f"R-{code.group(1)}"]
            if len(rows_hit):
                return _records(rows_hit)
        names = rows["retailer_alias"].str.lower().tolist()
        close = difflib.get_close_matches(text.lower().strip(), names, n=limit, cutoff=0.5)
        return _records(rows[rows["retailer_alias"].str.lower().isin(close)])

    def find_product(self, text: str, limit: int = 6) -> list[dict]:
        lower = text.lower()
        for word, meaning in SYNONYMS.items():
            lower = re.sub(rf"\b{word}\b", meaning, lower)
        tokens = [t for t in re.findall(r"[a-z]+|\d+", lower) if t not in UNIT_WORDS]
        rows = self.products().copy()
        names = rows["product_name"].str.lower()
        rows["score"] = [sum(t in n for t in tokens) for n in names]
        rows = rows[rows["score"] > 0].sort_values(["score", "net"], ascending=False).head(limit)
        return rows.drop(columns=["score"]).round(2).to_dict("records")


def _records(df: pd.DataFrame) -> list[dict]:
    return df.to_dict("records")


def merge_aliases() -> dict:
    """Known duplicates: one shop under two spellings stays one record."""
    return {"sundar kirana corner": "R-101", "sundar stores": "R-101"}
