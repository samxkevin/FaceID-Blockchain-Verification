"""Face detection and encoding.

Scope statement (important, and repeated in the README):
    This module proves only that *a human face was detected and encoded* in the
    input image. A 128-dimensional face encoding is not an identity. This
    project never claims that the encoding names a person, and the encoding is
    never written to the blockchain.

What is recorded downstream is non-biometric metadata: how many faces were
found, the encoding dimensionality, the detector model, and a salted commitment
to the encoding (see `encoding_commitment`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from src.core.errors import AmbiguousFaceError, InputImageError, NoFaceDetectedError
from src.core.hashing import sha256_hex

#: Detector models supported by dlib through face_recognition.
#: "hog" is CPU-fast and the default; "cnn" is slower but more accurate.
SUPPORTED_MODELS = ("hog", "cnn")

#: Image formats we accept. Anything else is rejected with a clear message
#: rather than failing deep inside the detector.
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "TIFF"}

#: Guard rails on input size. Too small and detection is meaningless; too large
#: and detection becomes needlessly slow.
MIN_DIMENSION = 64
MAX_PIXELS = 50_000_000


@dataclass(frozen=True)
class FaceBox:
    """One detected face in top/right/bottom/left pixel coordinates."""

    top: int
    right: int
    bottom: int
    left: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def to_dict(self) -> dict[str, int]:
        return {
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "left": self.left,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class FaceEncodingResult:
    """Outcome of the face stage.

    `primary_index` points at the largest detected face, which is the one whose
    metadata is reported. Sorting by area makes the choice deterministic for a
    given image instead of depending on detector iteration order.
    """

    image_path: str
    image_width: int
    image_height: int
    image_format: str
    detector_model: str
    upsample: int
    boxes: list[FaceBox]
    encodings: list[np.ndarray] = field(repr=False, default_factory=list)
    primary_index: int = 0

    @property
    def face_count(self) -> int:
        return len(self.encodings)

    @property
    def primary_encoding(self) -> np.ndarray:
        return self.encodings[self.primary_index]

    @property
    def primary_box(self) -> FaceBox:
        return self.boxes[self.primary_index]

    @property
    def encoding_dimensions(self) -> int:
        return int(self.primary_encoding.shape[0])

    def encoding_commitment(self) -> str:
        """Salted SHA-256 commitment to the quantized primary encoding.

        Rationale: we want the evidence record to bind to the specific encoding
        that was produced, without publishing a biometric template. Floats are
        quantized to 6 decimals first so the commitment is stable across
        platforms with different float formatting, then hashed with a domain
        separation prefix.

        This is a one-way commitment: it cannot be inverted into an encoding,
        and it is useless for matching against any other face database.
        """
        quantized = ",".join(f"{value:.6f}" for value in self.primary_encoding.tolist())
        payload = f"faceid-encoding-v1|{self.encoding_dimensions}|{quantized}"
        return sha256_hex(payload.encode("utf-8"))

    def to_metadata(self) -> dict[str, Any]:
        """Non-biometric metadata safe to place in the evidence record."""
        return {
            "detected": True,
            "face_count": self.face_count,
            "detector_model": self.detector_model,
            "detector_upsample": self.upsample,
            "encoding_dimensions": self.encoding_dimensions,
            "encoding_commitment_sha256": self.encoding_commitment(),
            "primary_face_box": self.primary_box.to_dict(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "image_format": self.image_format,
        }


def _validate_image(path: Path) -> tuple[int, int, str]:
    """Validate the file before handing it to the detector.

    Returns (width, height, format). Raises InputImageError with an actionable
    remedy for every rejection case.
    """
    if not path.exists():
        raise InputImageError(
            f"Input image does not exist: {path}",
            remedy="Check the --image path. Use an absolute path if unsure.",
        )
    if not path.is_file():
        raise InputImageError(
            f"Input path is not a file: {path}",
            remedy="Pass a single image file, not a directory.",
        )
    if path.stat().st_size == 0:
        raise InputImageError(
            f"Input image is empty (0 bytes): {path}",
            remedy="The file is truncated or was not downloaded fully.",
        )

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            width, height = oriented.size
            fmt = (image.format or "UNKNOWN").upper()
    except UnidentifiedImageError as exc:
        raise InputImageError(
            f"Input file is not a decodable image: {path}",
            remedy="Provide a JPEG, PNG, WEBP, BMP or TIFF photograph.",
        ) from exc
    except OSError as exc:
        raise InputImageError(
            f"Input image could not be read: {path} ({exc})",
            remedy="The file may be corrupt. Try re-exporting the photograph.",
        ) from exc

    if fmt not in SUPPORTED_FORMATS:
        raise InputImageError(
            f"Unsupported image format '{fmt}' for {path}",
            remedy=f"Convert the image to one of: {', '.join(sorted(SUPPORTED_FORMATS))}.",
        )
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise InputImageError(
            f"Image is too small to analyse: {width}x{height} pixels",
            remedy=f"Provide an image at least {MIN_DIMENSION}x{MIN_DIMENSION} pixels.",
        )
    if width * height > MAX_PIXELS:
        raise InputImageError(
            f"Image is too large to analyse: {width}x{height} pixels",
            remedy=f"Downscale the image below {MAX_PIXELS:,} total pixels.",
        )
    return int(width), int(height), fmt


def detect_and_encode(
    image_path: str | Path,
    model: str = "hog",
    upsample: int = 1,
    allow_multiple_faces: bool = True,
) -> FaceEncodingResult:
    """Detect faces in `image_path` and compute 128-d encodings.

    Args:
        image_path: local path to the photograph.
        model: "hog" (fast, CPU) or "cnn" (slower, more accurate).
        upsample: how many times to upscale before detection; raise to 2 to
            find small faces at the cost of speed.
        allow_multiple_faces: when False, more than one detected face is an
            error instead of selecting the largest one.

    Determinism: for a fixed image, model and upsample, dlib's detectors are
    deterministic, and we additionally sort detections by area (then by
    position) so the "primary" face never depends on iteration order.
    """
    path = Path(image_path).expanduser()
    width, height, fmt = _validate_image(path)

    if model not in SUPPORTED_MODELS:
        raise InputImageError(
            f"Unknown detector model '{model}'",
            remedy=f"Use one of: {', '.join(SUPPORTED_MODELS)}.",
        )
    if upsample < 0 or upsample > 3:
        raise InputImageError(
            f"Invalid upsample value {upsample}",
            remedy="Use an upsample between 0 and 3 (1 is a good default).",
        )

    # Imported lazily so that hashing/evidence tests run without dlib installed.
    try:
        import face_recognition
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise InputImageError(
            "The 'face_recognition' package (and its dlib backend) is not installed.",
            remedy="Install it with: pip install -r requirements.txt",
        ) from exc

    image = face_recognition.load_image_file(str(path))
    locations = face_recognition.face_locations(
        image, number_of_times_to_upsample=upsample, model=model
    )

    if not locations:
        raise NoFaceDetectedError(
            f"No face was detected in {path.name} using the '{model}' detector.",
            remedy=(
                "Try --face-model cnn for a more sensitive detector, raise "
                "--face-upsample to 2 for small faces, or use a clearer "
                "front-facing photograph."
            ),
        )

    boxes = [FaceBox(*loc) for loc in locations]
    # Deterministic ordering: largest face first, ties broken by position.
    order = sorted(
        range(len(boxes)),
        key=lambda i: (-boxes[i].area, boxes[i].top, boxes[i].left),
    )
    boxes = [boxes[i] for i in order]
    sorted_locations = [(b.top, b.right, b.bottom, b.left) for b in boxes]

    encodings = face_recognition.face_encodings(
        image, known_face_locations=sorted_locations, num_jitters=1
    )
    if not encodings:
        raise NoFaceDetectedError(
            f"A face region was located in {path.name} but no encoding could be computed.",
            remedy="The face may be too blurred, occluded or steeply angled. Use a clearer photograph.",
        )

    if len(encodings) > 1 and not allow_multiple_faces:
        raise AmbiguousFaceError(
            f"{len(encodings)} faces were detected but single-face mode was requested.",
            remedy="Crop the photograph to one subject, or drop --require-single-face.",
        )

    return FaceEncodingResult(
        image_path=str(path),
        image_width=width,
        image_height=height,
        image_format=fmt,
        detector_model=model,
        upsample=upsample,
        boxes=boxes,
        encodings=[np.asarray(e, dtype=np.float64) for e in encodings],
        primary_index=0,
    )
