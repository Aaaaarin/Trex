"""Transporter companies a person could call when an open purchase order has no truck.

Lookups only. Nothing here contacts a company: the list goes to the team, who decide who to ring.
Send generic public lanes and terms only, never anything from the operator's masked data.
"""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

_URL = "https://api.crustdata.com/company/search"
_TIMEOUT = 20
_INDUSTRIES = ["Truck Transportation", "Freight and Package Transportation", "Transportation/Trucking/Railroad", "Transportation, Logistics, Supply Chain and Storage", "Logistics & Supply Chain"]
_FIELDS = ["basic_info.name", "basic_info.website", "basic_info.professional_network_url", "basic_info.description", "basic_info.employee_count_range", "taxonomy.professional_network_industry", "taxonomy.professional_network_specialities", "locations.headquarters", "locations.city", "locations.state", "locations.country"]
_QUERIES = {"refrigerated": "refrigerated reefer truck cold chain road transport", "dry": "road transport truck fleet full truck load"}
_CITY_ALIASES = {"delhi": ["Delhi", "New Delhi"]}
_LANE = re.compile(r"^\s*([A-Za-z][A-Za-z ]{1,40}?)\s+to\s+([A-Za-z][A-Za-z ]{1,40}?)\s*$", re.IGNORECASE)
_NOT_ROAD_NAME = re.compile(r"\b(cold stor\w*|airlines?|aviation|air cargo|packers|couriers?|travels?|tours?|holidays|cabs?|taxi|conclave)\b", re.IGNORECASE)
_NOT_ROAD_ANY = re.compile(r"\b(roadside|driving app|job opportunit\w*|passenger|tour and travel)\b", re.IGNORECASE)
_NOT_ROAD_UNLESS_TRUCKS = re.compile(r"\b(air freight|air cargo|ocean freight|sea freight|maritime|shipping line|freight forward\w*|customs|courier|cold storage|software)\b", re.IGNORECASE)
_TRUCKS = re.compile(r"\b(road freight|road transport\w*|trucks?|truck ?loads?|fleet|ftl|roadways|roadlines)\b", re.IGNORECASE)
_COLD = re.compile(r"\b(refrigerated|reefers?|cold[- ]chain|cold logistics|temperature[- ](?:controlled|sensitive)|frozen|chilled)\b", re.IGNORECASE)
_REGISTRY = re.compile(r"is a transportation/trucking/railroad company based out of", re.IGNORECASE)


def _result(lane: str, vehicle_type: str, status: str, note: str, transporters: list | None = None) -> dict:
    rows = transporters or []
    return {"lane": lane, "vehicle_type": vehicle_type, "transporters": rows, "count": len(rows), "status": status, "note": note}


def _text(value) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _section(company: dict, name: str) -> dict:
    section = company.get(name)
    return section if isinstance(section, dict) else {}


def _body(cities: list[str], vehicle_type: str, fetch: int) -> dict:
    names = [n for city in cities for n in _CITY_ALIASES.get(city.lower(), [city])]
    conditions = [
        {"field": "locations.country", "type": "in", "value": ["IND"]},
        {"field": "taxonomy.professional_network_industry", "type": "in", "value": _INDUSTRIES},
        {"field": "locations.city", "type": "in", "value": names},
    ]
    return {"filters": {"op": "and", "conditions": conditions}, "search": {"query": _QUERIES[vehicle_type]}, "limit": fetch, "fields": _FIELDS}


def _specialities(taxonomy: dict) -> str:
    items = taxonomy.get("professional_network_specialities")
    return " ".join(str(i) for i in items) if isinstance(items, list) else ""


def _evidence(company: dict) -> str | None:
    basic, taxonomy = _section(company, "basic_info"), _section(company, "taxonomy")
    found = _COLD.search(" ".join([_text(basic.get("name")) or "", _text(basic.get("description")) or "", _specialities(taxonomy)]))
    return found.group(0).lower() if found else None


def _is_road(company: dict) -> bool:
    basic, taxonomy = _section(company, "basic_info"), _section(company, "taxonomy")
    name, description = _text(basic.get("name")), _text(basic.get("description")) or ""
    industry = _text(taxonomy.get("professional_network_industry"))
    if not name or (industry and industry not in _INDUSTRIES):
        return False
    if _NOT_ROAD_NAME.search(name) or _NOT_ROAD_ANY.search(f"{name} {description}"):
        return False
    return not _NOT_ROAD_UNLESS_TRUCKS.search(description) or bool(_TRUCKS.search(f"{name} {description} {_specialities(taxonomy)}"))


def _in_lane(company: dict, cities: list[str]) -> bool:
    locations = _section(company, "locations")
    place = f"{_text(locations.get('city')) or ''} {_text(locations.get('headquarters')) or ''}"
    return any(re.search(rf"\b{re.escape(city)}\b", place, re.IGNORECASE) for city in cities)


def _why(company: dict, vehicle_type: str) -> str:
    basic, locations, taxonomy = _section(company, "basic_info"), _section(company, "locations"), _section(company, "taxonomy")
    industry, city = _text(taxonomy.get("professional_network_industry")), _text(locations.get("city"))
    parts = [f"Crustdata industry {industry}" if industry else "industry not returned", f"based in {city}" if city else "based on this lane by head office address"]
    if vehicle_type == "refrigerated":
        evidence = _evidence(company)
        parts.append(f"profile says {evidence}" if evidence else "refrigerated capability not stated")
    if _REGISTRY.search(_text(basic.get("description")) or ""):
        parts.append("registry-style listing only, unverified")
    return "; ".join(parts)


def _row(company: dict, vehicle_type: str) -> dict:
    basic, locations = _section(company, "basic_info"), _section(company, "locations")
    place = [p for p in (_text(locations.get("city")), _text(locations.get("state"))) if p]
    return {
        "name": _text(basic.get("name")),
        "website": _text(basic.get("website")),
        "location": ", ".join(place) or _text(locations.get("headquarters")),
        "size": _text(basic.get("employee_count_range")),
        "why_matched": _why(company, vehicle_type),
        "source_url": _text(basic.get("professional_network_url")),
    }


def _rank(company: dict, vehicle_type: str) -> tuple:
    has_evidence = vehicle_type == "refrigerated" and _evidence(company) is not None
    return (not has_evidence, not _text(_section(company, "basic_info").get("website")))


def _distinct(companies: list[dict]) -> list[dict]:
    seen, kept = set(), []
    for company in companies:
        website = _text(_section(company, "basic_info").get("website"))
        host = urlparse(website if "//" in website else f"//{website}").netloc.lower().removeprefix("www.") if website else None
        if host is None or host not in seen:
            kept.append(company)
        seen.add(host)
    return kept


def _post(key: str, body: dict) -> tuple[list | None, str]:
    headers = {"authorization": f"Bearer {key}", "x-api-version": "2025-11-01", "content-type": "application/json"}
    try:
        response = httpx.post(_URL, headers=headers, json=body, timeout=_TIMEOUT)
    except httpx.TimeoutException:
        return None, "Crustdata did not answer within 20 seconds."
    except httpx.HTTPError as err:
        return None, f"Could not reach Crustdata ({type(err).__name__})."
    if response.status_code != 200:
        return None, f"Crustdata returned HTTP {response.status_code}."
    try:
        companies = response.json().get("companies")
    except (ValueError, AttributeError):
        return None, "Crustdata returned a reply that could not be read."
    if not isinstance(companies, list):
        return None, "Crustdata returned a reply that could not be read."
    return [c for c in companies if isinstance(c, dict)], ""


def find_transporters(lane: str, vehicle_type: str, limit: int = 5) -> dict:
    """List road transporters based at either end of a lane, for a person to call. Contacts nobody."""
    kind = vehicle_type.strip().lower() if isinstance(vehicle_type, str) else ""
    match = _LANE.match(lane) if isinstance(lane, str) else None
    if not match:
        return _result(lane, vehicle_type, "error", "Lane must read like 'Kanpur to Lucknow'.")
    if kind not in _QUERIES:
        return _result(lane, vehicle_type, "error", "Vehicle type must be refrigerated or dry.")
    load_dotenv(Path.home() / ".trex.env")
    key = os.environ.get("CRUSTDATA_API_KEY")
    if not key:
        return _result(lane, vehicle_type, "error", "CRUSTDATA_API_KEY is not set.")
    limit = max(1, min(limit, 20)) if isinstance(limit, int) else 5
    cities = [match.group(1), match.group(2)]
    companies, problem = _post(key, _body(cities, kind, min(limit * 5, 25)))
    if companies is None:
        return _result(lane, vehicle_type, "error", problem)
    kept = sorted((c for c in companies if _is_road(c) and _in_lane(c, cities)), key=lambda c: _rank(c, kind))
    if not kept:
        return _result(lane, vehicle_type, "empty", f"Crustdata returned {len(companies)} companies, none clearly a road transporter based on this lane.")
    rows = [_row(c, kind) for c in _distinct(kept)[:limit]]
    note = f"{len(rows)} listed from {len(companies)} returned. Based at a lane end, service on the lane not confirmed. For a person to check and call. Nobody has been contacted."
    return _result(lane, vehicle_type, "ok", note, rows)
