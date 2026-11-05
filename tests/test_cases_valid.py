"""Validate authored dispatch fixtures against masked invoices, without a model.

This module uses csv/json directly. It must not import trex: application config
loads local credentials. Passing these tests says nothing about agent accuracy.
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUESTS = ROOT / "tests" / "cases" / "requests.json"
INVOICES = ROOT / "data" / "masked" / "invoice_lines.csv"
DATA = json.loads(REQUESTS.read_text(encoding="utf-8"))
CASES = DATA["cases"]
OUTCOMES = {
    "evaluate_dispatch",
    "needs_stock_approval",
    "clarify_quantity",
    "clarify_unit",
    "clarify_product",
}
RETAILER_CODE = re.compile(r"\br[- ]?(\d{3})\b", re.IGNORECASE)
PRODUCT_CODE = re.compile(r"\bp\d{3}\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def catalogue():
    with INVOICES.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        assert {"retailer_id", "product_id", "distributor"} <= set(reader.fieldnames or [])
        rows = list(reader)
    assert rows, "The masked invoice file is empty"
    scoped = [row for row in rows if row["distributor"] == DATA["context"]["distributor"]]
    assert scoped, "The fixture distributor is absent from masked data"
    return {
        "retailers": {row["retailer_id"] for row in rows},
        "products": {row["product_id"] for row in rows},
        "scoped_retailers": {row["retailer_id"] for row in scoped},
        "scoped_products": {row["product_id"] for row in scoped},
    }


def _strings(value):
    """Include text, expectations and metadata when looking for catalogue ids."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def test_fixture_count_schema_and_coverage():
    assert DATA["schema_version"] == 1
    assert len(CASES) == 30
    assert len({case["id"] for case in CASES}) == 30
    assert len({case["text"] for case in CASES}) == 30
    assert set(DATA["outcomes"]) == OUTCOMES
    tags = {tag for case in CASES for tag in case["tags"]}
    assert {
        "hinglish", "misspelling", "peti", "pack", "missing_quantity",
        "two_products", "unknown_product", "reworded_pair",
    } <= tags


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_case_expected_ids_and_shape(case, catalogue):
    assert re.fullmatch(r"C\d{2}", case["id"])
    assert isinstance(case["text"], str) and case["text"].strip()
    assert isinstance(case["tags"], list) and case["tags"]
    assert all(isinstance(tag, str) and tag for tag in case["tags"])
    expected = case["expected"]
    assert set(expected) == {"retailer", "products", "outcome"}
    assert expected["outcome"] in OUTCOMES
    assert expected["retailer"] in catalogue["retailers"]
    assert expected["retailer"] in catalogue["scoped_retailers"]
    mentioned = {f"R-{code}" for code in RETAILER_CODE.findall(case["text"])}
    assert mentioned == {expected["retailer"]}
    lines = expected["products"]
    assert isinstance(lines, list) and lines
    known_products = []
    for line in lines:
        assert {"product", "pieces"} <= set(line)
        assert set(line) <= {"product", "pieces", "candidate_products"}
        product, pieces = line["product"], line["pieces"]
        if product is not None:
            assert product in catalogue["products"]
            assert product in catalogue["scoped_products"]
            known_products.append(product)
        else:
            assert expected["outcome"] == "clarify_product"
        assert pieces is None or (type(pieces) is int and pieces > 0)
        candidates = line.get("candidate_products", [])
        assert isinstance(candidates, list)
        assert len(candidates) == len(set(candidates))
        assert set(candidates) <= catalogue["products"]
        assert set(candidates) <= catalogue["scoped_products"]
        if candidates:
            assert product is None and len(candidates) >= 2
    assert len(known_products) == len(set(known_products))
    mentioned_products = {code.upper() for code in PRODUCT_CODE.findall(case["text"])}
    assert mentioned_products == set(known_products)
    if expected["outcome"] in {"evaluate_dispatch", "needs_stock_approval"}:
        assert all(line["product"] is not None and line["pieces"] is not None for line in lines)
    if expected["outcome"] in {"clarify_quantity", "clarify_unit"}:
        assert any(line["pieces"] is None for line in lines)
    if "unknown_product" in case["tags"]:
        assert any(line["product"] is None for line in lines)
    if "two_products" in case["tags"]:
        assert len(lines) == 2
    if "missing_quantity" in case["tags"]:
        assert expected["outcome"] == "clarify_quantity"
        assert any(line["pieces"] is None for line in lines)


def test_all_ids_including_text_and_metadata_exist(catalogue):
    for text in _strings(DATA):
        for code in RETAILER_CODE.findall(text):
            assert f"R-{code}" in catalogue["retailers"], text
        for code in PRODUCT_CODE.findall(text):
            assert code.upper() in catalogue["products"], text


def test_five_reworded_pairs_have_identical_expected_decisions():
    pairs = defaultdict(list)
    for case in CASES:
        if "pair" in case:
            assert "reworded_pair" in case["tags"]
            pairs[case["pair"]].append(case)
        else:
            assert "reworded_pair" not in case["tags"]
    assert len(pairs) == 5
    for name, pair in pairs.items():
        assert len(pair) == 2, name
        first, second = pair
        assert first["text"] != second["text"], name
        assert first["expected"] == second["expected"], name
