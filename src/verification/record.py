import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_record(image_hash: str, face_count: int, encoding_dimensions: int, match: dict, provider: str) -> dict:
    return {
        "schema_version": "1.0",
        "image_sha256": image_hash,
        "face_count": face_count,
        "encoding_dimensions": encoding_dimensions,
        "reverse_search_provider": provider,
        "matched_url": match["url"],
        "matched_title": match.get("title", ""),
        "matched_source": match.get("source", ""),
        "result_type": match.get("result_type", ""),
        "recorded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def canonical_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(record: dict) -> str:
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def save_record(record: dict, path: str) -> str:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(output)
