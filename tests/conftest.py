"""Shared fixtures. No test requires an API key, a private key or a network."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_image(width: int = 320, height: int = 240, seed: int = 0) -> bytes:
    """Deterministic synthetic image, so hashes are stable across machines."""
    image = Image.new("RGB", (width, height), (30 + seed * 7 % 200, 60, 90))
    draw = ImageDraw.Draw(image)
    for i in range(0, width, 24):
        draw.rectangle([i, 40 + (i + seed) % 60, i + 12, 140], fill=(200, 40 + i % 200, 20))
    draw.ellipse([width // 3, height // 4, 2 * width // 3, 3 * height // 4], fill=(240, 220, 190))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def image_bytes() -> bytes:
    return _make_image()


@pytest.fixture
def other_image_bytes() -> bytes:
    return _make_image(seed=17)


@pytest.fixture
def image_file(tmp_path: Path, image_bytes: bytes) -> Path:
    path = tmp_path / "input.png"
    path.write_bytes(image_bytes)
    return path


@pytest.fixture
def make_image():
    return _make_image


@pytest.fixture
def sample_evidence(image_file: Path) -> dict:
    """A complete evidence subtree built without touching the network."""
    from src.verification.record import build_evidence

    return build_evidence(
        image_path=image_file,
        image_fingerprint={"perceptual": {"ahash": "00", "dhash": "11", "phash": "22"}},
        face_metadata={
            "detected": True,
            "face_count": 1,
            "detector_model": "hog",
            "encoding_dimensions": 128,
            "encoding_commitment_sha256": "a" * 64,
        },
        search_summary={
            "provider": "Google Lens via SerpAPI",
            "engine": "google_lens",
            "total_candidates": 12,
            "exact_candidates": 2,
            "searched_at_utc": "2026-01-01T00:00:00+00:00",
        },
        selection={
            "selected": {
                "url": "https://www.instagram.com/p/AbCdEf123/",
                "domain": "instagram.com",
                "title": "A post",
                "source": "Instagram",
                "social_platform": "Instagram",
                "match_type": "EXACT",
                "provider_section": "exact_matches",
            },
            "evidence_tier": "VERIFIED_EXACT",
            "evidence_tier_meaning": "provider exact match, locally corroborated",
            "selection_rationale": "top ranked",
            "considered_social_candidates": [],
        },
        transport={"mode": "ephemeral", "url": "https://0x0.st/abc.png", "service": "0x0.st"},
        created_at_utc="2026-01-01T00:00:00+00:00",
    )
