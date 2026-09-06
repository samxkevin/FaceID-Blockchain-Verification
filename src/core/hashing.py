"""Deterministic hashing and canonical serialization primitives.

The integrity model of the whole project rests on two guarantees implemented
here:

1. `canonical_json` produces byte-identical output for equal Python data,
   independent of key insertion order or platform.
2. `sha256_hex` over that canonical form yields the record hash committed
   on-chain.

Both are pure functions with no I/O, no clock, and no environment access, so
they are fully reproducible and directly unit-testable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: Read files in 1 MiB chunks so large images never load fully into memory.
_CHUNK = 1024 * 1024


def canonical_json(data: Any) -> str:
    """Serialize `data` to a canonical JSON string.

    Rules (documented because the hash depends on them):
      * object keys sorted lexicographically
      * no insignificant whitespace
      * non-ASCII kept as real UTF-8 characters (not \\u escapes)
      * floats are rejected upstream; evidence uses only str/int/bool/None
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(data: Any) -> bytes:
    """UTF-8 encoding of the canonical JSON form."""
    return canonical_json(data).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(payload).hexdigest()


def sha256_json(data: Any) -> str:
    """Lowercase hex SHA-256 of the canonical JSON form of `data`."""
    return sha256_hex(canonical_bytes(data))


def sha256_file(path: str | Path) -> str:
    """Streamed lowercase hex SHA-256 of a file's exact bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_hex(value: str) -> str:
    """Normalize a hex digest: strip an optional 0x prefix, lowercase it.

    Used everywhere hashes are compared so that `0xAB..` and `ab..` never
    produce a false tamper alarm.
    """
    if not isinstance(value, str):
        raise TypeError(f"hex digest must be a string, got {type(value).__name__}")
    cleaned = value.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned


def is_sha256_hex(value: str) -> bool:
    """True when `value` is a syntactically valid SHA-256 hex digest."""
    try:
        cleaned = normalize_hex(value)
    except TypeError:
        return False
    if len(cleaned) != 64:
        return False
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        return False
    return True
