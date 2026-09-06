"""Input validation for the face stage.

Detection accuracy itself depends on dlib and on real photographs, so these
tests focus on the validation, error-message and metadata contracts, which are
what the pipeline's reliability actually rests on. Tests needing dlib are
skipped automatically when it is unavailable.
"""

from __future__ import annotations

import pytest

from src.core.errors import InputImageError, NoFaceDetectedError
from src.face.encoder import MIN_DIMENSION, SUPPORTED_MODELS, detect_and_encode

dlib_available = True
try:  # pragma: no cover - environment dependent
    import face_recognition  # noqa: F811
except Exception:  # noqa: BLE001
    dlib_available = False

requires_dlib = pytest.mark.skipif(not dlib_available, reason="face_recognition/dlib not installed")


class TestInputValidation:
    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(tmp_path / "nope.jpg")
        assert "does not exist" in str(exc.value)
        assert "--image" in exc.value.remedy

    def test_directory_is_rejected(self, tmp_path):
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(tmp_path)
        assert "not a file" in str(exc.value)

    def test_empty_file_is_rejected(self, tmp_path):
        path = tmp_path / "empty.jpg"
        path.write_bytes(b"")
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(path)
        assert "empty" in str(exc.value)

    def test_non_image_file_is_rejected(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("this is not an image at all", encoding="utf-8")
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(path)
        assert "not a decodable image" in str(exc.value)

    def test_tiny_image_is_rejected(self, tmp_path):
        from PIL import Image

        path = tmp_path / "tiny.png"
        Image.new("RGB", (10, 10)).save(path)
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(path)
        assert str(MIN_DIMENSION) in exc.value.remedy

    def test_unknown_detector_model_is_rejected(self, image_file):
        with pytest.raises(InputImageError) as exc:
            detect_and_encode(image_file, model="magic")
        assert all(m in exc.value.remedy for m in SUPPORTED_MODELS)

    def test_out_of_range_upsample_is_rejected(self, image_file):
        with pytest.raises(InputImageError):
            detect_and_encode(image_file, upsample=9)


@requires_dlib
class TestDetection:
    def test_image_without_a_face_raises_a_helpful_error(self, image_file):
        with pytest.raises(NoFaceDetectedError) as exc:
            detect_and_encode(image_file)
        assert "No face was detected" in str(exc.value)
        assert "--face-model cnn" in exc.value.remedy


class TestMetadataContract:
    def test_metadata_exposes_no_raw_encoding(self):
        """The metadata written into the evidence record must not contain the vector."""
        import numpy as np

        from src.face.encoder import FaceBox, FaceEncodingResult

        result = FaceEncodingResult(
            image_path="/private/path/photo.jpg",
            image_width=800,
            image_height=600,
            image_format="JPEG",
            detector_model="hog",
            upsample=1,
            boxes=[FaceBox(10, 90, 90, 10)],
            encodings=[np.linspace(0, 1, 128)],
        )
        metadata = result.to_metadata()
        assert metadata["face_count"] == 1
        assert metadata["encoding_dimensions"] == 128
        assert len(metadata["encoding_commitment_sha256"]) == 64
        assert "/private/path" not in str(metadata)
        assert "encodings" not in metadata

    def test_encoding_commitment_is_deterministic_and_sensitive(self):
        import numpy as np

        from src.face.encoder import FaceBox, FaceEncodingResult

        def build(vector):
            return FaceEncodingResult(
                image_path="p.jpg",
                image_width=800,
                image_height=600,
                image_format="JPEG",
                detector_model="hog",
                upsample=1,
                boxes=[FaceBox(10, 90, 90, 10)],
                encodings=[vector],
            )

        a = build(np.linspace(0, 1, 128))
        b = build(np.linspace(0, 1, 128))
        c = build(np.linspace(0, 1.001, 128))
        assert a.encoding_commitment() == b.encoding_commitment()
        assert a.encoding_commitment() != c.encoding_commitment()

    def test_primary_face_is_the_largest_detected_box(self):
        import numpy as np

        from src.face.encoder import FaceBox, FaceEncodingResult

        big = FaceBox(0, 200, 200, 0)
        assert big.area == 40000
        result = FaceEncodingResult(
            image_path="p.jpg",
            image_width=800,
            image_height=600,
            image_format="JPEG",
            detector_model="hog",
            upsample=1,
            boxes=[big, FaceBox(0, 50, 50, 0)],
            encodings=[np.zeros(128), np.ones(128)],
        )
        assert result.primary_box is big
        assert result.face_count == 2
