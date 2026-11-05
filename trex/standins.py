"""Stock and batch stand-ins. No stock data exists yet, so these come from later real bills.

Everything returned here is labelled a stand-in. Replace this module when real stock arrives.
"""
import math

import pandas as pd

from .config import standin
from .ledger import Ledger


def day_of_year(date: str) -> int:
    return pd.Timestamp(date).day_of_year


def stock_position(ledger: Ledger, plan_date: str) -> pd.DataFrame:
    """Stock by product batch: what later bills show was sold from each batch, capped at the cover days."""
    end = (pd.Timestamp(plan_date) + pd.Timedelta(days=standin("stock_cover_days"))).strftime("%Y-%m-%d")
    later = ledger._future(end)
    stock = later.groupby(["product_id", "product_name", "group", "batch_id", "batch_day_of_year"], as_index=False, dropna=False).agg(pieces=("pieces", "sum"), kg=("kg", "sum"))
    stock["pieces"] = stock["pieces"].map(math.ceil)
    doy = day_of_year(plan_date)
    shelf = standin("shelf_life_days")
    stock["age_days"] = (doy - stock["batch_day_of_year"]) % 365
    stock["life_days"] = stock["group"].map(lambda g: shelf.get(g, shelf["other"]))
    stock["days_left"] = stock["life_days"] - stock["age_days"]
    stock["share_left"] = (stock["days_left"] / stock["life_days"]).round(3)
    # Some batch codes imply stock older than its whole life. Treat that as a bad record, not as expired stock.
    stock["suspect_batch_date"] = stock["age_days"] > stock["life_days"]
    stock.loc[stock["suspect_batch_date"], ["days_left", "share_left"]] = [90, 0.5]

    plan_orders = ledger.orders_for(plan_date)
    for product, share in standin("scarce_products").items():
        ordered = plan_orders[plan_orders["product_id"] == product]["pieces"].sum()
        mask = stock["product_id"] == product
        if mask.any() and ordered > 0:
            total = stock.loc[mask, "pieces"].sum()
            stock.loc[mask, "pieces"] = (stock.loc[mask, "pieces"] * (ordered * share / total)).map(math.floor)
    return stock[stock["pieces"] > 0].reset_index(drop=True)


def stock_by_product(stock: pd.DataFrame) -> dict[str, int]:
    return stock.groupby("product_id")["pieces"].sum().astype(int).to_dict()
