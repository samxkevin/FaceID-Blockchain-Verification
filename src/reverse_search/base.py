"""Provider-agnostic data model for reverse-image search.

Every provider adapter converts its own response shape into `Candidate`
objects, so the ranking, corroboration and evidence stages never depend on a
particular vendor's JSON layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

#: How the provider itself classified a result. This is the provider's claim,
#: recorded verbatim - we never upgrade a VISUAL result to EXACT ourselves.
MATCH_EXACT = "EXACT"
MATCH_VISUAL = "VISUAL"
MATCH_PAGE = "PAGE"

#: Ordering used when ranking: stronger provider evidence first.
MATCH_TYPE_RANK = {MATCH_EXACT: 0, MATCH_PAGE: 1, MATCH_VISUAL: 2}


@dataclass
class Candidate:
    """One result returned by a reverse-image-search provider.

    Fields are intentionally flat and JSON-serializable so a candidate can be
    embedded in the evidence record without transformation.
    """

    url: str
    title: str = ""
    source: str = ""
    thumbnail: str = ""
    #: Provider's own classification: EXACT, VISUAL or PAGE.
    match_type: str = MATCH_VISUAL
    #: Name of the provider response array this came from (audit trail).
    provider_section: str = ""
    #: Position within that array as returned by the provider (0-based).
    provider_position: int = 0
    #: Set by the social classifier.
    is_social: bool = False
    social_platform: str = ""
    #: Set by the corroboration stage after we hash the thumbnail ourselves.
    corroboration: dict[str, Any] | None = field(default=None)

    @property
    def domain(self) -> str:
        """Registrable host of the result URL, lowercased, without port."""
        try:
            host = urlparse(self.url).netloc.lower()
        except ValueError:
            return ""
        host = host.split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host

    @property
    def corroborated(self) -> bool:
        """True only when we independently matched the provider thumbnail."""
        return bool(self.corroboration and self.corroboration.get("corroborated"))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "domain": self.domain,
            "match_type": self.match_type,
            "provider_section": self.provider_section,
            "provider_position": self.provider_position,
            "is_social": self.is_social,
            "social_platform": self.social_platform,
        }
        if self.corroboration is not None:
            data["local_corroboration"] = self.corroboration
        return data


@dataclass
class SearchResult:
    """The full, normalized outcome of one reverse-image search."""

    provider: str
    provider_engine: str
    query_image_reference: str
    candidates: list[Candidate]
    searched_at_utc: str
    #: Counts per match type, useful for the evidence record and the CLI.
    section_counts: dict[str, int] = field(default_factory=dict)

    @property
    def exact_matches(self) -> list[Candidate]:
        return [c for c in self.candidates if c.match_type == MATCH_EXACT]

    @property
    def social_candidates(self) -> list[Candidate]:
        return [c for c in self.candidates if c.is_social]

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "engine": self.provider_engine,
            "searched_at_utc": self.searched_at_utc,
            "total_candidates": len(self.candidates),
            "exact_candidates": len(self.exact_matches),
            "social_candidates": len(self.social_candidates),
            "section_counts": dict(sorted(self.section_counts.items())),
        }


class ReverseSearchProvider(Protocol):
    """Interface every provider adapter implements."""

    name: str
    engine: str

    def search(self, image_reference: str) -> SearchResult:
        """Run the search and return normalized candidates."""
        ...
