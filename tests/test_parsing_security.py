import json

import pytest

from looked.errors import AppError
from looked.results import cache_key, csv_export, parameters_from, parse_results
from looked.security import safe_url


def test_ranking_deduplication_missing_fields_social_filters(visual_payload):
    result = parse_results(visual_payload, "visual_matches")
    rows = result["results"]
    assert [row["position"] for row in rows] == [7, 9, 11, None]
    assert rows[0]["match_label"] == "Visual result"  # navigation flag is not evidence
    assert result["duplicates_removed"] == 1
    assert rows[2]["title"] == "Untitled result"
    assert rows[3]["link"] is rows[3]["thumbnail"] is rows[3]["domain"] is None
    assert result["social_domains"] == ["www.instagram.com"]
    assert result["warnings"] == ["Skipped 1 malformed result entries."]
    assert all("similarity" not in row for row in rows)


def test_only_exact_response_section_supports_exact_label():
    payload = {"exact_matches": [{"title": "Mock", "link": "https://example.com/"}]}
    assert parse_results(payload, "exact_matches")["results"][0]["match_label"] == "Provider exact-match result"


@pytest.mark.parametrize("payload", [{"visual_matches": []}, {"search_metadata": {"status": "Success"}}])
def test_empty_completed_searches(payload):
    assert parse_results(payload, "visual_matches")["results"] == []


@pytest.mark.parametrize("payload", [None, [], {}, {"visual_matches": {}}, {"visual_matches": None},
    {"search_metadata": []}, {"visual_matches": [None, "mock"]},
    {"error": "mock failure"}, {"search_metadata": {"status": "Processing"}, "visual_matches": []}])
def test_malformed_responses(payload):
    with pytest.raises(AppError):
        parse_results(payload, "visual_matches")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:image/png,MOCK", "file:///etc/passwd",
    "//example.com/photo", "https://user:pass@example.com/", "http://127.0.0.1/", "http://[::1]/",
    "http://192.168.1.1/", "http://169.254.169.254/", "https://localhost/", "https://host.local/",
    "https://example.com:9000/", "https://example.com/?api_key=OTHERKEY", "https://example.com\\@localhost/",
    "https://example.com/\nmock", "https://example.com/?key=MOCK_SECRET"])
def test_unsafe_links_rejected(url):
    assert safe_url(url, "MOCK_SECRET") is None


def test_key_echo_is_removed_from_results_and_csv_is_formula_safe():
    secret = "MOCK_SECRET"
    result = parse_results({"visual_matches": [
        {"title": "=MOCK_SECRET", "source": secret, "link": "https://example.com/?secret=MOCK_SECRET", "thumbnail": "https://example.com/?api_key=OTHER"},
        {"title": "\t=HYPERLINK(\"mock\")", "link": "https://example.com/mock"}
    ]}, "visual_matches", secret)
    assert secret not in json.dumps(result)
    assert result["results"][0]["link"] is None
    exported = csv_export({"result": result})
    assert "'=\u005bredacted\u005d" in exported
    assert "'\t=HYPERLINK" in exported


def test_no_social_filters_for_unreturned_domains():
    result = parse_results({"visual_matches": [{"link": "https://instagram.com.evil.example/"}]}, "visual_matches")
    assert not result["social_domains"]


@pytest.mark.parametrize("values", [{"type": "products"}, {"hl": "<script>"}, {"country": "usa"}, {"hl": None}])
def test_invalid_search_parameters(values):
    with pytest.raises(AppError):
        parameters_from(values)


def test_cache_parameters_canonicalized():
    assert cache_key("mockhash", {"a": 1, "b": 2}) == cache_key("mockhash", {"b": 2, "a": 1})
    assert cache_key("mockhash", {"a": 1}) != cache_key("otherhash", {"a": 1})
