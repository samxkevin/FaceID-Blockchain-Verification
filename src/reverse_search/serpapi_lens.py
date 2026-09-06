"""Google Lens reverse-image search through SerpAPI.

Nothing in this module is hardcoded. Every candidate URL, title and source comes
from the live provider response. The only static data here is the mapping from
SerpAPI response sections to our provider-neutral match types.

SerpAPI's Google Lens engine returns several arrays. We treat them differently
because they carry genuinely different evidential weight:

  exact_matches   -> MATCH_EXACT  : Google reports the *same image* on that page
  image_results   -> MATCH_PAGE   : pages that contain the image
  visual_matches  -> MATCH_VISUAL : visually similar, NOT necessarily the same

We never promote a VISUAL result to EXACT. Where the provider gives us no exact
matches, the record says so explicitly.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from src.core.errors import ProviderConfigError, ProviderRequestError
from src.reverse_search.base import (
    MATCH_EXACT,
    MATCH_PAGE,
    MATCH_VISUAL,
    Candidate,
    SearchResult,
)

#: SerpAPI response array -> our neutral match type. Order matters: earlier
#: sections carry stronger evidence and are parsed first.
SECTION_MATCH_TYPES: list[tuple[str, str]] = [
    ("exact_matches", MATCH_EXACT),
    ("image_results", MATCH_PAGE),
    ("image_sources", MATCH_PAGE),
    ("visual_matches", MATCH_VISUAL),
]

ENDPOINT = "https://serpapi.com/search.json"


class SerpApiLensProvider:
    """Adapter around SerpAPI's `google_lens` engine.

    Requires the query image to be reachable at a public URL; see
    `src.reverse_search.transport` for how a local file gets one without us
    hosting anything.
    """

    name = "Google Lens via SerpAPI"
    engine = "google_lens"
    #: Declares to the pipeline that this provider cannot take raw bytes.
    accepts_local_file = False

    def __init__(self, api_key: str | None = None, timeout: int = 90, country: str = "us") -> None:
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY", "")
        self.timeout = timeout
        self.country = country
        if not self.api_key:
            raise ProviderConfigError(
                "SERPAPI_API_KEY is not configured.",
                remedy="Get a key at https://serpapi.com and put SERPAPI_API_KEY=... in your .env file.",
            )

    def _request(self, image_url: str) -> dict[str, Any]:
        params = {
            "engine": self.engine,
            "url": image_url,
            "api_key": self.api_key,
            "hl": "en",
            "country": self.country,
        }
        try:
            response = requests.get(ENDPOINT, params=params, timeout=self.timeout)
        except requests.Timeout as exc:
            raise ProviderRequestError(
                f"SerpAPI did not respond within {self.timeout}s.",
                remedy="Retry, or raise the timeout.",
            ) from exc
        except requests.RequestException as exc:
            raise ProviderRequestError(
                f"Could not reach SerpAPI: {exc}",
                remedy="Check your network connection.",
            ) from exc

        if response.status_code == 401:
            raise ProviderConfigError(
                "SerpAPI rejected the API key (HTTP 401).",
                remedy="Verify SERPAPI_API_KEY in your .env file.",
            )
        if response.status_code == 429:
            raise ProviderRequestError(
                "SerpAPI rate limit or monthly quota exceeded (HTTP 429).",
                remedy="Wait and retry, or check your SerpAPI plan usage.",
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                f"SerpAPI returned HTTP {response.status_code}: {response.text[:300]}",
                remedy="Confirm the query image URL is publicly reachable.",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                "SerpAPI returned a response that was not valid JSON.",
                remedy="Retry the search.",
            ) from exc

        if isinstance(data, dict) and data.get("error"):
            raise ProviderRequestError(
                f"SerpAPI reported an error: {data['error']}",
                remedy="Confirm the query image URL is public and serves a real image file.",
            )
        return data

    @staticmethod
    def _parse(data: dict[str, Any]) -> tuple[list[Candidate], dict[str, int]]:
        """Convert a raw SerpAPI payload into Candidate objects.

        Deduplicates by URL, always keeping the strongest section a URL appeared
        in (exact beats page beats visual).
        """
        by_url: dict[str, Candidate] = {}
        counts: dict[str, int] = {}

        for section, match_type in SECTION_MATCH_TYPES:
            items = data.get(section) or []
            if not isinstance(items, list) or not items:
                continue
            counts[section] = len(items)
            for position, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                url = item.get("link") or item.get("url") or ""
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                # Earlier sections are stronger; do not let a later, weaker
                # section overwrite an already-recorded exact match.
                if url in by_url:
                    continue
                thumbnail = item.get("thumbnail") or item.get("image") or ""
                by_url[url] = Candidate(
                    url=url,
                    title=str(item.get("title") or ""),
                    source=str(item.get("source") or item.get("displayed_link") or ""),
                    thumbnail=thumbnail if isinstance(thumbnail, str) else "",
                    match_type=match_type,
                    provider_section=section,
                    provider_position=position,
                )
        return list(by_url.values()), counts

    def search(self, image_reference: str) -> SearchResult:
        """Run a live Google Lens search for a publicly reachable image URL."""
        if not str(image_reference).lower().startswith(("http://", "https://")):
            raise ProviderConfigError(
                "The Google Lens engine needs a public image URL, not a local path.",
                remedy="Run the pipeline with --upload, or pass --image-url <public link>.",
            )
        data = self._request(image_reference)
        candidates, counts = self._parse(data)
        return SearchResult(
            provider=self.name,
            provider_engine=self.engine,
            query_image_reference=image_reference,
            candidates=candidates,
            searched_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            section_counts=counts,
        )


# Backwards-compatible functional API (the original v1 entry point).
def reverse_image_search(image_url: str) -> SearchResult:
    """Preserved v1 helper: run a Google Lens search for a public image URL."""
    return SerpApiLensProvider().search(image_url)
