import os
from urllib.parse import urlparse

import requests


SOCIAL_HOSTS = {
    "instagram.com", "facebook.com", "x.com", "twitter.com",
    "tiktok.com", "linkedin.com", "threads.net",
}


def _is_social(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().split(":", 1)[0]
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


def reverse_image_search(image_url: str) -> dict:
    """Run a genuine Google Lens search through SerpAPI.

    image_url must be publicly reachable by the provider. No result is hardcoded.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_API_KEY is not configured.")
    if not image_url.startswith(("https://", "http://")):
        raise ValueError("image_url must be an HTTP(S) URL.")

    response = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google_lens", "url": image_url, "api_key": api_key, "hl": "en"},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    candidates = []
    for key in ("visual_matches", "exact_matches"):
        for item in data.get(key, []) or []:
            url = item.get("link") or item.get("url")
            if not url:
                continue
            candidates.append({
                "title": item.get("title", ""),
                "url": url,
                "source": item.get("source", ""),
                "thumbnail": item.get("thumbnail", ""),
                "is_social": _is_social(url),
                "result_type": key,
            })

    return {
        "provider": "Google Lens via SerpAPI",
        "query_image_url": image_url,
        "candidates": candidates,
        "raw_result_count": len(candidates),
    }


def choose_social_match(search_result: dict) -> dict:
    social = [c for c in search_result["candidates"] if c["is_social"]]
    if not social:
        raise RuntimeError(
            "Reverse-image search completed, but no social-media result was returned. "
            "Use a different test image or rerun the search."
        )
    return social[0]
