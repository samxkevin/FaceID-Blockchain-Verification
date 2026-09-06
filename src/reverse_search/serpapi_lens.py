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
from pathlib import Path
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

#: SerpAPI's Image API. Accepts a multipart file upload and returns an
#: `image_id` that the Google Lens engine can query directly.
UPLOAD_ENDPOINT = "https://serpapi.com/image"


class SerpApiLensProvider:
    """Adapter around SerpAPI's `google_lens` engine.

    Two ways to submit the query image, in order of preference:

    1. **SerpAPI Image API direct upload** (default). The local file is POSTed
       as multipart/form-data to https://serpapi.com/image, which returns an
       `image_id`. Lens is then queried with `image_id=...`. The photograph goes
       only to SerpAPI - the search provider we are already using - and is never
       published to a third-party temporary file bin.
    2. **Public URL** (fallback). Lens is queried with `url=...`, using either a
       link the operator already controls (`--image-url`) or an ephemeral
       anonymous upload (`--upload`). See `src.reverse_search.transport`.
    """

    name = "Google Lens via SerpAPI"
    engine = "google_lens"
    #: This provider can take a local file directly, via the Image API upload.
    accepts_local_file = True
    #: Wording used by the CLI/evidence record for the direct-upload transport.
    direct_transport_service = "serpapi-image-api"
    direct_transport_note = (
        "SerpAPI Image API direct upload: the local file was POSTed to "
        "https://serpapi.com/image and queried by image_id. It was not published "
        "to any third-party temporary file bin."
    )

    def __init__(self, api_key: str | None = None, timeout: int = 90, country: str = "us") -> None:
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY", "")
        self.timeout = timeout
        self.country = country
        if not self.api_key:
            raise ProviderConfigError(
                "SERPAPI_API_KEY is not configured.",
                remedy="Get a key at https://serpapi.com and put SERPAPI_API_KEY=... in your .env file.",
            )

    def upload_image(self, image_path: str | Path) -> str:
        """Upload a local file to SerpAPI's Image API and return its `image_id`.

        This is the direct-upload half of the flow: no public URL, no
        third-party file bin. Raises ProviderRequestError if the upload fails or
        the response carries no usable `image_id`.
        """
        path = Path(image_path).expanduser()
        if not path.is_file():
            raise ProviderConfigError(
                f"Cannot upload, file not found: {path}",
                remedy="Check the --image path.",
            )

        try:
            with open(path, "rb") as handle:
                response = requests.post(
                    UPLOAD_ENDPOINT,
                    files={"image": (path.name, handle, "application/octet-stream")},
                    data={"api_key": self.api_key},
                    timeout=self.timeout,
                )
        except requests.Timeout as exc:
            raise ProviderRequestError(
                f"The SerpAPI Image API did not respond within {self.timeout}s.",
                remedy="Retry, or fall back to --upload / --image-url.",
            ) from exc
        except requests.RequestException as exc:
            raise ProviderRequestError(
                f"Could not reach the SerpAPI Image API: {exc}",
                remedy="Check your network connection.",
            ) from exc

        if response.status_code == 401:
            raise ProviderConfigError(
                "SerpAPI rejected the API key on image upload (HTTP 401).",
                remedy="Verify SERPAPI_API_KEY in your .env file.",
            )
        if response.status_code == 429:
            raise ProviderRequestError(
                "SerpAPI rate limit or quota exceeded on image upload (HTTP 429).",
                remedy="Wait and retry, or check your SerpAPI plan usage.",
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                f"SerpAPI Image API returned HTTP {response.status_code}: {response.text[:300]}",
                remedy="Confirm the file is a supported image within SerpAPI's size limit.",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                "The SerpAPI Image API returned a response that was not valid JSON.",
                remedy="Retry, or fall back to --upload / --image-url.",
            ) from exc

        if isinstance(data, dict) and data.get("error"):
            raise ProviderRequestError(
                f"The SerpAPI Image API reported an error: {data['error']}",
                remedy="Confirm the file is a supported image within SerpAPI's size limit.",
            )

        image_id = data.get("image_id") if isinstance(data, dict) else None
        if not image_id or not isinstance(image_id, str):
            raise ProviderRequestError(
                "The SerpAPI Image API response did not contain an 'image_id'. "
                f"Received keys: {sorted(data) if isinstance(data, dict) else type(data).__name__}",
                remedy=(
                    "Retry the upload, or fall back to the URL transport with "
                    "--upload or --image-url <public link>."
                ),
            )
        return image_id

    def _request(self, image_reference: str, by_image_id: bool = False) -> dict[str, Any]:
        """Query the Lens engine, either by `image_id` or by public `url`."""
        params = {
            "engine": self.engine,
            "image_id" if by_image_id else "url": image_reference,
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
        """Run a live Google Lens search.

        `image_reference` is either a public http(s) URL (fallback transport) or
        a local file path, in which case the SerpAPI Image API direct-upload
        flow is used: upload -> image_id -> Lens query by image_id.
        """
        reference = str(image_reference)
        if reference.lower().startswith(("http://", "https://")):
            data = self._request(reference, by_image_id=False)
            recorded_reference = reference
        else:
            image_id = self.upload_image(reference)
            data = self._request(image_id, by_image_id=True)
            # Record the SerpAPI-side identifier, never the operator's path.
            recorded_reference = f"serpapi-image-id:{image_id}"

        candidates, counts = self._parse(data)
        return SearchResult(
            provider=self.name,
            provider_engine=self.engine,
            query_image_reference=recorded_reference,
            candidates=candidates,
            searched_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            section_counts=counts,
        )


# Backwards-compatible functional API (the original v1 entry point).
def reverse_image_search(image_url: str) -> SearchResult:
    """Preserved v1 helper: run a Google Lens search for a public image URL."""
    return SerpApiLensProvider().search(image_url)
