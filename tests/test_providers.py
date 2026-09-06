"""Provider adapters, tested against fixture payloads with mocked HTTP."""

from __future__ import annotations

import pytest

from src.core.errors import ProviderConfigError, ProviderRequestError
from src.reverse_search.base import MATCH_EXACT, MATCH_PAGE, MATCH_VISUAL
from src.reverse_search.serpapi_lens import SerpApiLensProvider
from src.reverse_search.tineye import TinEyeProvider

LENS_PAYLOAD = {
    "exact_matches": [
        {"title": "Exact post", "link": "https://www.instagram.com/p/EXACT1/", "source": "Instagram",
         "thumbnail": "https://t/1.jpg"},
    ],
    "image_results": [
        {"title": "Page", "link": "https://news.example.com/story", "source": "Example"},
    ],
    "visual_matches": [
        {"title": "Similar", "link": "https://x.com/u/status/1", "source": "X", "thumbnail": "https://t/2.jpg"},
        # Duplicate of the exact match: must NOT be downgraded to VISUAL.
        {"title": "Dup", "link": "https://www.instagram.com/p/EXACT1/", "source": "Instagram"},
        {"title": "No link"},
        {"link": "javascript:alert(1)"},
        "not a dict",
    ],
}


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or "response body"

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestSerpApiParsing:
    def test_sections_map_to_the_correct_match_types(self):
        candidates, counts = SerpApiLensProvider._parse(LENS_PAYLOAD)
        by_url = {c.url: c for c in candidates}
        assert by_url["https://www.instagram.com/p/EXACT1/"].match_type == MATCH_EXACT
        assert by_url["https://news.example.com/story"].match_type == MATCH_PAGE
        assert by_url["https://x.com/u/status/1"].match_type == MATCH_VISUAL
        assert counts["visual_matches"] == 5

    def test_duplicate_url_keeps_the_strongest_section(self):
        candidates, _ = SerpApiLensProvider._parse(LENS_PAYLOAD)
        matching = [c for c in candidates if c.url == "https://www.instagram.com/p/EXACT1/"]
        assert len(matching) == 1
        assert matching[0].match_type == MATCH_EXACT

    def test_malformed_entries_are_skipped(self):
        candidates, _ = SerpApiLensProvider._parse(LENS_PAYLOAD)
        urls = {c.url for c in candidates}
        assert "javascript:alert(1)" not in urls
        assert len(candidates) == 3

    def test_empty_payload_yields_no_candidates(self):
        candidates, counts = SerpApiLensProvider._parse({})
        assert candidates == []
        assert counts == {}

    def test_provider_position_is_preserved(self):
        candidates, _ = SerpApiLensProvider._parse(LENS_PAYLOAD)
        by_url = {c.url: c for c in candidates}
        assert by_url["https://x.com/u/status/1"].provider_position == 0


class TestSerpApiRequestHandling:
    def provider(self):
        return SerpApiLensProvider(api_key="test-key")

    def test_missing_api_key_raises_config_error(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        with pytest.raises(ProviderConfigError) as exc:
            SerpApiLensProvider()
        assert "SERPAPI_API_KEY" in str(exc.value)
        assert "serpapi.com" in exc.value.remedy

    def test_local_path_is_rejected_with_actionable_advice(self):
        with pytest.raises(ProviderConfigError) as exc:
            self.provider().search("/home/user/photo.jpg")
        assert "--upload" in exc.value.remedy

    def test_http_401_reported_as_a_key_problem(self, monkeypatch):
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse(status_code=401),
        )
        with pytest.raises(ProviderConfigError):
            self.provider().search("https://example.com/i.png")

    def test_http_429_reported_as_a_quota_problem(self, monkeypatch):
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse(status_code=429),
        )
        with pytest.raises(ProviderRequestError) as exc:
            self.provider().search("https://example.com/i.png")
        assert "quota" in str(exc.value).lower() or "rate limit" in str(exc.value).lower()

    def test_provider_error_field_is_surfaced(self, monkeypatch):
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse({"error": "Google hasn't returned any results"}),
        )
        with pytest.raises(ProviderRequestError) as exc:
            self.provider().search("https://example.com/i.png")
        assert "Google hasn't returned" in str(exc.value)

    def test_successful_search_returns_a_normalized_result(self, monkeypatch):
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse(LENS_PAYLOAD),
        )
        result = self.provider().search("https://example.com/i.png")
        assert result.provider_engine == "google_lens"
        assert len(result.exact_matches) == 1
        assert result.summary()["total_candidates"] == 3
        assert result.searched_at_utc.endswith("+00:00")


TINEYE_PAYLOAD = {
    "results": {
        "matches": [
            {
                "domain": "instagram.com",
                "image_url": "https://tineye/thumb.jpg",
                "backlinks": [
                    {"backlink": "https://www.instagram.com/p/ABC/", "crawl_date": "2025-01-01"},
                    {"backlink": "https://www.instagram.com/p/ABC/", "crawl_date": "2025-02-01"},
                ],
            },
            {"domain": "example.com", "backlinks": [{"backlink": "https://example.com/a"}]},
        ]
    }
}


class TestTinEye:
    def test_every_backlink_is_recorded_as_an_exact_match(self):
        candidates, counts = TinEyeProvider._parse(TINEYE_PAYLOAD)
        assert all(c.match_type == MATCH_EXACT for c in candidates)
        assert counts["matches"] == 2

    def test_duplicate_backlinks_are_deduplicated(self):
        candidates, _ = TinEyeProvider._parse(TINEYE_PAYLOAD)
        assert len(candidates) == 2

    def test_empty_payload_is_handled(self):
        assert TinEyeProvider._parse({})[0] == []

    def test_provider_declares_it_accepts_local_files(self):
        assert TinEyeProvider.accepts_local_file is True
        assert SerpApiLensProvider.accepts_local_file is False

    def test_missing_api_key_raises_config_error(self, monkeypatch):
        monkeypatch.delenv("TINEYE_API_KEY", raising=False)
        with pytest.raises(ProviderConfigError) as exc:
            TinEyeProvider()
        assert "--provider serpapi" in exc.value.remedy

    def test_missing_file_is_rejected(self):
        with pytest.raises(ProviderConfigError):
            TinEyeProvider(api_key="k").search("/no/such/file.jpg")

    def test_local_path_is_not_leaked_into_the_record(self, monkeypatch, image_file):
        monkeypatch.setattr(
            "src.reverse_search.tineye.requests.post",
            lambda *a, **k: FakeResponse(TINEYE_PAYLOAD),
        )
        result = TinEyeProvider(api_key="k").search(str(image_file))
        assert result.query_image_reference == "local-file:input.png"
        assert str(image_file.parent) not in result.query_image_reference
