"""Social-platform classification and post-shape detection."""

from __future__ import annotations

import pytest

from src.match.social import classify, is_social, platform_for_url, registrable_domain


class TestDomainParsing:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.instagram.com/p/abc/", "instagram.com"),
            ("https://Instagram.com:443/p/abc", "instagram.com"),
            ("https://m.facebook.com/x", "m.facebook.com"),
            ("https://user@x.com/a/status/1", "x.com"),
            ("not a url", ""),
        ],
    )
    def test_registrable_domain(self, url, expected):
        assert registrable_domain(url) == expected


class TestPlatformDetection:
    @pytest.mark.parametrize(
        "url,platform",
        [
            ("https://www.instagram.com/p/abc/", "Instagram"),
            ("https://x.com/user/status/1", "X (Twitter)"),
            ("https://twitter.com/user/status/1", "X (Twitter)"),
            ("https://de.linkedin.com/in/someone", "LinkedIn"),
            ("https://www.tiktok.com/@u/video/123", "TikTok"),
            ("https://www.threads.net/@u/post/abc", "Threads"),
            ("https://m.facebook.com/photo.php", "Facebook"),
        ],
    )
    def test_known_platforms_are_detected(self, url, platform):
        assert platform_for_url(url) == platform

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/instagram.com/fake",
            "https://notinstagram.com/p/abc",
            "https://news.bbc.co.uk/story",
            "https://instagram.com.evil.net/p/abc",
        ],
    )
    def test_lookalike_domains_are_not_social(self, url):
        assert platform_for_url(url) == ""
        assert not is_social(url)


class TestPostShape:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.instagram.com/p/CabcDEF123/",
            "https://www.instagram.com/reel/Xyz789/",
            "https://www.instagram.com/some.user/",
            "https://x.com/jack/status/20",
            "https://www.tiktok.com/@user/video/7123456789",
            "https://www.linkedin.com/in/someone/",
            "https://www.reddit.com/r/pics/comments/abc123/title/",
            "https://www.threads.net/@user/post/AbC-1",
            "https://www.facebook.com/page/posts/12345",
        ],
    )
    def test_real_post_urls_are_post_shaped(self, url):
        result = classify(url)
        assert result.is_social
        assert result.is_post_shaped, result.reason

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.instagram.com/accounts/login/",
            "https://www.instagram.com/",
            "https://www.facebook.com/login",
            "https://x.com/i/flow/signup",
            "https://www.linkedin.com/help/linkedin",
            "https://www.tiktok.com/discover/something",
            "https://www.instagram.com/explore/tags/x/",
        ],
    )
    def test_utility_pages_are_social_but_not_post_shaped(self, url):
        result = classify(url)
        assert result.is_social
        assert not result.is_post_shaped, result.reason

    def test_non_social_url_is_fully_negative(self):
        result = classify("https://example.com/gallery/1")
        assert not result.is_social
        assert not result.is_post_shaped
        assert result.platform == ""

    def test_query_string_does_not_break_classification(self):
        assert classify("https://x.com/jack/status/20?s=20&t=abc").is_post_shaped
