"""Perceptual image hashing used for *independent* corroboration of a match.

Why this module exists
----------------------
A reverse-image-search provider tells us "this page looks similar". That is the
provider's claim. To avoid trusting it blindly, the pipeline downloads the
thumbnail/preview image that the provider returned for a candidate and compares
it locally against the input photograph using perceptual hashes.

This produces evidence we computed ourselves:
  * dHash (difference hash) - robust to scaling and mild compression
  * aHash (average hash)    - crude brightness structure
  * pHash (DCT hash)        - robust to scaling and gamma changes

Hamming distance between two 64-bit hashes is a *distance*, not a probability.
We deliberately never convert it into a fabricated "confidence percentage".
We only report the raw distances plus a threshold decision, and the thresholds
are recorded in the evidence record so a reviewer can re-derive the verdict.

Limitation (documented, not hidden): the provider thumbnail is a re-encoded,
downscaled derivative of the image on the social post - it is not the post's
original file. A low distance therefore corroborates "the thumbnail the search
provider associated with that post is visually the same picture as our input".
It is not a cryptographic proof of byte equality.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
from PIL import Image, ImageOps

#: Side length of the grayscale grid used by aHash/dHash.
_HASH_SIZE = 8
#: Working size for the DCT used by pHash.
_PHASH_SIZE = 32


def _load_grayscale(data: bytes) -> Image.Image:
    """Decode bytes into a normalized grayscale PIL image.

    EXIF orientation is applied first so that a rotated-on-disk photo hashes the
    same as the visually identical upright copy.
    """
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image)
    return image.convert("L")


def _bits_to_hex(bits: np.ndarray) -> str:
    """Pack a boolean array (row-major) into a lowercase hex string."""
    packed = np.packbits(bits.astype(np.uint8).flatten())
    return packed.tobytes().hex()


def average_hash(data: bytes) -> str:
    """64-bit aHash: each pixel brighter than the mean becomes a 1 bit."""
    image = _load_grayscale(data).resize((_HASH_SIZE, _HASH_SIZE), Image.Resampling.LANCZOS)
    pixels = np.asarray(image, dtype=np.float64)
    return _bits_to_hex(pixels > pixels.mean())


def difference_hash(data: bytes) -> str:
    """64-bit dHash: compares each pixel with its right-hand neighbour."""
    image = _load_grayscale(data).resize(
        (_HASH_SIZE + 1, _HASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(image, dtype=np.float64)
    return _bits_to_hex(pixels[:, 1:] > pixels[:, :-1])


def _dct_1d(matrix: np.ndarray) -> np.ndarray:
    """Type-II DCT along the last axis (small, dependency-free implementation)."""
    n = matrix.shape[-1]
    k = np.arange(n)
    basis = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    return matrix @ basis.T


def perceptual_hash(data: bytes) -> str:
    """64-bit pHash from the low-frequency 8x8 block of a 32x32 DCT."""
    image = _load_grayscale(data).resize((_PHASH_SIZE, _PHASH_SIZE), Image.Resampling.LANCZOS)
    pixels = np.asarray(image, dtype=np.float64)
    dct = _dct_1d(_dct_1d(pixels).T).T
    low = dct[:_HASH_SIZE, :_HASH_SIZE]
    # Exclude the DC term from the median so overall brightness does not bias it.
    median = np.median(low.flatten()[1:])
    return _bits_to_hex(low > median)


def hamming_distance(hex_a: str, hex_b: str) -> int:
    """Number of differing bits between two equal-length hex hashes."""
    a = bytes.fromhex(hex_a)
    b = bytes.fromhex(hex_b)
    if len(a) != len(b):
        raise ValueError("cannot compare hashes of different lengths")
    return int(
        np.unpackbits(np.frombuffer(a, dtype=np.uint8) ^ np.frombuffer(b, dtype=np.uint8)).sum()
    )


@dataclass(frozen=True)
class PerceptualFingerprint:
    """The three perceptual hashes of one image, plus its decoded dimensions."""

    ahash: str
    dhash: str
    phash: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint(data: bytes) -> PerceptualFingerprint:
    """Compute all perceptual hashes for the given image bytes."""
    with Image.open(io.BytesIO(data)) as probe:
        oriented = ImageOps.exif_transpose(probe)
        width, height = oriented.size
    return PerceptualFingerprint(
        ahash=average_hash(data),
        dhash=difference_hash(data),
        phash=perceptual_hash(data),
        width=int(width),
        height=int(height),
    )


#: Bit-distance thresholds. Chosen conservatively and recorded in the evidence
#: record so the verdict can be recomputed by a third party.
DHASH_MAX_DISTANCE = 10
PHASH_MAX_DISTANCE = 10


@dataclass(frozen=True)
class SimilarityReport:
    """Locally computed comparison between the input image and a candidate."""

    dhash_distance: int
    phash_distance: int
    ahash_distance: int
    dhash_threshold: int
    phash_threshold: int
    corroborated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare(a: PerceptualFingerprint, b: PerceptualFingerprint) -> SimilarityReport:
    """Compare two fingerprints and apply the documented thresholds.

    `corroborated` requires BOTH dHash and pHash to agree; a single hash family
    is easier to fool, and requiring agreement lowers the false-positive rate.
    """
    d_dist = hamming_distance(a.dhash, b.dhash)
    p_dist = hamming_distance(a.phash, b.phash)
    a_dist = hamming_distance(a.ahash, b.ahash)
    return SimilarityReport(
        dhash_distance=d_dist,
        phash_distance=p_dist,
        ahash_distance=a_dist,
        dhash_threshold=DHASH_MAX_DISTANCE,
        phash_threshold=PHASH_MAX_DISTANCE,
        corroborated=d_dist <= DHASH_MAX_DISTANCE and p_dist <= PHASH_MAX_DISTANCE,
    )
