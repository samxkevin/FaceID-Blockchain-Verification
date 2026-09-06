#!/usr/bin/env python3
"""End-to-end pipeline: local image -> face -> reverse search -> evidence -> Sepolia.

Usage examples
--------------
  # TinEye: local file goes straight to the provider, nothing is uploaded anywhere else
  python scripts/run_pipeline.py --image samples/photo.jpg --provider tineye

  # Google Lens via SerpAPI, with an ephemeral anonymous upload for the fetch URL
  python scripts/run_pipeline.py --image samples/photo.jpg --upload

  # Google Lens via SerpAPI, reusing a public URL you already control
  python scripts/run_pipeline.py --image samples/photo.jpg --image-url https://...

  # Everything except the blockchain write (no key or gas needed)
  python scripts/run_pipeline.py --image samples/photo.jpg --upload --skip-chain
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src` importable when the script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from src.core import console, perceptual  # noqa: E402
from src.core.errors import PipelineError  # noqa: E402
from src.face.encoder import detect_and_encode  # noqa: E402
from src.match.selection import TIER_RANK, select_social_match  # noqa: E402
from src.reverse_search import transport  # noqa: E402
from src.verification.record import (  # noqa: E402
    attach_blockchain_anchor,
    build_evidence,
    build_record,
    save_record,
)

TOTAL_STEPS = 5

#: Human-readable transport labels shown in the demo output.
TRANSPORT_LABELS = {
    "serpapi-image-api": "SerpAPI Image API direct upload",
    "none": "direct local upload (provider accepts raw bytes)",
    "operator-supplied": "operator-supplied public URL",
    "0x0.st": "ephemeral anonymous bin (0x0.st)",
    "tmpfiles.org": "ephemeral anonymous bin (tmpfiles.org)",
    "uguu.se": "ephemeral anonymous bin (uguu.se)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="Face detection -> genuine reverse-image search -> tamper-evident blockchain record.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--image", required=True, help="Local input photograph.")

    group = parser.add_argument_group("reverse-image search")
    group.add_argument(
        "--provider",
        choices=("serpapi", "tineye"),
        default="serpapi",
        help="serpapi = Google Lens (needs a fetchable URL); tineye = direct local upload.",
    )
    group.add_argument(
        "--image-url",
        help="Public URL already serving this exact image (verified before use).",
    )
    group.add_argument(
        "--upload",
        action="store_true",
        help="Upload the image to an anonymous short-retention bin so Lens can fetch it.",
    )
    group.add_argument(
        "--corroborate-top",
        type=int,
        default=5,
        help="How many candidates to perceptually hash-check locally (default: 5).",
    )
    group.add_argument(
        "--require-tier",
        choices=tuple(TIER_RANK),
        help="Abort unless the best match reaches at least this evidence tier.",
    )
    group.add_argument(
        "--require-post-url",
        action="store_true",
        help="Only accept results whose URL addresses a specific post or profile.",
    )

    face = parser.add_argument_group("face detection")
    face.add_argument("--face-model", choices=("hog", "cnn"), default="hog")
    face.add_argument("--face-upsample", type=int, default=1)
    face.add_argument(
        "--require-single-face",
        action="store_true",
        help="Fail if the photograph contains more than one face.",
    )

    chain = parser.add_argument_group("blockchain")
    chain.add_argument("--skip-chain", action="store_true", help="Build the record but do not anchor it.")

    parser.add_argument("--record", default="output/verification_record.json")
    return parser


def resolve_transport(args, provider) -> tuple[str, transport.TransportResult]:
    """Decide how the provider will receive the image, and return (reference, transport).

    An explicit --image-url or --upload always wins, so the operator can force
    the URL fallback. Otherwise a provider that accepts local files gets the
    path directly (TinEye multipart, or the SerpAPI Image API upload).
    """
    provider_accepts_local = getattr(provider, "accepts_local_file", False)

    if provider_accepts_local and not args.image_url and not args.upload:
        return str(Path(args.image).expanduser()), transport.TransportResult(
            mode="direct",
            url="",
            service=getattr(provider, "direct_transport_service", "none"),
            note=getattr(
                provider,
                "direct_transport_note",
                "The provider accepts the image bytes directly; the file was not published anywhere.",
            ),
        )

    if args.image_url:
        console.note("Verifying that --image-url serves the same picture as --image ...")
        result = transport.verify_public_url(args.image, args.image_url)
        return result.url, result

    if args.upload:
        console.note("Uploading to an anonymous short-retention file bin ...")
        result = transport.upload_ephemeral(args.image)
        return result.url, result

    raise PipelineError(
        f"{getattr(provider, 'name', 'The provider')} must fetch the image over HTTP, "
        "but no URL was supplied.",
        remedy=(
            "Choose one:\n"
            "  --upload                       anonymous short-retention upload (no hosting required)\n"
            "  --image-url <public link>      reuse a link you already control\n"
            "  --provider tineye              send the local file directly, no URL at all"
        ),
    )


def run(args) -> int:
    load_dotenv()
    image_path = Path(args.image).expanduser()

    console.banner(
        "FACE ID + BLOCKCHAIN VERIFICATION",
        "local image -> face encoding -> reverse image search -> SHA-256 -> Ethereum Sepolia",
    )

    # --- Step 1: face -----------------------------------------------------
    console.step(1, TOTAL_STEPS, "Detecting and encoding face")
    face = detect_and_encode(
        image_path,
        model=args.face_model,
        upsample=args.face_upsample,
        allow_multiple_faces=not args.require_single_face,
    )
    console.field("Face detected:", "YES")
    console.field("Faces:", face.face_count)
    console.field("Detector:", f"dlib {face.detector_model.upper()} (upsample {face.upsample})")
    console.field("Encoding:", f"{face.encoding_dimensions} dimensions")
    console.field("Primary face box:", face.primary_box.to_dict())
    console.field("Encoding commitment:", face.encoding_commitment()[:32] + "...")
    console.note("The encoding stays local. It is never uploaded and never written on-chain.")
    console.note("A face encoding is not an identity; this step only proves a face was found.")

    image_bytes = image_path.read_bytes()
    input_fp = perceptual.fingerprint(image_bytes)

    # --- Step 2: reverse image search ------------------------------------
    console.step(2, TOTAL_STEPS, "Running genuine reverse-image search")
    if args.provider == "tineye":
        from src.reverse_search.tineye import TinEyeProvider

        provider = TinEyeProvider()
    else:
        from src.reverse_search.serpapi_lens import SerpApiLensProvider

        provider = SerpApiLensProvider()

    reference, transport_result = resolve_transport(args, provider)
    console.field("Provider:", provider.name)
    console.field("Image transport:", TRANSPORT_LABELS.get(
        transport_result.service, f"{transport_result.mode} ({transport_result.service})"
    ))
    if transport_result.url:
        console.field("Query image URL:", transport_result.url)
    if transport_result.note:
        console.note(transport_result.note)

    search = provider.search(reference)
    summary = search.summary()
    if summary["engine"] == "google_lens" and transport_result.service == "serpapi-image-api":
        console.field("Lens query mode:", "image_id (no public URL used)")
        console.field("SerpAPI image_id:", search.query_image_reference.split(":", 1)[-1])
    console.field("Candidates returned:", summary["total_candidates"])
    console.field("Provider exact matches:", summary["exact_candidates"])
    console.field("Response sections:", summary["section_counts"])
    console.field("Searched at (UTC):", summary["searched_at_utc"])
    console.note("Every candidate below came from this live response. Nothing is hardcoded.")

    # --- Step 3: social selection ----------------------------------------
    console.step(3, TOTAL_STEPS, "Selecting and corroborating the social-media match")
    selection = select_social_match(
        search,
        input_fingerprint=input_fp,
        corroborate_top_n=max(0, args.corroborate_top),
        require_tier=args.require_tier,
        require_post_shaped=args.require_post_url,
    )
    winner = selection.candidate
    console.field("Social candidates:", len(selection.ranked))
    console.field("Provider match type:", winner.match_type)
    console.field("Evidence tier:", console.paint(selection.tier, "bold"))
    console.field("Social platform:", winner.social_platform)
    console.field("Matched URL:", winner.url)
    console.field("Source:", winner.source or winner.domain)
    if winner.title:
        console.field("Title:", winner.title[:80])
    corr = winner.corroboration or {}
    if corr.get("attempted"):
        console.field(
            "Local hash check:",
            f"dHash {corr.get('dhash_distance')}/{corr.get('dhash_threshold')}, "
            f"pHash {corr.get('phash_distance')}/{corr.get('phash_threshold')} "
            f"=> {'CORROBORATED' if corr.get('corroborated') else 'not corroborated'}",
        )
    else:
        console.field("Local hash check:", "not possible (no fetchable thumbnail)")
    console.rule()
    for line in _wrap(selection.to_dict()["evidence_tier_meaning"]):
        console.note(line)

    # --- Step 4: evidence record -----------------------------------------
    console.step(4, TOTAL_STEPS, "Creating tamper-evident evidence record")
    evidence = build_evidence(
        image_path=image_path,
        image_fingerprint={"perceptual": input_fp.to_dict()},
        face_metadata=face.to_metadata(),
        search_summary=summary,
        selection=selection.to_dict(),
        transport=transport_result.to_dict(),
    )
    record = build_record(evidence)
    digest = record["integrity"]["record_sha256"]
    console.field("Image SHA-256:", evidence["input_image"]["sha256"])
    console.field("Record SHA-256:", console.paint(digest, "bold"))
    console.field("Schema version:", evidence["schema_version"])
    console.note("The hash covers the whole evidence subtree: change any field and it changes.")

    # --- Step 5: blockchain -----------------------------------------------
    console.step(5, TOTAL_STEPS, "Registering evidence on Ethereum Sepolia")
    if args.skip_chain:
        save_record(record, args.record)
        console.warn("--skip-chain was set: nothing was written to the blockchain.")
        console.field("Record saved:", Path(args.record).resolve())
        console.status("EVIDENCE RECORDED (NOT ANCHORED)", good=False)
        return 0

    from src.blockchain.registry import register

    receipt = register(digest, winner.url)
    attach_blockchain_anchor(record, receipt.to_dict())
    saved = save_record(record, args.record)

    console.field("Network:", f"{receipt.network} (chain id {receipt.chain_id})")
    console.field("Contract:", receipt.contract_address)
    console.field("Transaction:", receipt.transaction_hash)
    console.field("Block:", receipt.block_number)
    console.field("Gas used:", receipt.gas_used)
    console.field("Submitter:", receipt.submitter)
    console.field("URL keccak256:", receipt.url_keccak256)
    console.field("Explorer:", receipt.explorer_tx_url)
    console.rule()
    console.field("Record saved:", saved.resolve())
    console.status("VERIFICATION REGISTERED", good=True)
    print("Verify independently with:")
    print(f"  python scripts/verify_record.py --record {saved}\n")
    return 0


def _wrap(text: str, width: int = 66) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width)


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except PipelineError as exc:
        console.error_block(exc, code=exc.code, remedy=exc.remedy)
        console.status("PIPELINE FAILED", good=False)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
