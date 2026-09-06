"""Building, saving, loading and re-verifying the tamper-evident evidence record.

Integrity model
---------------
The record is split into two parts:

  * `evidence`  - everything that is being attested. This subtree alone is
                  canonicalized and hashed.
  * `integrity` - the resulting `record_sha256`, the algorithm identifiers, and
                  (after registration) the on-chain anchor.

Because the hash covers the `evidence` subtree in its entirety, changing ANY
meaningful field - the image hash, the matched URL, the face count, the evidence
tier, the timestamp - changes `record_sha256` and verification fails. Metadata
appended afterwards (transaction hash, block number) lives outside `evidence`
and therefore does not invalidate the hash, which is exactly what allows the
anchor to be written after the hash is computed.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.errors import EvidenceError, TamperDetectedError
from src.core.hashing import (
    canonical_json,
    is_sha256_hex,
    normalize_hex,
    sha256_file,
    sha256_json,
)

#: Bumped whenever the hashed `evidence` layout changes in a way that would
#: alter the digest of an otherwise identical observation.
SCHEMA_VERSION = "2.0"

#: Fixed, explicit statement of scope embedded in every record. Judges and any
#: third-party verifier read the same wording that the README uses.
CLAIM_SCOPE = {
    "proves": [
        "A face was detected and encoded in the input image at pipeline runtime.",
        "The input image had exactly this SHA-256 digest.",
        "The named reverse-image-search provider returned this URL for this image.",
        "This evidence record existed, unchanged, before the recorded blockchain transaction.",
    ],
    "does_not_prove": [
        "The identity of any person appearing in the image.",
        "That any person owns or controls the matched social-media account.",
        "That the matched page currently contains, or ever contained, the identical image file, unless the evidence tier is VERIFIED_EXACT or PROVIDER_EXACT.",
        "The truthfulness of any claim made by the reverse-image-search provider.",
    ],
}


def _runtime_metadata() -> dict[str, Any]:
    """Reproducibility context. Recorded but deliberately coarse.

    Only the Python and OS family are stored - never a username, hostname or
    absolute path, which would leak operator information into a public chain
    anchor's off-chain record.
    """
    return {
        "python_version": ".".join(str(v) for v in sys.version_info[:3]),
        "platform": platform.system(),
    }


def build_evidence(
    *,
    image_path: str | Path,
    image_fingerprint: dict[str, Any],
    face_metadata: dict[str, Any],
    search_summary: dict[str, Any],
    selection: dict[str, Any],
    transport: dict[str, Any],
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Assemble the hashed `evidence` subtree.

    All values must be JSON-primitive (str/int/bool/None/list/dict). Floats are
    rejected because their textual form is not portable enough for a hash that
    a third party must reproduce exactly.
    """
    path = Path(image_path)
    selected = selection.get("selected", {})

    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": created_at_utc
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "input_image": {
            # File name only - never the operator's directory structure.
            "file_name": path.name,
            "sha256": sha256_file(path) if path.is_file() else "",
            "byte_size": path.stat().st_size if path.is_file() else 0,
            **image_fingerprint,
        },
        "face_analysis": face_metadata,
        "reverse_image_search": search_summary,
        "image_transport": transport,
        "match": {
            "matched_url": selected.get("url", ""),
            "matched_domain": selected.get("domain", ""),
            "matched_title": selected.get("title", ""),
            "matched_source": selected.get("source", ""),
            "social_platform": selected.get("social_platform", ""),
            "provider_match_type": selected.get("match_type", ""),
            "provider_section": selected.get("provider_section", ""),
            "evidence_tier": selection.get("evidence_tier", ""),
            "evidence_tier_meaning": selection.get("evidence_tier_meaning", ""),
            "selection_rationale": selection.get("selection_rationale", ""),
            "local_corroboration": selected.get("local_corroboration"),
        },
        "considered_social_candidates": selection.get("considered_social_candidates", []),
        "claim_scope": CLAIM_SCOPE,
        "runtime": _runtime_metadata(),
    }
    _reject_floats(evidence)
    return evidence


def _reject_floats(node: Any, path: str = "evidence") -> None:
    """Guard: floats would make the canonical form platform-dependent."""
    if isinstance(node, float):
        raise EvidenceError(
            f"Float value found at {path}; evidence must contain only "
            "strings, integers, booleans, nulls, lists and objects.",
            remedy="Quantize the value to an int or a fixed-precision string before recording it.",
        )
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_floats(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_floats(value, f"{path}[{index}]")


def compute_record_hash(evidence: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the `evidence` subtree."""
    return sha256_json(evidence)


def build_record(evidence: dict[str, Any]) -> dict[str, Any]:
    """Wrap an evidence subtree into a complete, hashed record document."""
    digest = compute_record_hash(evidence)
    return {
        "evidence": evidence,
        "integrity": {
            "record_sha256": digest,
            "hash_algorithm": "SHA-256",
            "canonicalization": "JSON, sorted keys, separators (',',':'), UTF-8, no NaN",
            "hashed_subtree": "evidence",
        },
        "blockchain": None,
    }


def attach_blockchain_anchor(record: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    """Record the on-chain anchor. Lives outside `evidence`, so the hash holds."""
    record["blockchain"] = anchor
    return record


def save_record(record: dict[str, Any], path: str | Path) -> Path:
    """Write the record as pretty, stable JSON (human-reviewable)."""
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def load_record(path: str | Path) -> dict[str, Any]:
    """Load and structurally validate a record file."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise EvidenceError(
            f"Evidence record not found: {file_path}",
            remedy="Run scripts/run_pipeline.py first, or check the --record path.",
        )
    try:
        record = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceError(
            f"Evidence record is not valid JSON: {exc}",
            remedy="The file was corrupted or hand-edited into an invalid state.",
        ) from exc

    if not isinstance(record, dict):
        raise EvidenceError(
            "Evidence record must be a JSON object.",
            remedy="Regenerate the record with scripts/run_pipeline.py.",
        )
    for key in ("evidence", "integrity"):
        if key not in record:
            raise EvidenceError(
                f"Evidence record is missing the required '{key}' section.",
                remedy="Regenerate the record with scripts/run_pipeline.py.",
            )
    stored = record["integrity"].get("record_sha256", "")
    if not is_sha256_hex(stored):
        raise EvidenceError(
            f"integrity.record_sha256 is not a valid SHA-256 digest: {stored!r}",
            remedy="Regenerate the record with scripts/run_pipeline.py.",
        )
    return record


@dataclass(frozen=True)
class LocalIntegrityReport:
    """Outcome of re-hashing a record without touching the network."""

    stored_hash: str
    recomputed_hash: str
    matches: bool

    @property
    def status(self) -> str:
        return "UNMODIFIED" if self.matches else "TAMPERED"


def check_local_integrity(record: dict[str, Any]) -> LocalIntegrityReport:
    """Recompute the record hash from the evidence and compare it to the stored one."""
    stored = normalize_hex(record["integrity"]["record_sha256"])
    recomputed = compute_record_hash(record["evidence"])
    return LocalIntegrityReport(
        stored_hash=stored, recomputed_hash=recomputed, matches=stored == recomputed
    )


def assert_local_integrity(record: dict[str, Any]) -> LocalIntegrityReport:
    """Raise TamperDetectedError when the local record no longer hashes correctly."""
    report = check_local_integrity(record)
    if not report.matches:
        raise TamperDetectedError(
            "The local evidence has been modified after it was recorded.\n"
            f"  stored     : {report.stored_hash}\n"
            f"  recomputed : {report.recomputed_hash}",
            remedy=(
                "The evidence subtree no longer matches its own hash. Restore the "
                "original record file; the on-chain commitment cannot be changed."
            ),
        )
    return report


def canonical_evidence_json(record: dict[str, Any]) -> str:
    """Exact byte string that was hashed - useful for manual verification demos."""
    return canonical_json(record["evidence"])
