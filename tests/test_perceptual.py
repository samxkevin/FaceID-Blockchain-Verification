"""Perceptual hashing: the locally computed corroboration evidence."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.core import perceptual


def reencode(data: bytes, fmt: str = "JPEG", quality: int = 70, scale: float = 1.0) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if scale != 1.0:
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=quality)
    return buffer.getvalue()


class TestHashProperties:
    def test_all_hashes_are_64_bits(self, image_bytes):
        fp = perceptual.fingerprint(image_bytes)
        for value in (fp.ahash, fp.dhash, fp.phash):
            assert len(value) == 16  # 64 bits as hex

    def test_hashing_is_deterministic(self, image_bytes):
        assert perceptual.fingerprint(image_bytes).to_dict() == perceptual.fingerprint(image_bytes).to_dict()

    def test_dimensions_are_reported(self, image_bytes):
        fp = perceptual.fingerprint(image_bytes)
        assert (fp.width, fp.height) == (320, 240)


class TestRobustness:
    def test_identical_bytes_have_zero_distance(self, image_bytes):
        fp = perceptual.fingerprint(image_bytes)
        report = perceptual.compare(fp, fp)
        assert report.dhash_distance == 0
        assert report.phash_distance == 0
        assert report.corroborated

    def test_jpeg_recompression_still_corroborates(self, image_bytes):
        a = perceptual.fingerprint(image_bytes)
        b = perceptual.fingerprint(reencode(image_bytes, quality=60))
        assert perceptual.compare(a, b).corroborated

    def test_downscaling_still_corroborates(self, image_bytes):
        a = perceptual.fingerprint(image_bytes)
        b = perceptual.fingerprint(reencode(image_bytes, scale=0.4))
        assert perceptual.compare(a, b).corroborated

    def test_a_different_image_does_not_corroborate(self, image_bytes, other_image_bytes):
        a = perceptual.fingerprint(image_bytes)
        b = perceptual.fingerprint(other_image_bytes)
        assert not perceptual.compare(a, b).corroborated


class TestReportContents:
    def test_thresholds_are_recorded_so_the_verdict_is_reproducible(self, image_bytes):
        fp = perceptual.fingerprint(image_bytes)
        report = perceptual.compare(fp, fp).to_dict()
        assert report["dhash_threshold"] == perceptual.DHASH_MAX_DISTANCE
        assert report["phash_threshold"] == perceptual.PHASH_MAX_DISTANCE

    def test_no_fabricated_confidence_score_is_produced(self, image_bytes):
        report = perceptual.compare(
            perceptual.fingerprint(image_bytes), perceptual.fingerprint(image_bytes)
        ).to_dict()
        for key in report:
            assert "confidence" not in key
            assert "probability" not in key
            assert "score" not in key

    def test_all_reported_distances_are_integers(self, image_bytes, other_image_bytes):
        report = perceptual.compare(
            perceptual.fingerprint(image_bytes), perceptual.fingerprint(other_image_bytes)
        ).to_dict()
        for key, value in report.items():
            if "distance" in key or "threshold" in key:
                assert isinstance(value, int)


class TestHammingDistance:
    def test_known_distance(self):
        assert perceptual.hamming_distance("00", "ff") == 8
        assert perceptual.hamming_distance("0f", "0f") == 0

    def test_mismatched_lengths_are_rejected(self):
        with pytest.raises(ValueError):
            perceptual.hamming_distance("00", "ffff")
