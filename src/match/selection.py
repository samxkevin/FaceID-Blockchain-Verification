"""Deterministic ranking and selection of the social-media match.

Design rules
------------
* No invented confidence scores. Every ranking key is either evidence the
  provider gave us, or a measurement we computed ourselves (perceptual hash
  distance). Nothing is a made-up percentage.
* Fully deterministic. The final tiebreaker is the candidate URL, so the same
  provider response always yields the same selection.
* Exact beats visual, always. A visual match can never outrank an exact match.

Evidence tiers assigned to the selected candidate
-------------------------------------------------
  VERIFIED_EXACT     provider says exact match AND our own perceptual hashing of
                     the provider thumbnail agrees it is the same picture
  PROVIDER_EXACT     provider says exact match, we could not independently check
                     (no thumbnail, or the fetch was blocked)
  CORROBORATED_VISUAL provider says only "visually similar", but our own
                     perceptual comparison says it is the same picture
  PROVIDER_VISUAL    provider says visually similar and we could not corroborate
                     it - the weakest tier

The tier is written into the evidence record and printed by the CLI, so the
strength of the claim is always explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests

from src.core import perceptual
from src.core.errors import NoSocialMatchError
from src.core.hashing import sha256_hex
from src.reverse_search.base import MATCH_EXACT, MATCH_TYPE_RANK, Candidate, SearchResult
from src.match import social

TIER_VERIFIED_EXACT = "VERIFIED_EXACT"
TIER_PROVIDER_EXACT = "PROVIDER_EXACT"
TIER_CORROBORATED_VISUAL = "CORROBORATED_VISUAL"
TIER_PROVIDER_VISUAL = "PROVIDER_VISUAL"

#: Lower is stronger. Used for ranking and for the --require-tier gate.
TIER_RANK = {
    TIER_VERIFIED_EXACT: 0,
    TIER_PROVIDER_EXACT: 1,
    TIER_CORROBORATED_VISUAL: 2,
    TIER_PROVIDER_VISUAL: 3,
}

#: Human-readable statement of exactly what each tier does and does not assert.
TIER_MEANING = {
    TIER_VERIFIED_EXACT: (
        "The search provider reported this page as an exact-image match, and an "
        "independent local perceptual-hash comparison of the provider's thumbnail "
        "agrees it is the same picture."
    ),
    TIER_PROVIDER_EXACT: (
        "The search provider reported this page as an exact-image match. No "
        "independent local comparison was possible (no fetchable thumbnail)."
    ),
    TIER_CORROBORATED_VISUAL: (
        "The search provider reported only a visual/similar match, but an "
        "independent local perceptual-hash comparison of the provider's thumbnail "
        "indicates it is the same picture."
    ),
    TIER_PROVIDER_VISUAL: (
        "The search provider reported a visual/similar match only. This is NOT "
        "evidence that the identical image appears on the page."
    ),
}


@dataclass
class Selection:
    """The chosen candidate plus a full, replayable audit trail."""

    candidate: Candidate
    tier: str
    #: Every social candidate considered, in final ranked order.
    ranked: list[Candidate]
    #: Human-readable explanation of why this candidate won.
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.candidate.to_dict(),
            "evidence_tier": self.tier,
            "evidence_tier_meaning": TIER_MEANING[self.tier],
            "selection_rationale": self.rationale,
            "considered_social_candidates": [c.to_dict() for c in self.ranked],
        }


def annotate_social(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Tag each candidate with its social platform classification."""
    annotated = []
    for candidate in candidates:
        result = social.classify(candidate.url)
        candidate.is_social = result.is_social
        candidate.social_platform = result.platform
        # Stash post-shape on the candidate for ranking without widening the
        # public dataclass surface.
        candidate.__dict__["_post_shaped"] = result.is_post_shaped
        candidate.__dict__["_social_reason"] = result.reason
        annotated.append(candidate)
    return annotated


def corroborate(
    candidate: Candidate,
    input_fingerprint: perceptual.PerceptualFingerprint,
    timeout: int = 30,
) -> None:
    """Download the candidate's thumbnail and compare it to the input ourselves.

    Failures are recorded, never fatal: social platforms and CDNs block
    automated fetches routinely, and an un-corroborated candidate is still a
    legitimate (weaker) result.
    """
    if not candidate.thumbnail:
        candidate.corroboration = {
            "attempted": False,
            "corroborated": False,
            "note": "Provider returned no thumbnail image for this result.",
        }
        return

    try:
        data = requests.get(
            candidate.thumbnail,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FaceID-Verification/2.0)"},
        )
        data.raise_for_status()
        remote = perceptual.fingerprint(data.content)
    except Exception as exc:  # noqa: BLE001 - any failure is simply "not corroborated"
        candidate.corroboration = {
            "attempted": True,
            "corroborated": False,
            "note": f"Thumbnail could not be fetched or decoded: {type(exc).__name__}",
        }
        return

    report = perceptual.compare(input_fingerprint, remote)
    candidate.corroboration = {
        "attempted": True,
        "corroborated": report.corroborated,
        "method": "perceptual hash (dHash + pHash) of the provider thumbnail vs the local input image",
        "thumbnail_sha256": sha256_hex(data.content),
        **report.to_dict(),
        "note": (
            "Compares the provider's thumbnail, which is a downscaled derivative "
            "of the image on the page - not the page's original file."
        ),
    }


def tier_for(candidate: Candidate) -> str:
    """Assign the evidence tier for a candidate."""
    is_exact = candidate.match_type == MATCH_EXACT
    if is_exact and candidate.corroborated:
        return TIER_VERIFIED_EXACT
    if is_exact:
        return TIER_PROVIDER_EXACT
    if candidate.corroborated:
        return TIER_CORROBORATED_VISUAL
    return TIER_PROVIDER_VISUAL


def _sort_key(candidate: Candidate) -> tuple:
    """Deterministic ranking key. Every component is real evidence.

    Order of importance:
      1. evidence tier (exact and/or locally corroborated first)
      2. provider match type (exact > page > visual)
      3. URL is post-shaped rather than a login/utility page
      4. our own pHash distance, when measured (lower = more similar)
      5. the provider's own ordering within its response
      6. URL string, purely to make ties reproducible
    """
    corr = candidate.corroboration or {}
    measured = corr.get("corroborated") is not None and corr.get("attempted")
    phash_distance = corr.get("phash_distance", 999) if measured else 999
    return (
        TIER_RANK[tier_for(candidate)],
        MATCH_TYPE_RANK.get(candidate.match_type, 9),
        0 if candidate.__dict__.get("_post_shaped") else 1,
        phash_distance,
        candidate.provider_position,
        candidate.url,
    )


def select_social_match(
    result: SearchResult,
    input_fingerprint: perceptual.PerceptualFingerprint | None = None,
    corroborate_top_n: int = 5,
    require_tier: str | None = None,
    require_post_shaped: bool = False,
) -> Selection:
    """Rank the social candidates and select the strongest one.

    Args:
        result: the normalized provider response.
        input_fingerprint: perceptual fingerprint of the local input image; when
            supplied, the top candidates are independently corroborated.
        corroborate_top_n: how many candidates to hash-check (each costs one
            HTTP request).
        require_tier: fail unless the winner reaches at least this tier.
        require_post_shaped: fail unless the winner is a real post/profile URL.
    """
    annotate_social(result.candidates)
    social_candidates = [c for c in result.candidates if c.is_social]

    if not social_candidates:
        raise NoSocialMatchError(
            "The reverse-image search returned "
            f"{len(result.candidates)} result(s), but none were on a known social platform.",
            remedy=(
                "This is a genuine negative result, not a bug. Try an image that is "
                "actually published on social media, or try the other provider "
                "(--provider tineye / --provider serpapi)."
            ),
        )

    if require_post_shaped:
        post_shaped = [c for c in social_candidates if c.__dict__.get("_post_shaped")]
        if not post_shaped:
            raise NoSocialMatchError(
                f"{len(social_candidates)} social-domain result(s) were returned, but none "
                "were an addressable post or profile URL (they were login/utility pages).",
                remedy="Drop --require-post-url to accept them, or try another image.",
            )
        social_candidates = post_shaped

    # Pre-rank on provider evidence alone, then spend HTTP requests
    # corroborating only the most promising candidates.
    social_candidates.sort(key=_sort_key)
    if input_fingerprint is not None:
        for candidate in social_candidates[:corroborate_top_n]:
            corroborate(candidate, input_fingerprint)
        social_candidates.sort(key=_sort_key)

    winner = social_candidates[0]
    tier = tier_for(winner)

    if require_tier and TIER_RANK[tier] > TIER_RANK[require_tier]:
        raise NoSocialMatchError(
            f"The best available match reached tier {tier}, below the required {require_tier}.",
            remedy=(
                "Lower or remove --require-tier to record the weaker match honestly, "
                "or try a different image/provider."
            ),
        )

    rationale = (
        f"Selected from {len(social_candidates)} social candidate(s). "
        f"Provider match type: {winner.match_type}. "
        f"Post-shaped URL: {'yes' if winner.__dict__.get('_post_shaped') else 'no'}. "
    )
    corr = winner.corroboration or {}
    if corr.get("attempted"):
        rationale += (
            "Independent perceptual check: "
            + ("PASSED" if corr.get("corroborated") else "did not pass")
            + f" (dHash {corr.get('dhash_distance')}, pHash {corr.get('phash_distance')})."
        )
    else:
        rationale += "No independent perceptual check was possible."

    return Selection(candidate=winner, tier=tier, ranked=social_candidates, rationale=rationale)
