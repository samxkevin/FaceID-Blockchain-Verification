from dataclasses import dataclass
from pathlib import Path

import face_recognition
import numpy as np


@dataclass
class FaceEncodingResult:
    face_locations: list
    encodings: list[np.ndarray]

    @property
    def face_count(self) -> int:
        return len(self.encodings)


def detect_and_encode(image_path: str) -> FaceEncodingResult:
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Input image does not exist: {path}")

    image = face_recognition.load_image_file(str(path))
    locations = face_recognition.face_locations(image, model="hog")
    encodings = face_recognition.face_encodings(image, known_face_locations=locations)

    if not encodings:
        raise RuntimeError("No face was detected in the input image.")

    return FaceEncodingResult(
        face_locations=locations,
        encodings=[np.asarray(e, dtype=np.float64) for e in encodings],
    )
