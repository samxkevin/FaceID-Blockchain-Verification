#!/usr/bin/env python3
"""Independent verification of an evidence record against Ethereum Sepolia.

This script needs NO private key and NO API key. Anyone holding the record file,
an RPC URL and the contract address can run it and reach the same verdict.

  python scripts/verify_record.py --record output/verification_record.json

Checks performed:
  1. the record file is structurally valid
  2. the evidence subtree still hashes to the stored record_sha256  (local tamper check)
  3. that hash is anchored on-chain                                  (existence)
  4. the on-chain hash equals the local hash                         (integrity)
  5. keccak256(local matched URL) equals the on-chain URL commitment (URL binding)

Exit code 0 = VERIFIED / UNMODIFIED. Any non-zero exit means verification failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from src.core import console  # noqa: E402
from src.core.errors import PipelineError  # noqa: E402
from src.core.hashing import normalize_hex  # noqa: E402
from src.verification.record import (  # noqa: E402
    canonical_evidence_json,
    check_local_integrity,
    load_record,
)

TOTAL_CHECKS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_record.py",
        description="Independently verify a face-verification evidence record on Ethereum Sepolia.",
    )
    parser.add_argument("--record", required=True, help="Path to the evidence record JSON.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Only re-hash the local record; skip all blockchain checks.",
    )
    parser.add_argument(
        "--show-canonical",
        action="store_true",
        help="Print the exact canonical JSON bytes that were hashed.",
    )
    parser.add_argument("--rpc-url", help="Override SEPOLIA_RPC_URL.")
    parser.add_argument("--contract", help="Override CONTRACT_ADDRESS.")
    return parser


def run(args) -> int:
    load_dotenv()
    console.banner(
        "INDEPENDENT VERIFICATION",
        "local evidence -> SHA-256 recomputation -> Ethereum Sepolia cross-check",
    )

    failures: list[str] = []

    # --- 1. structure -----------------------------------------------------
    console.step(1, TOTAL_CHECKS, "Checking local evidence")
    record = load_record(args.record)
    evidence = record["evidence"]
    match = evidence.get("match", {})
    console.field("Record file:", Path(args.record).resolve())
    console.field("Schema version:", evidence.get("schema_version"))
    console.field("Created (UTC):", evidence.get("created_at_utc"))
    console.field("Image SHA-256:", evidence.get("input_image", {}).get("sha256"))
    console.field("Faces detected:", evidence.get("face_analysis", {}).get("face_count"))
    console.field("Matched URL:", match.get("matched_url"))
    console.field("Evidence tier:", match.get("evidence_tier"))
    console.ok("Record structure is valid.")

    if args.show_canonical:
        console.rule()
        print(canonical_evidence_json(record))
        console.rule()

    # --- 2. local hash ----------------------------------------------------
    console.step(2, TOTAL_CHECKS, "Checking record hash")
    integrity = check_local_integrity(record)
    console.field("Stored SHA-256:", integrity.stored_hash)
    console.field("Recomputed SHA-256:", integrity.recomputed_hash)
    if integrity.matches:
        console.ok("The evidence subtree still hashes to the stored value.")
    else:
        console.fail("The local evidence has been MODIFIED since it was recorded.")
        failures.append("local record hash mismatch (tampering detected)")

    if args.offline:
        console.step(3, TOTAL_CHECKS, "Blockchain checks skipped (--offline)")
        console.warn("Only the local hash was verified; the on-chain anchor was not consulted.")
        return _finish(failures, offline=True)

    # --- 3/4/5. chain ------------------------------------------------------
    from src.blockchain.registry import fetch, url_hash

    console.step(3, TOTAL_CHECKS, "Checking blockchain")
    anchor = record.get("blockchain") or {}
    lookup_hash = integrity.stored_hash
    try:
        chain = fetch(lookup_hash, rpc_url=args.rpc_url, contract_address=args.contract)
    except PipelineError as exc:
        console.fail(str(exc).splitlines()[0])
        if not integrity.matches:
            console.note(
                "Expected: a tampered record hashes to a different value, so nothing is anchored under it."
            )
        console.field("Contract:", args.contract or anchor.get("contract_address") or "(from .env)")
        failures.append("record hash is not anchored on-chain")
        return _finish(failures)

    console.field("Network:", f"{chain['network']} (chain id {chain['chain_id']})")
    console.field("Contract:", chain["contract_address"])
    console.field("Submitter:", chain["submitter"])
    block_time = chain["block_time_unix"]
    console.field(
        "Block timestamp:",
        f"{block_time} ({datetime.fromtimestamp(block_time, timezone.utc).isoformat()})",
    )
    if anchor.get("transaction_hash"):
        console.field("Transaction:", anchor["transaction_hash"])
        console.field("Explorer:", anchor.get("explorer_tx_url", ""))
    console.ok("An on-chain record exists for this hash.")

    console.step(4, TOTAL_CHECKS, "Comparing hashes")
    chain_hash = normalize_hex(chain["record_sha256"])
    console.field("Local SHA-256:", integrity.recomputed_hash)
    console.field("Stored SHA-256:", integrity.stored_hash)
    console.field("On-chain SHA-256:", chain_hash)
    if chain_hash == integrity.stored_hash == integrity.recomputed_hash:
        console.ok("All three hashes are identical.")
    else:
        console.fail("Hash mismatch between the local evidence and the blockchain.")
        failures.append("on-chain hash does not match the local evidence")

    console.step(5, TOTAL_CHECKS, "Comparing matched URL")
    local_url = match.get("matched_url", "")
    local_url_hash = normalize_hex(url_hash(local_url)) if local_url else ""
    chain_url_hash = normalize_hex(chain["url_keccak256"])
    console.field("Local URL:", local_url)
    console.field("Local keccak256:", local_url_hash)
    console.field("On-chain keccak256:", chain_url_hash)
    if local_url_hash and local_url_hash == chain_url_hash:
        console.ok("The matched URL is exactly the one committed on-chain.")
    else:
        console.fail("The matched URL does not match the on-chain commitment.")
        failures.append("matched URL does not match the on-chain commitment")

    return _finish(failures)


def _finish(failures: list[str], offline: bool = False) -> int:
    console.rule()
    if failures:
        for item in failures:
            console.fail(item, indent=2)
        console.status("VERIFICATION FAILED - RECORD IS NOT TRUSTWORTHY", good=False)
        return 2

    if offline:
        console.status("LOCAL RECORD UNMODIFIED (blockchain not checked)", good=True)
        return 0

    console.status("VERIFIED / UNMODIFIED", good=True)
    print("What this proves:")
    print("  - The evidence record has not changed since it was anchored.")
    print("  - The anchor, its timestamp and its submitter are recorded immutably on Sepolia.")
    print("  - The reverse-image-search provider returned this URL for this image.")
    print()
    print("What this does NOT prove:")
    print("  - The identity of any person in the photograph.")
    print("  - That anyone owns or controls the matched social-media account.")
    print()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except PipelineError as exc:
        console.error_block(exc, code=exc.code, remedy=exc.remedy)
        console.status("VERIFICATION FAILED", good=False)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
