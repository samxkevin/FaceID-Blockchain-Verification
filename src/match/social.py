"""Classifying which result URLs are genuine social-media *posts*.

This module does two things:

1. Maps a result domain to a known social platform.
2. Decides whether the URL looks like an addressable post/profile rather than a
   generic landing, login, help or share page.

Step 2 matters: a Google Lens response frequently contains `instagram.com` links
that are actually `/accounts/login/` or `/p/` share wrappers. Selecting one of
those and calling it a "matching social media post" would be dishonest, so those
URLs are classified as social-domain but NOT post-shaped, and the ranking stage
demotes them.

Nothing here hardcodes a result. It only classifies URLs the provider returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

#: Registrable domain -> human-readable platform name.
SOCIAL_PLATFORMS: dict[str, str] = {
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "tiktok.com": "TikTok",
    "linkedin.com": "LinkedIn",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "reddit.com": "Reddit",
    "pinterest.com": "Pinterest",
    "tumblr.com": "Tumblr",
    "youtube.com": "YouTube",
    "vk.com": "VK",
    "weibo.com": "Weibo",
    "mastodon.social": "Mastodon",
    "bsky.app": "Bluesky",
    "flickr.com": "Flickr",
    "snapchat.com": "Snapchat",
}

#: Per-platform regexes for URL paths that address a specific post or profile.
#: Anchored on the path only, so query strings never affect classification.
POST_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Instagram": [
        re.compile(r"^/(p|reel|reels|tv)/[\w.-]+/?$"),
        re.compile(r"^/[\w.][\w.]{1,29}/?$"),  # profile
        re.compile(r"^/stories/[\w.-]+/\d+/?$"),
    ],
    "Facebook": [
        re.compile(r"^/[\w.-]+/(posts|videos|photos)/[\w.-]+/?$"),
        re.compile(r"^/photo(\.php)?/?$"),
        re.compile(r"^/permalink\.php/?$"),
        re.compile(r"^/groups/[\w.-]+/posts/\d+/?$"),
        re.compile(r"^/watch/?$"),
    ],
    "X (Twitter)": [
        re.compile(r"^/[\w]{1,15}/status/\d+/?$"),
        re.compile(r"^/[\w]{1,15}/?$"),
    ],
    "TikTok": [
        re.compile(r"^/@[\w.-]+/video/\d+/?$"),
        re.compile(r"^/@[\w.-]+/?$"),
    ],
    "LinkedIn": [
        re.compile(r"^/posts/[\w.-]+/?$"),
        re.compile(r"^/in/[\w.-]+/?$"),
        re.compile(r"^/feed/update/[\w:.-]+/?$"),
        re.compile(r"^/company/[\w.-]+/?$"),
    ],
    "Threads": [
        re.compile(r"^/@[\w.-]+/post/[\w-]+/?$"),
        re.compile(r"^/@[\w.-]+/?$"),
    ],
    "Reddit": [
        re.compile(r"^/r/[\w]+/comments/[\w]+(/[\w%-]*)?/?$"),
        re.compile(r"^/user/[\w-]+/?$"),
    ],
    "Pinterest": [re.compile(r"^/pin/[\w-]+/?$")],
    "Tumblr": [re.compile(r"^/post/\d+(/[\w-]*)?/?$")],
    "YouTube": [
        re.compile(r"^/watch/?$"),
        re.compile(r"^/shorts/[\w-]+/?$"),
        re.compile(r"^/@[\w.-]+/?$"),
    ],
    "Bluesky": [re.compile(r"^/profile/[\w.:-]+(/post/[\w]+)?/?$")],
    "Mastodon": [re.compile(r"^/@[\w.-]+(/\d+)?/?$")],
    "Flickr": [re.compile(r"^/photos/[\w@.-]+(/\d+)?/?$")],
    "VK": [re.compile(r"^/(wall-?\d+_\d+|photo-?\d+_\d+|[\w.]+)/?$")],
    "Snapchat": [re.compile(r"^/add/[\w.-]+/?$"), re.compile(r"^/spotlight/[\w-]+/?$")],
    "Weibo": [re.compile(r"^/\d+/[\w]+/?$"), re.compile(r"^/u/\d+/?$")],
}

#: Path prefixes that are never a real post, on any platform.
NON_POST_PREFIXES = (
    "/accounts/login",
    "/login",
    "/signup",
    "/register",
    "/help",
    "/support",
    "/legal",
    "/privacy",
    "/terms",
    "/about",
    "/explore",
    "/search",
    "/directory",
    "/i/flow",
    "/share",
    "/sharer",
    "/intent",
    "/policies",
    "/settings",
    "/download",
    "/business",
    "/developers",
    "/dir/",
    "/pub/dir",
    "/hashtag",
    "/tags",
    "/discover",
)


@dataclass(frozen=True)
class SocialClassification:
    """Result of classifying one URL."""

    is_social: bool
    platform: str
    #: True when the path addresses a specific post or profile.
    is_post_shaped: bool
    reason: str


def registrable_domain(url: str) -> str:
    """Lowercased host of `url` without `www.`, userinfo or port."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def platform_for_url(url: str) -> str:
    """Return the platform name for `url`, or "" when it is not social.

    Subdomains count (e.g. `m.facebook.com`, `de.linkedin.com`), because
    platforms routinely serve the same post from regional hosts.
    """
    host = registrable_domain(url)
    if not host:
        return ""
    for domain, platform in SOCIAL_PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return ""


def classify(url: str) -> SocialClassification:
    """Classify a provider-returned URL as social / post-shaped."""
    platform = platform_for_url(url)
    if not platform:
        return SocialClassification(False, "", False, "domain is not a known social platform")

    try:
        path = urlparse(url).path or "/"
    except ValueError:
        return SocialClassification(True, platform, False, "URL path could not be parsed")

    normalized = path.rstrip("/") or "/"
    lowered = normalized.lower()

    if lowered == "/":
        return SocialClassification(True, platform, False, "platform home page, not a specific post")
    for prefix in NON_POST_PREFIXES:
        if lowered.startswith(prefix):
            return SocialClassification(
                True, platform, False, f"utility page ({prefix}), not a user post"
            )

    for pattern in POST_PATTERNS.get(platform, []):
        if pattern.match(normalized) or pattern.match(normalized + "/"):
            return SocialClassification(True, platform, True, "URL addresses a specific post or profile")

    return SocialClassification(
        True, platform, False, "social domain but the path does not match a known post format"
    )


def is_social(url: str) -> bool:
    """Convenience predicate preserved from v1."""
    return classify(url).is_social
