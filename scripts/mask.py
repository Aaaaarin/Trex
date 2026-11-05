"""Turn raw distributor invoice exports into masked CSVs safe to commit.

Raw files stay outside the repo. Real names, codes and numbers never reach data/masked/.
"""
import argparse
import hashlib
import json
import hmac
import os
import re
import secrets
from pathlib import Path

import pandas as pd
from pyxlsb import open_workbook

OUT = Path(__file__).resolve().parents[1] / "data" / "masked"
SALT_FILE = Path.home() / ".trex.salt"
# The real brand and retailer terms stay outside the repo, like the salt and the raw files.
_LOCAL = json.loads((Path.home() / ".trex.mask.json").read_text(encoding="utf-8"))

TYPE_KEYWORDS = {
    "horeca": r"HOTEL|RESTAURANT|RESTURANT|DHABA|CAFE|CATER|KITCHEN|BAKER|BAKARY|SWEET|MISTHAN|FOOD|PIZZA|TEA|CANTEEN|BIRYANI",
    "outlet": rf"PARLOU?R|{_LOCAL['outlet_word']}",
    "modern": rf"MART|SUPER|BAZAAR|HYPER|MALL|RETAIL|{_LOCAL['chain_words']}|\bBIG\b",
    "wholesale": r"TRADER|TRADING|AGENC|ENTERPRISE|DISTRIB|WHOLE|ASSOCIATE|BROTHER|SONS",
}
ALIAS_A = ["Sundar", "Kamal", "Rupa", "Tara", "Neel", "Chanda", "Vasant", "Gulab", "Deep", "Mohan"]
ALIAS_B = ["Store", "Traders", "Kirana", "General Store", "Provision", "Mart", "Sweets", "Bakery", "Dairy", "Hotel"]
PRODUCT_SUBS = [tuple(s) for s in _LOCAL["product_subs"]]


def salt() -> bytes:
    if not SALT_FILE.exists():
        SALT_FILE.write_text(secrets.token_hex(32))
    return SALT_FILE.read_text().strip().encode()


def tag(value: str, prefix: str, n: int = 6) -> str:
    digest = hmac.new(salt(), f"{prefix}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{prefix}-{digest[:n].upper()}"


def read_xlsb(path: str) -> pd.DataFrame:
    with open_workbook(path) as wb, wb.get_sheet(wb.sheets[0]) as sheet:
        rows = [[c.v for c in r] for r in sheet.rows()]
    return pd.DataFrame(rows[1:], columns=rows[0])


def retailer_type(name: str) -> str:
    upper = str(name).upper()
    for kind, pattern in TYPE_KEYWORDS.items():
        if re.search(pattern, upper):
            return kind
    return "kirana"


def clean_product(name: str) -> str:
    out = str(name)
    for pattern, sub in PRODUCT_SUBS:
        out = re.sub(pattern, sub, out)
    return re.sub(r"\s+", " ", out).strip()


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def iso(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%d/%m/%Y").dt.strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs=2, help="raw xlsb exports: distributor A then distributor B")
    args = ap.parse_args()

    frames = []
    for label, path in zip("AB", args.raw):
        frame = read_xlsb(path)
        frame["distributor"] = label
        frames.append(frame)
    d = pd.concat(frames, ignore_index=True)
    d["is_counter"] = d["Salesman Name"].str.lower().str.contains("counter") | d["Route Name"].str.lower().str.contains("counter")

    # Stable labels by frequency, distributor A first, so the plan's route and vehicle labels hold.
    beat = d[~d["is_counter"]]
    route_map: dict[str, str] = {}
    vehicle_map: dict[str, str] = {}
    salesman_map: dict[str, str] = {}
    for label in "AB":
        part = beat[beat["distributor"] == label]
        for name in part["Route Name"].value_counts().index:
            route_map.setdefault(norm_key(name), f"R{len(route_map) + 1:02d}")
        for name in part["Vehicle Name"].value_counts().index:
            vehicle_map.setdefault(name, f"V{len(vehicle_map) + 1}")
        for name in part["Salesman Name"].value_counts().index:
            salesman_map.setdefault(name, f"S{len(salesman_map) + 1:02d}")
    for name in d["Delivery Route Name"].unique():
        route_map.setdefault(norm_key(name), f"R{len(route_map) + 1:02d}")
    for name in d["Route Name"].unique():
        route_map.setdefault(norm_key(name), f"R{len(route_map) + 1:02d}")
    for name in d["Vehicle Name"].unique():
        vehicle_map.setdefault(name, f"V{len(vehicle_map) + 1}")
    for name in d["Salesman Name"].unique():
        salesman_map.setdefault(name, "COUNTER" if "counter" in str(name).lower() else f"S{len(salesman_map) + 1:02d}")

    # Fixed short retailer codes and invented display aliases. Aliases never derive from real names.
    retailer_rows = d.drop_duplicates("Retailer Code")[["Retailer Code", "Retailer Name", "distributor"]].sort_values("Retailer Code")
    order = sorted(retailer_rows["Retailer Code"], key=lambda c: tag(c, "RORD", 12))
    retailer_ids = {code: f"R-{i + 101}" for i, code in enumerate(order)}
    real_names = {norm_key(n) for n in retailer_rows["Retailer Name"]}
    aliases: dict[str, str] = {}
    for i, code in enumerate(order):
        a, b = ALIAS_A[i % len(ALIAS_A)], ALIAS_B[(i // len(ALIAS_A)) % len(ALIAS_B)]
        alias = f"{a} {b} {i + 101}"
        if norm_key(alias) in real_names:
            alias = f"Shop {i + 101}"
        aliases[code] = alias

    product_order = d.groupby("Product Code")["Product Net Amount"].sum().sort_values(ascending=False).index
    product_ids = {code: f"P{i + 1:03d}" for i, code in enumerate(product_order)}

    m = d["Batch Code"].astype(str).str.extract(r"^([A-Za-z]{3})(\d{3})")
    out = pd.DataFrame(
        {
            "distributor": d["distributor"],
            "invoice_id": d["Sales Invoice No"].map(lambda v: tag(v, "INV")),
            "invoice_date": iso(d["Sales Invoice Date"]),
            "delivery_date_field": iso(d["Delivery Date"]),
            "uploaded_date": iso(d["Uploaded Date"]),
            "retailer_id": d["Retailer Code"].map(retailer_ids),
            "retailer_alias": d["Retailer Code"].map(aliases),
            "retailer_type_hint": d["Retailer Name"].map(retailer_type),
            "route_id": d["Route Name"].map(lambda v: route_map[norm_key(v)]),
            "delivery_route_id": d["Delivery Route Name"].map(lambda v: route_map[norm_key(v)]),
            "salesman_id": d["Salesman Name"].map(salesman_map),
            "vehicle_id": d["Vehicle Name"].map(vehicle_map),
            "delivery_person_named": d["Delivery Boy Name"].str.strip().str.upper() != "DELIVERY BOY",
            "is_counter": d["is_counter"],
            "app_bill": d["SFA Bill"].eq("Yes"),
            "product_id": d["Product Code"].map(product_ids),
            "product_name": d["Product Name"].map(clean_product),
            "category": d["LOB Name"],
            "batch_id": d["Batch Code"].map(lambda v: tag(v, "B", 4)),
            "batch_day_of_year": pd.to_numeric(m[1], errors="coerce"),
            "mrp": d["MRP"],
            "qty_box": d["Product Quantity in Box"],
            "qty_nos": d["Product Quantity in Nos"],
            "rate_before_tax": d["Selling Rate Before Tax"],
            "tax_amount": d["Product Tax Amount"],
            "scheme_discount": d["Product Scheme Discount"],
            "net_amount": d["Product Net Amount"],
            "invoice_net_amount": d["Sales Net Amount"],
        }
    )
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "invoice_lines.csv", index=False)
    print("lines", len(out), "invoices", out["invoice_id"].nunique(), "retailers", out["retailer_id"].nunique(), "products", out["product_id"].nunique())
    print("raw net", round(d["Product Net Amount"].sum(), 2), "masked net", round(out["net_amount"].sum(), 2))

    # Audit: nothing real may appear in the masked output.
    # Aliases are invented, so a chance overlap with a real shop name is not a leak.
    text = out.drop(columns=["retailer_alias"]).to_csv(index=False).lower()
    columns = ["Distributor Name", "Distributor Code", "Sales Invoice No", "Salesman Name", "Route Name", "Delivery Route Name", "Retailer Code", "Retailer Name", "Vehicle Name", "Product Code", "Batch Code"]
    leaks = {}
    for col in columns:
        for value in d[col].astype(str).unique():
            v = value.strip().lower()
            if len(v) >= 5 and v not in {"delivery boy", "counter sale", "counter sales"} and v in text:
                leaks.setdefault(col, []).append(len(v))
    brand_hits = len(re.findall(_LOCAL["audit_word"], text))
    print("leak columns", {k: len(v) for k, v in leaks.items()}, "brand hits", brand_hits)
    if leaks or brand_hits:
        raise SystemExit("MASKING AUDIT FAILED")
    print("audit passed")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
