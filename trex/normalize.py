"""Turn messy invoice lines into consistent pieces, kilograms and product groups.

Rate and quantity disagree on about a third of lines, because rate is sometimes per case and
quantity per piece. Value divided by consumer price is the one measure that holds across them.
"""
import re

import pandas as pd

PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|gm|g|ltr|lt|l|ml)\b", re.I)
CASE = re.compile(r"^\D*?(\d+)\s*[xX]\s*(\d+)(?:\s*[xX]\s*(\d+))?")
TRADE_FACTOR = 0.87  # selling price as a share of MRP, measured roughly from the data
GROUPS = [
    ("paneer", r"paneer"),
    ("butter", r"butter(?!\s*scotch)|butter milk"),
    ("cheese", r"cheese|mozzarella|cheddar"),
    ("cream", r"fresh cream|cream 1"),
    ("ghee", r"ghee"),
    ("chocolate", r"chocolate|choco"),
    ("powder", r"milk powder"),
    ("creamer", r"dairy creamer"),
    ("milk", r"milk"),
]


def pack_kg(name: str) -> float | None:
    m = PACK.search(str(name))
    if not m:
        return None
    value = float(m.group(1))
    return value if m.group(2).lower() in ("kg", "ltr", "lt", "l") else value / 1000


def case_pack(name: str) -> int:
    """Pieces per case from a name such as '12x1 Ltr' or '8x8x200 Gm'. 1 if unknown."""
    text = re.sub(r"\([^)]*\)", "", str(name))
    m = re.search(r"(\d+)\s*[xX]\s*(\d+)(?:\s*[xX]\s*(\d+))?", text)
    if not m:
        return 1
    return int(m.group(1)) * (int(m.group(2)) if m.group(3) else 1)


def group_of(name: str, category: str = "") -> str:
    if str(category).lower().startswith("frozen"):
        return "frozen"
    lower = str(name).lower()
    for group, pattern in GROUPS:
        if re.search(pattern, lower):
            return group
    return "other"


def add_measures(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["pack_kg"] = d["product_name"].map(pack_kg)
    d["case_pack"] = d["product_name"].map(case_pack)
    d["group"] = [group_of(n, c) for n, c in zip(d["product_name"], d["category"])]
    d["taxable"] = d["net_amount"] - d["tax_amount"]
    price = d["mrp"].where(d["mrp"] > 0) * TRADE_FACTOR
    d["pieces"] = (d["taxable"] / price).round(1)
    d["kg"] = d["pieces"] * d["pack_kg"]
    d["kg"] = d["kg"].fillna(0.0)
    d["pieces"] = d["pieces"].fillna(0.0)
    return d


def line_warnings(d: pd.DataFrame) -> dict:
    """Facts about record quality the agent must state rather than hide."""
    rate_qty = d["rate_before_tax"] * d["qty_nos"]
    agrees = ((d["taxable"] / rate_qty.where(rate_qty > 0)).between(0.98, 1.02)).mean()
    invoices = d.drop_duplicates("invoice_id")
    return {
        "lines_where_rate_times_quantity_matches_value": round(float(agrees), 3),
        "invoices_naming_a_delivery_person": round(float(invoices["delivery_person_named"].mean()), 3),
        "invoices_marked_app_bill": round(float(invoices["app_bill"].mean()), 3),
        "delivery_date_equals_upload_date": round(float((invoices["delivery_date_field"] == invoices["uploaded_date"]).mean()), 3),
    }
