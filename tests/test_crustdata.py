"""Transporter lookup with every network call mocked. The lanes are test inputs, not the operator's routes."""
import httpx
import pytest

from trex import crustdata

LANE = "Kanpur to Lucknow"


def company(name="Test Roadways", website="https://test.example", description="Road transport by truck.", city="Kanpur", state="Uttar Pradesh", size="11-50", url="https://profile.example/test", specialities=None):
    return {
        "basic_info": {"name": name, "website": website, "description": description, "employee_count_range": size, "professional_network_url": url},
        "locations": {"city": city, "state": state, "country": "IND", "headquarters": f"{city}, {state}, India"},
        "taxonomy": {"professional_network_industry": "Truck Transportation", "professional_network_specialities": specialities or []},
    }


@pytest.fixture
def api(monkeypatch):
    """Replace httpx.post. Set api.reply to a list of companies, a Response or an exception."""

    class Api:
        reply: object = []
        calls: list = []

    Api.calls = []
    monkeypatch.setenv("CRUSTDATA_API_KEY", "test-key-value")
    monkeypatch.setattr(crustdata, "load_dotenv", lambda *a, **k: None)

    def post(url, headers=None, json=None, timeout=None):
        Api.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(Api.reply, Exception):
            raise Api.reply
        if isinstance(Api.reply, httpx.Response):
            return Api.reply
        return httpx.Response(200, json={"companies": Api.reply})

    monkeypatch.setattr(crustdata.httpx, "post", post)
    return Api


def test_shape_and_values_come_from_crustdata(api):
    api.reply = [company()]
    out = crustdata.find_transporters(LANE, "dry")
    assert set(out) == {"lane", "vehicle_type", "transporters", "count", "status", "note"}
    assert (out["lane"], out["vehicle_type"], out["status"], out["count"]) == (LANE, "dry", "ok", 1)
    row = out["transporters"][0]
    assert set(row) == {"name", "website", "location", "size", "why_matched", "source_url"}
    assert row["name"] == "Test Roadways"
    assert row["website"] == "https://test.example"
    assert row["location"] == "Kanpur, Uttar Pradesh"
    assert row["size"] == "11-50"
    assert row["source_url"] == "https://profile.example/test"
    assert "Truck Transportation" in row["why_matched"]


def test_blank_or_missing_fields_become_null(api):
    bare = company(website="", size="", url=None)
    del bare["basic_info"]["description"]
    api.reply = [bare]
    row = crustdata.find_transporters(LANE, "dry")["transporters"][0]
    assert row["website"] is None and row["size"] is None and row["source_url"] is None


def test_request_shape_and_timeout(api):
    api.reply = [company()]
    crustdata.find_transporters("Delhi to Jaipur", "refrigerated")
    call = api.calls[0]
    assert call["url"] == "https://api.crustdata.com/company/search"
    assert call["headers"]["authorization"] == "Bearer test-key-value"
    assert call["headers"]["x-api-version"] == "2025-11-01"
    assert call["timeout"] == 20
    assert call["json"]["limit"] == 25
    fields = {c["field"]: c["value"] for c in call["json"]["filters"]["conditions"]}
    assert fields["locations.country"] == ["IND"]
    assert fields["locations.city"] == ["Delhi", "New Delhi", "Jaipur"]


def test_only_company_search_is_ever_called(api):
    api.reply = [company()]
    for kind in ("dry", "refrigerated"):
        crustdata.find_transporters(LANE, kind)
    assert {c["url"] for c in api.calls} == {"https://api.crustdata.com/company/search"}


@pytest.mark.parametrize("name,description", [
    ("Siyaram Cold Storage Pvt Ltd", "A warehouse."),
    ("Blue Dart Air Cargo", "Parcels by plane."),
    ("Test Exim", "International freight forwarder."),
    ("Test Ocean", "Ocean freight and maritime agency."),
    ("Road Help", "An AI roadside assistance platform."),
    ("Happy Tours", "Holiday tours and travels."),
    ("Taj Holidays India", "Road transport by truck."),
    ("State Road Transport", "A public passenger road transport corporation."),
    ("Truck Jobs", "A truck driving app that aggregates job opportunities."),
])
def test_not_road_transport_is_dropped(api, name, description):
    api.reply = [company(name=name, description=description)]
    out = crustdata.find_transporters(LANE, "dry")
    assert out["status"] == "empty" and out["transporters"] == []
    assert out["note"]


@pytest.mark.parametrize("description", [
    "Multimodal: road freight, air freight, sea freight and customs.",
    "Full truck load, local courier and home relocation.",
])
def test_road_carrier_with_other_services_is_kept(api, description):
    api.reply = [company(description=description)]
    assert crustdata.find_transporters(LANE, "dry")["count"] == 1


def test_other_primary_industry_is_dropped(api):
    other = company()
    other["taxonomy"]["professional_network_industry"] = "Warehousing and Storage"
    api.reply = [other]
    assert crustdata.find_transporters(LANE, "dry")["status"] == "empty"


def test_same_website_is_listed_once(api):
    api.reply = [company(name="Brand A", website="http://www.same.example"), company(name="Brand B", website="https://same.example/"), company(name="No Site", website=""), company(name="No Site Two", website="")]
    names = [r["name"] for r in crustdata.find_transporters(LANE, "dry")["transporters"]]
    assert names == ["Brand A", "No Site", "No Site Two"]


def test_company_off_the_lane_is_dropped(api):
    api.reply = [company(name="Elsewhere Roadways", city="Khanpur", state="West Bengal")]
    assert crustdata.find_transporters(LANE, "dry")["status"] == "empty"


def test_refrigerated_evidence_ranks_first_and_is_stated(api):
    plain = company(name="Plain Roadways")
    cold = company(name="Frost Carriers", website="", specialities=["Refrigerated Transport"])
    api.reply = [plain, cold]
    rows = crustdata.find_transporters(LANE, "refrigerated")["transporters"]
    assert [r["name"] for r in rows] == ["Frost Carriers", "Plain Roadways"]
    assert "refrigerated" in rows[0]["why_matched"]
    assert "not stated" in rows[1]["why_matched"]


def test_cold_storage_alone_is_not_refrigerated_evidence(api):
    assert crustdata._evidence(company(name="Test Cold Storage")) is None


def test_registry_listing_is_flagged(api):
    api.reply = [company(website="", description="TEST ROADWAYS is a transportation/trucking/railroad company based out of Kanpur.")]
    assert "unverified" in crustdata.find_transporters(LANE, "dry")["transporters"][0]["why_matched"]


def test_limit_is_respected(api):
    api.reply = [company(name=f"Carrier {i}", website=f"https://c{i}.example") for i in range(9)]
    out = crustdata.find_transporters(LANE, "dry", limit=3)
    assert out["count"] == 3 and len(out["transporters"]) == 3


def test_bad_limit_does_not_raise(api):
    api.reply = [company()]
    assert crustdata.find_transporters(LANE, "dry", limit="many")["status"] == "ok"


def test_empty_reply(api):
    api.reply = []
    out = crustdata.find_transporters(LANE, "dry")
    assert out["status"] == "empty" and out["count"] == 0


@pytest.mark.parametrize("reply", [
    httpx.Response(401, text="nope"),
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="<html>not json"),
    httpx.Response(200, json=["a", "list"]),
    httpx.Response(200, json={"companies": "nope"}),
    httpx.Response(200, json={}),
    httpx.TimeoutException("slow"),
    httpx.ConnectError("down"),
])
def test_failures_return_a_plain_error(api, reply):
    api.reply = reply
    out = crustdata.find_transporters(LANE, "dry")
    assert out["status"] == "error" and out["transporters"] == [] and out["count"] == 0
    assert out["note"] and "test-key-value" not in str(out)


def test_timeout_note_is_plain(api):
    api.reply = httpx.ReadTimeout("slow")
    assert "20 seconds" in crustdata.find_transporters(LANE, "dry")["note"]


def test_malformed_company_entries_are_survived(api):
    api.reply = ["junk", {"basic_info": None, "locations": None, "taxonomy": None}, company()]
    out = crustdata.find_transporters(LANE, "dry")
    assert out["status"] == "ok" and out["count"] == 1


def test_missing_key_makes_no_call(api, monkeypatch):
    monkeypatch.delenv("CRUSTDATA_API_KEY")
    out = crustdata.find_transporters(LANE, "dry")
    assert out["status"] == "error" and "not set" in out["note"]
    assert api.calls == []


@pytest.mark.parametrize("lane,kind", [("Kanpur", "dry"), ("", "dry"), (None, "dry"), ("ID-1234 to ID-5678", "dry"), (LANE, "tanker"), (LANE, None)])
def test_bad_input_makes_no_call(api, lane, kind):
    out = crustdata.find_transporters(lane, kind)
    assert out["status"] == "error" and out["count"] == 0
    assert api.calls == []
