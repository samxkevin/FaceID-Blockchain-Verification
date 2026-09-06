#!/usr/bin/env python3
"""Prove the record is tamper-evident, on camera, in one command.

Copies an existing evidence record, edits one field inside the hashed evidence
subtree, and shows that the record no longer hashes to its committed value.
The on-chain anchor is never touched and cannot be changed.

  python scripts/tamper_demo.py --record output/verification_record.json

Then run the verifier against the tampered copy to see it fail:

  python scripts/verify_record.py --record output/verification_record.TAMPERED.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import console  # noqa: E402
from src.core.errors import PipelineError  # noqa: E402
from src.verification.record import check_local_integrity, load_record  # noqa: E402


def run(args) -> int:
    console.banner("TAMPER-EVIDENCE DEMONSTRATION", "modify one field, watch the hash break")

    record = load_record(args.record)
    before = check_local_integrity(record)

    console.step(1, 3, "Original record")
    console.field("Matched URL:", record["evidence"]["match"]["matched_url"])
    console.field("Stored SHA-256:", before.stored_hash)
    console.field("Recomputed SHA-256:", before.recomputed_hash)
    console.ok(f"Integrity: {before.status}")

    console.step(2, 3, f"Tampering with evidence.{args.field}")
    target = record["evidence"]
    parts = args.field.split(".")
    for key in parts[:-1]:
        if key not in target:
            raise PipelineError(
                f"No such field in the evidence subtree: evidence.{args.field}",
                remedy="Use dotted notation, e.g. match.matched_url or input_image.sha256",
            )
        target = target[key]
    leaf = parts[-1]
    if leaf not in target:
        raise PipelineError(
            f"No such field in the evidence subtree: evidence.{args.field}",
            remedy="Use dotted notation, e.g. match.matched_url or input_image.sha256",
        )

    original_value = target[leaf]
    target[leaf] = args.value
    console.field("Field:", f"evidence.{args.field}")
    console.field("Was:", original_value)
    console.field("Now:", args.value)
    console.note("integrity.record_sha256 is deliberately left untouched, as a forger would leave it.")

    console.step(3, 3, "Re-checking integrity")
    after = check_local_integrity(record)
    console.field("Committed SHA-256:", after.stored_hash)
    console.field("Recomputed SHA-256:", after.recomputed_hash)

    output = Path(args.output or str(Path(args.record).with_suffix("")) + ".TAMPERED.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    if after.matches:
        console.fail("The hash did not change - this field is not covered by the commitment.")
        console.status("UNEXPECTED: TAMPERING NOT DETECTED", good=False)
        return 1

    console.ok("The hashes now differ: the modification is detectable.")
    console.field("Tampered copy:", output.resolve())
    console.status("TAMPERING DETECTED", good=True)
    print("The forged hash is not anchored on Sepolia, so on-chain lookup also fails:")
    print(f"  python scripts/verify_record.py --record {output}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Demonstrate tamper-evidence of an evidence record.")
    parser.add_argument("--record", required=True, help="Path to a valid evidence record.")
    parser.add_argument(
        "--field",
        default="match.matched_url",
        help="Dotted field inside the evidence subtree to modify.",
    )
    parser.add_argument(
        "--value",
        default="https://instagram.com/p/forged-by-an-attacker/",
        help="Replacement value.",
    )
    parser.add_argument("--output", help="Where to write the tampered copy.")
    args = parser.parse_args()
    try:
        return run(args)
    except PipelineError as exc:
        console.error_block(exc, code=exc.code, remedy=exc.remedy)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
