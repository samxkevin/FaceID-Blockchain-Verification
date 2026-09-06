"""Ranking, tiering and selection of the social match.

All provider responses here are fixtures. No live API is called; the SerpAPI
parser is exercised against a captured-shape payload so the mapping from
response sections to match types is genuinely tested.
"""

from __future__ import annotations

import pytest

from src.core.errors import NoSocialMatchError
from src.match.selection import (
    TIER_CORROBORATED_VISUAL,
    TIER_PROVIDER_EXACT,
    TIER_PROVIDER_VISUAL,
    TIER_VERIFIED_EXACT,
    select_social_match,
    tier_for,
)
from src.reverse_search.base import MATCH_EXACT, MATCH_PAGE, MATCH_VISUAL, Candidate, SearchResult


def make_result(candidates: list[Candidate]) -> SearchResult:
    return SearchResult(
        provider="test",
        provider_engine="test",
        query_image_reference="https://example.com/i.png",
        candidates=candidates,
        searched_at_utc="2026-01-01T00:00:00+00:00",
        section_counts={},
    )


def candidate(url, match_type=MATCH_VISUAL, position=0, thumbnail="") -> Candidate:
    return Candidate(
        url=url,
        title="t",
        source="s",
        thumbnail=thumbnail,
        match_type=match_type,
        provider_position=position,
    )


class TestSelection:
    def test_exact_match_beats_visual_match(self):
        result = make_result(
            [
                candidate("https://x.com/a/status/1", MATCH_VISUAL, 0),
                candidate("https://www.instagram.com/p/aaa/", MATCH_EXACT, 9),
            ]
        )
        selection = select_social_match(result)
        assert selection.candidate.url == "https://www.instagram.com/p/aaa/"
        assert selection.tier == TIER_PROVIDER_EXACT

    def test_exact_match_wins_even_when_listed_last(self):
        candidates = [candidate(f"https://x.com/u/status/{i}", MATCH_VISUAL, i) for i in range(10)]
        candidates.append(candidate("https://x.com/u/status/99", MATCH_EXACT, 99))
        selection = select_social_match(make_result(candidates))
        assert selection.candidate.url.endswith("/99")

    def test_page_match_beats_visual_match(self):
        result = make_result(
            [
                candidate("https://x.com/a/status/1", MATCH_VISUAL, 0),
                candidate("https://x.com/b/status/2", MATCH_PAGE, 5),
            ]
        )
        assert select_social_match(result).candidate.url.endswith("/2")

    def test_non_social_results_are_never_selected(self):
        result = make_result(
            [
                candidate("https://example.com/photo", MATCH_EXACT, 0),
                candidate("https://www.instagram.com/p/aaa/", MATCH_VISUAL, 1),
            ]
        )
        assert select_social_match(result).candidate.url == "https://www.instagram.com/p/aaa/"

    def test_post_shaped_url_preferred_over_login_page(self):
        result = make_result(
            [
                candidate("https://www.instagram.com/accounts/login/", MATCH_VISUAL, 0),
                candidate("https://www.instagram.com/p/real/", MATCH_VISUAL, 1),
            ]
        )
        assert select_social_match(result).candidate.url == "https://www.instagram.com/p/real/"

    def test_selection_is_deterministic_regardless_of_input_order(self):
        base = [
            candidate("https://x.com/a/status/1", MATCH_VISUAL, 3),
            candidate("https://www.instagram.com/p/b/", MATCH_VISUAL, 3),
            candidate("https://www.tiktok.com/@c/video/9", MATCH_VISUAL, 3),
        ]
        first = select_social_match(make_result(list(base))).candidate.url
        second = select_social_match(make_result(list(reversed(base)))).candidate.url
        assert first == second

    def test_no_social_results_raises_a_clear_error(self):
        result = make_result([candidate("https://example.com/a", MATCH_EXACT)])
        with pytest.raises(NoSocialMatchError) as exc:
            select_social_match(result)
        assert "none were on a known social platform" in str(exc.value)
        assert exc.value.remedy

    def test_empty_result_raises(self):
        with pytest.raises(NoSocialMatchError):
            select_social_match(make_result([]))

    def test_require_post_shaped_rejects_utility_only_results(self):
        result = make_result([candidate("https://www.instagram.com/accounts/login/", MATCH_EXACT)])
        with pytest.raises(NoSocialMatchError) as exc:
            select_social_match(result, require_post_shaped=True)
        assert "addressable post" in str(exc.value)

    def test_require_tier_rejects_a_weaker_match(self):
        result = make_result([candidate("https://x.com/a/status/1", MATCH_VISUAL)])
        with pytest.raises(NoSocialMatchError) as exc:
            select_social_match(result, require_tier=TIER_PROVIDER_EXACT)
        assert "below the required" in str(exc.value)

    def test_require_tier_accepts_a_sufficient_match(self):
        result = make_result([candidate("https://x.com/a/status/1", MATCH_EXACT)])
        assert select_social_match(result, require_tier=TIER_PROVIDER_EXACT).tier == TIER_PROVIDER_EXACT

    def test_rationale_and_audit_trail_are_populated(self):
        result = make_result([candidate("https://x.com/a/status/1", MATCH_EXACT)])
        payload = select_social_match(result).to_dict()
        assert payload["evidence_tier"] == TIER_PROVIDER_EXACT
        assert payload["evidence_tier_meaning"]
        assert payload["selection_rationale"]
        assert len(payload["considered_social_candidates"]) == 1


class TestTiers:
    def test_uncorroborated_visual_is_the_weakest_tier(self):
        assert tier_for(candidate("https://x.com/a/status/1", MATCH_VISUAL)) == TIER_PROVIDER_VISUAL

    def test_corroborated_exact_is_the_strongest_tier(self):
        item = candidate("https://x.com/a/status/1", MATCH_EXACT)
        item.corroboration = {"attempted": True, "corroborated": True}
        assert tier_for(item) == TIER_VERIFIED_EXACT

    def test_corroborated_visual_sits_between_the_two(self):
        item = candidate("https://x.com/a/status/1", MATCH_VISUAL)
        item.corroboration = {"attempted": True, "corroborated": True}
        assert tier_for(item) == TIER_CORROBORATED_VISUAL

    def test_a_visual_match_is_never_upgraded_to_exact(self):
        item = candidate("https://x.com/a/status/1", MATCH_VISUAL)
        item.corroboration = {"attempted": True, "corroborated": True, "phash_distance": 0}
        assert tier_for(item) != TIER_VERIFIED_EXACT
        assert tier_for(item) != TIER_PROVIDER_EXACT


class TestCorroboration:
    def test_corroboration_reorders_equal_provider_evidence(self, monkeypatch, image_bytes, make_image):
        """A locally corroborated visual match outranks an uncorroborated one."""
        from src.core import perceptual
        from src.match import selection as selection_module

        different = make_image(seed=99)
        responses = {
            "https://thumb/match.png": image_bytes,
            "https://thumb/other.png": different,
        }

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        monkeypatch.setattr(
            selection_module.requests,
            "get",
            lambda url, **kw: FakeResponse(responses[url]),
        )

        result = make_result(
            [
                candidate("https://x.com/a/status/1", MATCH_VISUAL, 0, "https://thumb/other.png"),
                candidate("https://x.com/b/status/2", MATCH_VISUAL, 1, "https://thumb/match.png"),
            ]
        )
        selection = select_social_match(result, input_fingerprint=perceptual.fingerprint(image_bytes))
        assert selection.candidate.url.endswith("/2")
        assert selection.tier == TIER_CORROBORATED_VISUAL
        assert selection.candidate.corroboration["corroborated"] is True

    def test_failed_thumbnail_fetch_is_recorded_not_fatal(self, monkeypatch, image_bytes):
        from src.core import perceptual
        from src.match import selection as selection_module

        def boom(url, **kwargs):
            raise RuntimeError("blocked by platform")

        monkeypatch.setattr(selection_module.requests, "get", boom)
        result = make_result([candidate("https://x.com/a/status/1", MATCH_VISUAL, 0, "https://t/x.png")])
        selection = select_social_match(result, input_fingerprint=perceptual.fingerprint(image_bytes))
        assert selection.tier == TIER_PROVIDER_VISUAL
        assert selection.candidate.corroboration["attempted"] is True
        assert selection.candidate.corroboration["corroborated"] is False

    def test_missing_thumbnail_is_recorded_as_not_attempted(self, image_bytes):
        from src.core import perceptual

        result = make_result([candidate("https://x.com/a/status/1", MATCH_EXACT, 0, "")])
        selection = select_social_match(result, input_fingerprint=perceptual.fingerprint(image_bytes))
        assert selection.candidate.corroboration["attempted"] is False
        assert selection.tier == TIER_PROVIDER_EXACT
