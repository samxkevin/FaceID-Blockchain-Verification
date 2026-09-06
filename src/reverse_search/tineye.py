"""TinEye reverse-image search - accepts a LOCAL file directly.

Why this provider is included
-----------------------------
TinEye's API takes the image bytes in a multipart POST. That removes the entire
"the provider needs a public URL" problem: with `--provider tineye` the input
photograph never has to be uploaded to a third-party file bin first.

TinEye is also strictly an *exact/derivative* image index rather than a
"visually similar" index: a TinEye hit means the same image (possibly cropped,
resized or re-encoded) was found on that page. That is materially stronger
evidence than a Google Lens visual match, so every TinEye result is recorded as
MATCH_EXACT.

Trade-off (documented, not hidden): TinEye's crawl coverage of logged-in social
platforms is thinner than Google's, so it may return nothing for an image that
Lens finds. The pipeline supports both and records which one was used.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.core.errors import ProviderConfigError, ProviderRequestError
from src.reverse_search.base import MATCH_EXACT, Candidate, SearchResult

ENDPOINT = "https://api.tineye.com/rest/search/"


class TinEyeProvider:
    """Adapter around the TinEye REST API using direct file upload."""

    name = "TinEye API"
    engine = "tineye"
    #: This provider takes raw bytes - no public URL is required.
    accepts_local_file = True

    def __init__(self, api_key: str | None = None, timeout: int = 120) -> None:
        self.api_key = api_key or os.getenv("TINEYE_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            raise ProviderConfigError(
                "TINEYE_API_KEY is not configured.",
                remedy=(
                    "Get a key at https://services.tineye.com and set TINEYE_API_KEY=... "
                    "in your .env file, or use --provider serpapi instead."
                ),
            )

    def _request(self, image_path: Path) -> dict[str, Any]:
        try:
            with open(image_path, "rb") as handle:
                response = requests.post(
                    ENDPOINT,
                    headers={"x-api-key": self.api_key},
                    files={"image": (image_path.name, handle, "application/octet-stream")},
                    data={"limit": "100", "sort": "score", "order": "desc"},
                    timeout=self.timeout,
                )
        except requests.Timeout as exc:
            raise ProviderRequestError(
                f"TinEye did not respond within {self.timeout}s.",
                remedy="Retry, or raise the timeout.",
            ) from exc
        except requests.RequestException as exc:
            raise ProviderRequestError(
                f"Could not reach the TinEye API: {exc}",
                remedy="Check your network connection.",
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderConfigError(
                f"TinEye rejected the API key (HTTP {response.status_code}).",
                remedy="Verify TINEYE_API_KEY in your .env file.",
            )
        if response.status_code == 429:
            raise ProviderRequestError(
                "TinEye rate limit or bundle exhausted (HTTP 429).",
                remedy="Wait and retry, or top up your TinEye search bundle.",
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                f"TinEye returned HTTP {response.status_code}: {response.text[:300]}",
                remedy="Confirm the image is a supported format under TinEye's size limit.",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderRequestError(
                "TinEye returned a response that was not valid JSON.",
                remedy="Retry the search.",
            ) from exc

    @staticmethod
    def _parse(data: dict[str, Any]) -> tuple[list[Candidate], dict[str, int]]:
        """Flatten TinEye's match/backlink structure into Candidates.

        TinEye nests results as matches -> backlinks; each backlink is a page
        where that image copy was found. Each backlink becomes one candidate.
        """
        results = (data.get("results") or {}) if isinstance(data, dict) else {}
        matches = results.get("matches") or []
        by_url: dict[str, Candidate] = {}
        position = 0

        for match in matches:
            if not isinstance(match, dict):
                continue
            thumbnail = str(match.get("image_url") or "")
            for backlink in match.get("backlinks") or []:
                if not isinstance(backlink, dict):
                    continue
                url = backlink.get("backlink") or backlink.get("url") or ""
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                if url not in by_url:
                    by_url[url] = Candidate(
                        url=url,
                        title=str(backlink.get("crawl_date") or match.get("domain") or ""),
                        source=str(match.get("domain") or ""),
                        thumbnail=thumbnail,
                        # TinEye indexes exact/derivative copies only.
                        match_type=MATCH_EXACT,
                        provider_section="matches.backlinks",
                        provider_position=position,
                    )
                    position += 1

        return list(by_url.values()), {"matches": len(matches), "backlinks": len(by_url)}

    def search(self, image_reference: str) -> SearchResult:
        """Run a TinEye search by uploading the local file directly."""
        path = Path(image_reference).expanduser()
        if not path.is_file():
            raise ProviderConfigError(
                f"TinEye needs a readable local image file, got: {image_reference}",
                remedy="Pass a valid --image path.",
            )
        data = self._request(path)
        candidates, counts = self._parse(data)
        return SearchResult(
            provider=self.name,
            provider_engine=self.engine,
            # Record the file identity, never the operator's directory layout.
            query_image_reference=f"local-file:{path.name}",
            candidates=candidates,
            searched_at_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            section_counts=counts,
        )
