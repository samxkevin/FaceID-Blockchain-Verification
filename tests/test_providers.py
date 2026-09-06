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

    def test_local_path_now_uses_the_image_api_upload(self, monkeypatch, image_file):
        """A local path is no longer an error: it triggers the Image API upload."""
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse({"image_id": "LOCALID"}),
        )
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse(LENS_PAYLOAD),
        )
        result = self.provider().search(str(image_file))
        assert result.query_image_reference == "serpapi-image-id:LOCALID"

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
        # Both providers can now take a local file: TinEye via multipart search,
        # SerpAPI via the Image API upload -> image_id flow.
        assert TinEyeProvider.accepts_local_file is True
        assert SerpApiLensProvider.accepts_local_file is True

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


class TestSerpApiImageApiUpload:
    """SerpAPI Image API direct-upload flow: POST /image -> image_id -> Lens."""

    def provider(self):
        return SerpApiLensProvider(api_key="test-key")

    def test_successful_upload_returns_image_id(self, monkeypatch, image_file):
        """1. Successful Image API upload."""
        captured = {}

        def fake_post(url, files=None, data=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["files"] = files
            captured["data"] = data
            return FakeResponse({"image_id": "abc123imageid"})

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.post", fake_post)

        image_id = self.provider().upload_image(image_file)

        assert image_id == "abc123imageid"
        assert captured["url"] == "https://serpapi.com/image"
        # multipart/form-data with the file under the 'image' field
        assert "image" in captured["files"]
        assert captured["files"]["image"][0] == image_file.name
        # api_key travels as form data, not in the query string
        assert captured["data"]["api_key"] == "test-key"

    def test_missing_image_id_is_reported(self, monkeypatch, image_file):
        """2. Response without an image_id."""
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse({"status": "ok", "some_other_field": 1}),
        )
        with pytest.raises(ProviderRequestError) as exc:
            self.provider().upload_image(image_file)
        assert "did not contain an 'image_id'" in str(exc.value)
        # The remedy must point at the fallback transport.
        assert "--upload" in exc.value.remedy or "--image-url" in exc.value.remedy

    def test_empty_image_id_is_rejected(self, monkeypatch, image_file):
        """2b. Present but empty image_id is treated as missing."""
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse({"image_id": ""}),
        )
        with pytest.raises(ProviderRequestError):
            self.provider().upload_image(image_file)

    @pytest.mark.parametrize(
        "status,expected",
        [(401, ProviderConfigError), (429, ProviderRequestError), (500, ProviderRequestError)],
    )
    def test_upload_http_failures_are_typed(self, monkeypatch, image_file, status, expected):
        """3a. Upload HTTP failures map to typed errors."""
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse(status_code=status),
        )
        with pytest.raises(expected):
            self.provider().upload_image(image_file)

    def test_upload_api_error_field_is_surfaced(self, monkeypatch, image_file):
        """3b. A JSON body carrying an 'error' field is surfaced."""
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse({"error": "Unsupported image format"}),
        )
        with pytest.raises(ProviderRequestError) as exc:
            self.provider().upload_image(image_file)
        assert "Unsupported image format" in str(exc.value)

    def test_upload_network_failure_is_typed(self, monkeypatch, image_file):
        """3c. Transport-level failure during upload."""
        import requests as _requests

        def boom(*a, **k):
            raise _requests.ConnectionError("connection reset")

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.post", boom)
        with pytest.raises(ProviderRequestError) as exc:
            self.provider().upload_image(image_file)
        assert "Could not reach the SerpAPI Image API" in str(exc.value)

    def test_missing_file_is_rejected_before_any_request(self, monkeypatch, tmp_path):
        """3d. A bad path fails before touching the network."""

        def boom(*a, **k):
            raise AssertionError("no HTTP request should be made")

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.post", boom)
        with pytest.raises(ProviderConfigError):
            self.provider().upload_image(tmp_path / "missing.png")

    def test_lens_is_queried_with_image_id_not_url(self, monkeypatch, image_file):
        """4. Lens call uses image_id when given a local file."""
        captured = {}

        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.post",
            lambda *a, **k: FakeResponse({"image_id": "IMGID42"}),
        )

        def fake_get(url, params=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse(LENS_PAYLOAD)

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.get", fake_get)

        result = self.provider().search(str(image_file))

        assert captured["params"]["engine"] == "google_lens"
        assert captured["params"]["image_id"] == "IMGID42"
        assert "url" not in captured["params"]
        # The record references the SerpAPI-side id, never the local path.
        assert result.query_image_reference == "serpapi-image-id:IMGID42"
        assert str(image_file.parent) not in result.query_image_reference
        assert len(result.exact_matches) == 1

    def test_public_url_still_uses_the_url_parameter(self, monkeypatch):
        """4b. The URL fallback path is preserved unchanged."""
        captured = {}

        def no_post(*a, **k):
            raise AssertionError("a public URL must not trigger an upload")

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.post", no_post)

        def fake_get(url, params=None, timeout=None, **kwargs):
            captured["params"] = params
            return FakeResponse(LENS_PAYLOAD)

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.get", fake_get)

        result = self.provider().search("https://example.com/i.png")

        assert captured["params"]["url"] == "https://example.com/i.png"
        assert "image_id" not in captured["params"]
        assert result.query_image_reference == "https://example.com/i.png"

    def test_no_third_party_bin_is_used_for_direct_upload(self, monkeypatch, image_file):
        """5. Nothing is uploaded to a third-party temporary file bin."""
        from src.reverse_search import transport

        # Any call into the ephemeral-bin transport is a failure.
        def boom(*a, **k):
            raise AssertionError("ephemeral third-party bin must not be used")

        monkeypatch.setattr(transport, "upload_ephemeral", boom)

        posted_hosts = []

        def fake_post(url, **kwargs):
            posted_hosts.append(url)
            return FakeResponse({"image_id": "IMGID99"})

        monkeypatch.setattr("src.reverse_search.serpapi_lens.requests.post", fake_post)
        monkeypatch.setattr(
            "src.reverse_search.serpapi_lens.requests.get",
            lambda *a, **k: FakeResponse(LENS_PAYLOAD),
        )

        self.provider().search(str(image_file))

        # The image was POSTed to SerpAPI and nowhere else.
        assert posted_hosts == ["https://serpapi.com/image"]
        for host, _ in transport.EPHEMERAL_UPLOADERS:
            assert not any(host in url for url in posted_hosts)

    def test_provider_declares_it_accepts_local_files(self):
        """The pipeline relies on this flag to pick the direct-upload transport."""
        assert SerpApiLensProvider.accepts_local_file is True
        assert SerpApiLensProvider.direct_transport_service == "serpapi-image-api"
