# FaceID + Blockchain Verification

**HHgoa Task #3** — a local, end-to-end pipeline that detects and encodes a face from a photograph, finds a real matching social-media post through a **genuine reverse-image search**, and anchors that match on **Ethereum Sepolia** as a tamper-evident, independently verifiable record.

No website. No hosting. No hardcoded results. One command in, a blockchain transaction out.

```
LOCAL IMAGE → FACE ENCODING → REVERSE IMAGE SEARCH → SOCIAL MATCH
     → EVIDENCE RECORD → SHA-256 → SEPOLIA TX → INDEPENDENT VERIFICATION
```

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Requirements](#2-requirements)
3. [Architecture](#3-architecture)
4. [Pipeline flow](#4-pipeline-flow)
5. [Face detection and encoding](#5-face-detection-and-encoding)
6. [Genuine reverse-image search](#6-genuine-reverse-image-search)
7. [Social-result validation](#7-social-result-validation)
8. [Evidence record](#8-evidence-record)
9. [SHA-256 integrity model](#9-sha-256-integrity-model)
10. [Ethereum Sepolia blockchain](#10-ethereum-sepolia-blockchain)
11. [Smart contract](#11-smart-contract)
12. [Setup](#12-setup)
13. [Environment variables](#13-environment-variables)
14. [Deployment](#14-deployment)
15. [End-to-end execution](#15-end-to-end-execution)
16. [Independent verification](#16-independent-verification)
17. [Example expected output](#17-example-expected-output)
18. [Testing](#18-testing)
19. [Security considerations](#19-security-considerations)
20. [Known limitations](#20-known-limitations)
21. [What the blockchain proves](#21-what-the-blockchain-proves)
22. [What the blockchain does NOT prove](#22-what-the-blockchain-does-not-prove)
23. [Implementation status](#23-implementation-status)
24. [Screen-recording checklist](#24-screen-recording-checklist)

---

## 1. Project overview

Given a local photograph, this pipeline:

1. **Detects and encodes a face** with dlib/`face_recognition` (128-dimensional encoding, computed locally, never uploaded, never put on-chain).
2. **Runs a genuine reverse-image search** against the *actual input image* through a live third-party provider (Google Lens via SerpAPI, or TinEye).
3. **Selects a real social-media match** from the live provider response using a deterministic, evidence-based ranking, and **independently corroborates** it with perceptual hashing that we compute ourselves.
4. **Builds a canonical evidence record** and hashes it with SHA-256.
5. **Anchors the hash on Ethereum Sepolia** through an immutable, append-only smart contract.
6. **Verifies the record independently** — recomputing the hash locally and cross-checking it against the chain, with no API key and no private key required.

**Nothing about the social-media result is hardcoded.** There is no fallback URL, no seeded username, no canned response anywhere in the codebase. If the provider returns no social match, the pipeline fails loudly with a genuine negative result rather than inventing one.

### The honesty principle

This project is deliberately careful about what it claims. A face encoding is not an identity. A reverse-image match is not proof of account ownership. Every evidence record embeds a machine-readable `claim_scope` stating exactly what is and is not proven, and the CLI prints the same caveats. See [§21](#21-what-the-blockchain-proves) and [§22](#22-what-the-blockchain-does-not-prove).

---

## 2. Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Tested on 3.11 |
| pip packages | see `requirements.txt` | `dlib-bin` ships prebuilt wheels — no CMake needed |
| SerpAPI key | free tier | *or* a TinEye key — at least one reverse-search provider |
| Sepolia RPC URL | any | Public nodes work; no account required |
| Sepolia test wallet | funded from a faucet | Only for **writing**. Verification needs no key. |

No Node.js, Hardhat, Foundry, Docker or web server is required. Contract compilation and deployment are pure Python via `py-solc-x`.

---

## 3. Architecture

```
                        ┌──────────────────────────┐
   samples/photo.jpg ──►│  src/face/encoder.py     │  dlib HOG/CNN detection
                        │  validation + encoding   │  128-d encoding (stays local)
                        └────────────┬─────────────┘
                                     │ face metadata (non-biometric)
                                     ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  src/reverse_search/                                        │
   │    transport.py       how the image reaches the provider    │
   │    serpapi_lens.py    Google Lens   (needs a fetch URL)     │
   │    tineye.py          TinEye        (direct local upload)   │
   │    base.py            provider-neutral Candidate model      │
   └────────────┬────────────────────────────────────────────────┘
                │ normalized candidates (EXACT / PAGE / VISUAL)
                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  src/match/                                                 │
   │    social.py      platform + post-shape classification      │
   │    selection.py   deterministic ranking, evidence tiers,    │
   │                   independent perceptual corroboration      │
   └────────────┬────────────────────────────────────────────────┘
                │ selected match + full audit trail
                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  src/verification/record.py                                 │
   │    canonical evidence subtree → SHA-256 record hash         │
   └────────────┬────────────────────────────────────────────────┘
                │ record_sha256 + keccak256(matched_url)
                ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  src/blockchain/registry.py  ⇄  FaceVerificationRegistry    │
   │                                  on Ethereum Sepolia        │
   └─────────────────────────────────────────────────────────────┘

   Cross-cutting: src/core/  hashing.py · perceptual.py · errors.py · console.py
```

### Repository layout

```
contracts/
    FaceVerificationRegistry.sol     immutable append-only commitment registry
src/
    core/
        hashing.py                   canonical JSON + SHA-256 primitives
        perceptual.py                aHash / dHash / pHash, Hamming distance
        errors.py                    typed errors, each with a remedy
        console.py                   clean CLI formatting
    face/encoder.py                  validation, detection, encoding, metadata
    reverse_search/
        base.py                      Candidate / SearchResult, match types
        transport.py                 direct | provided | ephemeral image transport
        serpapi_lens.py              Google Lens adapter
        tineye.py                    TinEye adapter (direct upload)
    match/
        social.py                    platform + post-shape classification
        selection.py                 ranking, evidence tiers, corroboration
    verification/record.py           evidence build / hash / save / tamper check
    blockchain/registry.py           Web3 client, all failure modes typed
scripts/
    run_pipeline.py                  the end-to-end demo command
    verify_record.py                 independent verification (no keys needed)
    deploy_contract.py               compile + deploy to Sepolia
    tamper_demo.py                   prove tamper-evidence on camera
tests/                               191 tests, no keys or network required
```

---

## 4. Pipeline flow

| Step | Stage | Output |
|---|---|---|
| 1 | Face detection + encoding | face count, box, 128-d encoding, commitment hash |
| 2 | Reverse-image search | live candidate list, classified EXACT / PAGE / VISUAL |
| 3 | Social selection + corroboration | matched URL, platform, **evidence tier** |
| 4 | Evidence record | canonical JSON, image SHA-256, **record SHA-256** |
| 5 | Blockchain anchor | Sepolia tx hash, block number, submitter |

---

## 5. Face detection and encoding

**Implemented.** `src/face/encoder.py`.

- **Detection:** dlib HOG (default, fast) or CNN (`--face-model cnn`, more sensitive). `--face-upsample 2` finds smaller faces.
- **Encoding:** 128-dimensional `face_recognition` encoding.
- **Validation before detection:** existence, non-empty, decodability, format allow-list (JPEG/PNG/WEBP/BMP/TIFF), minimum 64×64, maximum 50 MP. EXIF orientation is normalized so a rotated-on-disk photo behaves identically to its upright copy.
- **Multiple faces:** all faces are detected and counted. Detections are sorted by area (ties broken by position), so the "primary" face is deterministic and never depends on detector iteration order. `--require-single-face` turns multi-face images into an explicit error.
- **Errors:** every failure names the cause and a fix — e.g. *"No face was detected … try `--face-model cnn`, raise `--face-upsample` to 2, or use a clearer front-facing photograph."*

### What this step establishes

> A human face was **detected and encoded** in the input image.

It does **not** establish who that person is. The pipeline never compares the encoding to any identity database, and makes no identity claim anywhere.

### Biometric handling

The raw 128-d encoding **never leaves the machine** and is **never written on-chain**. What goes into the record is a one-way **salted commitment**: the encoding is quantized to 6 decimals (for cross-platform stability), prefixed with a domain separator, and SHA-256'd. This binds the record to the specific encoding that was produced without publishing a biometric template that could be reused against the subject. Biometrics are permanent and irrevocable; putting them on an immutable public ledger would be irresponsible.

---

## 6. Genuine reverse-image search

**Implemented.** Two live providers, no cached or canned responses.

### Provider comparison

| | **TinEye** (`--provider tineye`) | **Google Lens / SerpAPI** (`--provider serpapi`, default) |
|---|---|---|
| Local file accepted directly | ✅ **yes** — no upload anywhere else | ❌ needs a fetchable URL |
| Index type | exact / derivative copies only | exact + visual similarity |
| Match strength | every hit is an exact-image match | distinguishes exact vs visual |
| Social coverage | thinner | broader |

### Input image handling (Priority 2)

Google Lens fetches the query image over HTTP and cannot take a local file. Rather than hide that dependency, the pipeline makes it explicit and offers three transports, each recorded verbatim in the evidence record:

| Mode | Flag | What happens |
|---|---|---|
| `direct` | `--provider tineye` | **Best.** Image bytes POSTed straight to the provider. Nothing is published anywhere. |
| `provided` | `--image-url <url>` | You supply a public URL. The pipeline **downloads it and verifies** it serves the same picture (byte-identical, or perceptually identical if the host re-encoded it) before trusting it. A mismatch is a hard error. |
| `ephemeral` | `--upload` | Opt-in anonymous upload to a short-retention file bin (0x0.st → tmpfiles.org → uguu.se, first success wins) purely so Lens can fetch it. |

**No hosting infrastructure is introduced.** The ephemeral bins are third-party throwaway endpoints we do not operate, nothing persists, and the upload only ever happens behind an explicit `--upload` flag — never silently.

### Match-type fidelity

SerpAPI response sections are mapped to provider-neutral types and **never upgraded**:

| Response section | Match type | Meaning |
|---|---|---|
| `exact_matches` | `EXACT` | provider reports the same image on that page |
| `image_results`, `image_sources` | `PAGE` | pages containing the image |
| `visual_matches` | `VISUAL` | visually similar — *not* necessarily the same image |

Duplicate URLs across sections keep the **strongest** classification. A `VISUAL` result is never relabelled as `EXACT`.

---

## 7. Social-result validation

**Implemented.** `src/match/social.py`, `src/match/selection.py`.

### Three-stage filtering

1. **Platform classification** — 19 platforms (Instagram, Facebook, X, TikTok, LinkedIn, Threads, Reddit, Pinterest, Bluesky, Mastodon, YouTube, …). Subdomains count (`m.facebook.com`, `de.linkedin.com`); lookalike domains do not (`instagram.com.evil.net` is rejected).
2. **Post-shape detection** — per-platform regexes decide whether the path addresses a *specific post or profile* (`/p/CabcDEF/`, `/user/status/123`, `/@user/video/456`) rather than a login, help, share, explore or hashtag page. Selecting `instagram.com/accounts/login/` and calling it "a matching social media post" would be dishonest, so those are demoted.
3. **Independent corroboration** — the pipeline downloads the provider's thumbnail for the top candidates and compares it to the input image using **our own** perceptual hashes (dHash + pHash, both must agree within 10 bits). This is evidence we computed, not a provider claim.

### Evidence tiers

The strength of the final claim is always explicit — never a fabricated confidence percentage:

| Tier | Provider says | We independently confirmed | Meaning |
|---|---|---|---|
| `VERIFIED_EXACT` | exact match | ✅ same picture | **Strongest.** |
| `PROVIDER_EXACT` | exact match | ⚠️ not checkable | Provider's exact claim, uncorroborated. |
| `CORROBORATED_VISUAL` | visual only | ✅ same picture | Our hashing is stronger than the provider's label. |
| `PROVIDER_VISUAL` | visual only | ❌ | **Weakest.** *Not* evidence the identical image is on that page. |

`--require-tier PROVIDER_EXACT` aborts unless a strong-enough match is found. `--require-post-url` rejects non-post URLs.

### Deterministic ranking

Every sort key is real evidence — no invented scores:

1. evidence tier → 2. provider match type → 3. post-shaped URL → 4. our measured pHash distance → 5. provider's own ordering → 6. URL string (reproducibility tiebreaker).

The same provider response always yields the same selection, regardless of input ordering. Every considered candidate is preserved in the record under `considered_social_candidates`, so a reviewer can replay the decision.

---

## 8. Evidence record

**Implemented.** `src/verification/record.py`. Schema version `2.0`.

The record has two parts:

- **`evidence`** — everything being attested. **This subtree alone is hashed.**
- **`integrity`** + **`blockchain`** — the resulting hash, algorithm identifiers, and the on-chain anchor (written *after* hashing, which is why it lives outside `evidence`).

```jsonc
{
  "evidence": {
    "schema_version": "2.0",
    "created_at_utc": "2026-09-06T09:45:28+00:00",
    "input_image": {
      "file_name": "photo.jpg",          // file name only — no directory paths
      "sha256": "18e0eaf8…",
      "byte_size": 48213,
      "perceptual": { "ahash": "…", "dhash": "…", "phash": "…", "width": 612, "height": 408 }
    },
    "face_analysis": {
      "detected": true, "face_count": 1,
      "detector_model": "hog", "detector_upsample": 1,
      "encoding_dimensions": 128,
      "encoding_commitment_sha256": "cd767a56…",   // one-way; NOT the encoding
      "primary_face_box": { "top": 96, "right": 368, "bottom": 225, "left": 239 }
    },
    "reverse_image_search": {
      "provider": "Google Lens via SerpAPI", "engine": "google_lens",
      "searched_at_utc": "…", "total_candidates": 34,
      "exact_candidates": 2, "social_candidates": 6, "section_counts": { … }
    },
    "image_transport": { "mode": "ephemeral", "service": "0x0.st", "url": "…" },
    "match": {
      "matched_url": "https://www.instagram.com/p/…/",
      "matched_domain": "instagram.com", "social_platform": "Instagram",
      "provider_match_type": "EXACT", "provider_section": "exact_matches",
      "evidence_tier": "VERIFIED_EXACT",
      "evidence_tier_meaning": "…plain-English statement of exactly what this tier asserts…",
      "selection_rationale": "…why this candidate won…",
      "local_corroboration": { "dhash_distance": 0, "phash_distance": 0,
                               "dhash_threshold": 10, "phash_threshold": 10,
                               "corroborated": true }
    },
    "considered_social_candidates": [ … full ranked audit trail … ],
    "claim_scope": { "proves": [ … ], "does_not_prove": [ … ] },
    "runtime": { "python_version": "3.11.2", "platform": "Linux" }
  },
  "integrity": {
    "record_sha256": "9428d2cf…",
    "hash_algorithm": "SHA-256",
    "canonicalization": "JSON, sorted keys, separators (',',':'), UTF-8, no NaN",
    "hashed_subtree": "evidence"
  },
  "blockchain": { "transaction_hash": "0x…", "block_number": 6543210, … }
}
```

**Privacy by construction:** only the file *name* is stored (never your directory layout), only the OS family (never hostname or username), and only a one-way commitment to the encoding (never the biometric vector). Floats are rejected outright — their textual form is not portable enough for a hash a third party must reproduce exactly.

---

## 9. SHA-256 integrity model

Canonicalization rules (the digest depends on all four):

1. object keys sorted lexicographically
2. no insignificant whitespace — `separators=(",", ":")`
3. UTF-8, non-ASCII preserved as real characters (not `\u` escapes)
4. NaN/Infinity rejected

```
record_sha256 = SHA-256( canonical_json( record["evidence"] ) )
```

**Change any meaningful field and the hash changes.** This is enforced by a parametrized test over the matched URL, evidence tier, image hash, face count, encoding dimensions, provider, timestamp, schema version and transport mode.

Verify it by hand with no trust in this code:

```bash
python scripts/verify_record.py --record output/verification_record.json --show-canonical | \
  head -c -1 | sha256sum          # matches integrity.record_sha256
```

**Why a forger cannot win:** editing a field breaks the hash (check 2 fails). Editing the field *and* recomputing the stored hash makes the file locally self-consistent, but produces a **different** hash — one that was never anchored on-chain, so the on-chain lookup fails (check 3). The attacker cannot rewrite the original blockchain entry, and cannot back-date a new one.

---

## 10. Ethereum Sepolia blockchain

**Blockchain: Ethereum Sepolia testnet (chain ID 11155111).**

Chosen because it is the standard, well-supported Ethereum test network: free faucet ETH, full Etherscan explorer support so judges can independently inspect the transaction in a browser, and identical EVM semantics to mainnet — the same contract would deploy unchanged to a production chain.

The client **hard-checks the chain ID** and refuses to operate on any other network.

Two values are written per record:

| Field | Type | Purpose |
|---|---|---|
| `recordHash` | `bytes32` | SHA-256 of the canonical evidence |
| `urlHash` | `bytes32` | keccak256 of the matched URL |
| `blockTime` | `uint64` | block timestamp — an immutable *before* proof |
| `submitter` | `address` | who paid for the transaction |

---

## 11. Smart contract

`contracts/FaceVerificationRegistry.sol` — Solidity `^0.8.20`, no owner, no upgrade path, no delete, no update. Once written, an entry is permanent. That immutability is precisely what makes the record tamper-evident.

### Improvements over v1

| v1 | v2 | Why |
|---|---|---|
| stored the full URL `string` | stores `keccak256(url)` as `bytes32` | Unbounded calldata and storage grew with URL length. A 32-byte commitment is equally tamper-evident, costs a fixed amount of gas, and avoids publishing the URL in plaintext on a permanent public ledger. The full URL stays in the off-chain record, which `recordHash` already commits to. |
| `uint256 timestamp` | `uint64 blockTime` | Packs with `address submitter` into one storage slot — cheaper, and still valid beyond the year 500,000. |
| `require` strings | custom `error` types | Cheaper, and gives verifiers machine-readable revert reasons. |
| `getVerification` returned zeros for missing records | **reverts** with `RecordNotFound` | Callers can never mistake "absent" for "zero-valued". |
| — | added `exists()` | Non-reverting existence check for verifier scripts. |
| — | added `verify(recordHash, urlHash)` | Confirms anchoring **and** URL binding in a **single** `eth_call`. |
| — | added `totalRecords` | Cheap enumeration/sanity checking. |
| event indexed only on hash+submitter | indexes `recordHash`, `urlHash`, `submitter` | A verifier can locate the anchoring transaction from the record hash alone, with no log scanning by address. |

**Duplicate `recordHash` values are rejected**, so the first anchoring of any evidence record is authoritative and its timestamp can never be overwritten or back-dated.

---

## 12. Setup

```bash
git clone https://github.com/samxkevin/FaceID-Blockchain-Verification.git
cd FaceID-Blockchain-Verification

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # + tests and contract deployment

cp .env.example .env                   # then fill it in (see §13)
```

`dlib-bin` provides prebuilt dlib wheels, so no CMake or C++ toolchain is required. If your platform has no wheel, fall back to `pip install dlib` (needs CMake).

---

## 13. Environment variables

| Variable | Required for | Notes |
|---|---|---|
| `SERPAPI_API_KEY` | Google Lens search | free tier at serpapi.com |
| `TINEYE_API_KEY` | TinEye search | alternative; accepts local files directly |
| `SEPOLIA_RPC_URL` | writing **and** verifying | e.g. `https://ethereum-sepolia-rpc.publicnode.com` |
| `PRIVATE_KEY` | **writing only** | throwaway faucet wallet — never a mainnet key |
| `CONTRACT_ADDRESS` | writing **and** verifying | printed by the deploy script |

You need **at least one** search provider key. `.env` is git-ignored. **Independent verification requires no private key and no API key** — only `SEPOLIA_RPC_URL` and `CONTRACT_ADDRESS`.

---

## 14. Deployment

Fund a throwaway Sepolia wallet from a faucet (e.g. https://sepoliafaucet.com), then:

```bash
python scripts/deploy_contract.py
```

This downloads solc 0.8.24 (once, cached), compiles with the optimizer enabled, deploys, and prints:

```
      Contract address:         0xAbCd…1234
      Explorer:                 https://sepolia.etherscan.io/address/0xAbCd…1234

Add this line to your .env file:

  CONTRACT_ADDRESS=0xAbCd…1234
```

Compile without deploying: `python scripts/deploy_contract.py --compile-only`

---

## 15. End-to-end execution

```bash
# Best: TinEye takes the local file directly — no upload anywhere else
python scripts/run_pipeline.py --image samples/photo.jpg --provider tineye

# Google Lens with an anonymous short-retention upload so it can fetch the image
python scripts/run_pipeline.py --image samples/photo.jpg --upload

# Google Lens reusing a public URL you already control (verified before use)
python scripts/run_pipeline.py --image samples/photo.jpg --image-url https://example.com/photo.jpg

# Dry run: everything except the blockchain write (no key, no gas)
python scripts/run_pipeline.py --image samples/photo.jpg --upload --skip-chain

# Demand strong evidence only
python scripts/run_pipeline.py --image samples/photo.jpg --upload \
    --require-tier PROVIDER_EXACT --require-post-url
```

Useful flags: `--face-model cnn`, `--face-upsample 2`, `--require-single-face`, `--corroborate-top N`, `--record <path>`.

---

## 16. Independent verification

```bash
python scripts/verify_record.py --record output/verification_record.json
```

No private key. No API key. Anyone holding the record file, an RPC URL and the contract address reaches the same verdict:

1. record file is structurally valid
2. the evidence subtree still hashes to `integrity.record_sha256` — **local tamper check**
3. that hash is anchored on-chain — **existence**
4. the on-chain hash equals the local hash — **integrity**
5. `keccak256(local matched URL)` equals the on-chain commitment — **URL binding**

Exit code `0` = VERIFIED / UNMODIFIED, non-zero = failed.

Options: `--offline` (local re-hash only), `--show-canonical` (print the exact hashed bytes), `--rpc-url` / `--contract` (override `.env`).

### Prove tamper-evidence on camera

```bash
python scripts/tamper_demo.py --record output/verification_record.json
python scripts/verify_record.py --record output/verification_record.TAMPERED.json   # exits 2
```

Verify entirely outside this codebase — the contract is public, so a judge can call `verify(recordHash, urlHash)` directly on Sepolia Etherscan's *Read Contract* tab.

---

## 17. Example expected output

### Pipeline

```
==========================================================================
 FACE ID + BLOCKCHAIN VERIFICATION
 local image -> face encoding -> reverse image search -> SHA-256 -> Ethereum Sepolia
==========================================================================

[1/5] Detecting and encoding face
      Face detected:            YES
      Faces:                    1
      Detector:                 dlib HOG (upsample 1)
      Encoding:                 128 dimensions
      Primary face box:         {'top': 96, 'right': 368, 'bottom': 225, 'left': 239, …}
      Encoding commitment:      cd767a564367ddbd302772e6f46e09e7...
      The encoding stays local. It is never uploaded and never written on-chain.
      A face encoding is not an identity; this step only proves a face was found.

[2/5] Running genuine reverse-image search
      Provider:                 Google Lens via SerpAPI
      Image transport:          ephemeral (0x0.st)
      Query image URL:          https://0x0.st/xxxx.jpg
      Candidates returned:      34
      Provider exact matches:   2
      Response sections:        {'exact_matches': 2, 'visual_matches': 32}
      Every candidate below came from this live response. Nothing is hardcoded.

[3/5] Selecting and corroborating the social-media match
      Social candidates:        6
      Provider match type:      EXACT
      Evidence tier:            VERIFIED_EXACT
      Social platform:          Instagram
      Matched URL:              https://www.instagram.com/p/Cx9AbCdEfGh/
      Local hash check:         dHash 0/10, pHash 0/10 => CORROBORATED
--------------------------------------------------------------------------
      The search provider reported this page as an exact-image match,
      and an independent local perceptual-hash comparison of the
      provider's thumbnail agrees it is the same picture.

[4/5] Creating tamper-evident evidence record
      Image SHA-256:            18e0eaf89dd576e06e2672bc75f8c86ff7f1ddc0f…
      Record SHA-256:           9428d2cf74d29c766c87c7744f7be7f3df0f4a10e…
      Schema version:           2.0
      The hash covers the whole evidence subtree: change any field and it changes.

[5/5] Registering evidence on Ethereum Sepolia
      Network:                  ethereum-sepolia (chain id 11155111)
      Contract:                 0xAbCd…1234
      Transaction:              0x7f3a…9e21
      Block:                    6543210
      Gas used:                 68432
      Explorer:                 https://sepolia.etherscan.io/tx/0x7f3a…9e21

==========================================================================
 STATUS: VERIFICATION REGISTERED
==========================================================================
```

### Verification

```
[1/5] Checking local evidence        OK   Record structure is valid.
[2/5] Checking record hash           OK   The evidence subtree still hashes to the stored value.
[3/5] Checking blockchain            OK   An on-chain record exists for this hash.
[4/5] Comparing hashes               OK   All three hashes are identical.
[5/5] Comparing matched URL          OK   The matched URL is exactly the one committed on-chain.

==========================================================================
 STATUS: VERIFIED / UNMODIFIED
==========================================================================
```

### Tampering

```
[3/3] Re-checking integrity
      Committed SHA-256:        9428d2cf74d29c766c87c7744f7be7f3df0f4a10e…
      Recomputed SHA-256:       2274648629fe85aec639dd3d99a6ef33341a2c072…
      OK   The hashes now differ: the modification is detectable.

 STATUS: TAMPERING DETECTED
```

---

## 18. Testing

```bash
pip install -r requirements-dev.txt
pytest                      # 191 tests
pytest -v                   # verbose
pytest --tb=short -q        # concise
```

**No test requires your API keys, your private key, or any network access.** All external APIs are mocked; the contract tests run on an in-process EVM.

| File | Tests | Covers |
|---|---|---|
| `test_hashing.py` | 24 | canonical JSON determinism, key-order independence, Unicode, NaN rejection, file hashing, digest sensitivity |
| `test_record.py` | 31 | evidence construction, hash sensitivity per field, tamper detection, self-consistent forgery, persistence, float rejection, no-biometric-leak, no-path-leak |
| `test_selection.py` | 19 | exact-over-visual preference, determinism, post-shape preference, tier assignment, corroboration reordering, blocked-thumbnail handling |
| `test_social.py` | 34 | platform detection, lookalike-domain rejection, post-shape regexes, utility-page demotion |
| `test_providers.py` | 18 | SerpAPI/TinEye parsing, section→type mapping, dedup, malformed entries, HTTP 401/429/error paths |
| `test_blockchain.py` | 28 | hex encoding, keccak URL hashing, every config error, wrong network, record-not-found, URL-substitution detection, key-never-leaked |
| `test_perceptual.py` | 12 | hash determinism, JPEG/downscale robustness, different-image rejection, no fabricated confidence scores |
| `test_face.py` | 11 | input validation, error remedies, metadata contract, encoding commitment, primary-face selection |
| `test_contract_integration.py` | 14 | **real solc compile + in-process EVM**: registration, events, duplicate rejection, zero-value rejection, revert-on-missing, `verify()`, end-to-end tamper detection |

Notable adversarial tests: a forger who edits a field *and* recomputes the stored hash still produces a commitment that was never anchored; substituting the matched URL is caught by the on-chain keccak comparison; private keys never appear in any error message.

> The contract integration tests skip automatically if `solc` cannot be downloaded (offline CI); everything else still runs.

---

## 19. Security considerations

- **Private keys** are read from the environment only. Never logged, never written to the record, never included in an error message — there is an explicit test asserting a bad key is not echoed back.
- **Use a throwaway wallet** holding only faucet ETH. Never a key controlling mainnet funds.
- **`.env` is git-ignored.** Verify with `git check-ignore .env` before recording.
- **Reading requires no key**, so verification can be delegated to anyone safely.
- **Chain-ID enforcement** prevents accidentally broadcasting to the wrong network.
- **No biometric data on-chain.** Only a one-way commitment reaches the record; the encoding never leaves the machine.
- **Minimal PII in the record:** file name only, OS family only, no absolute paths, no hostname, no username.
- **`--upload` is opt-in.** Uploading the photograph to a third-party bin never happens silently.
- **Anyone can write to the contract.** It is a public notary: `submitter` records *who* anchored a claim, but the contract makes no statement about whether that party is trustworthy.
- **URLs are committed as hashes**, avoiding permanently publishing a plaintext link to someone's social profile on a public ledger.

---

## 20. Known limitations

These are real and are **not** hidden. Where a limitation cannot be eliminated, the architecture mitigates it and the record documents it.

1. **Google Lens requires a fetchable image URL.** *Mitigated:* `--provider tineye` avoids it entirely; `--upload` uses throwaway bins; `--image-url` is verified before use. The transport used is always recorded.
2. **A visual match is not proof the identical image is on the page.** *Mitigated:* exact and visual are never conflated; we independently perceptually-hash the provider thumbnail; the resulting **evidence tier** states the strength precisely.
3. **Corroboration uses the provider's thumbnail, not the post's original file.** Thumbnails are downscaled, re-encoded derivatives. A low Hamming distance corroborates "the same picture", not byte equality. Stated in the record.
4. **Social platforms block automated fetches**, and posts may be private, deleted or region-locked. *Mitigated:* fetch failures are recorded as "not corroborated" and are never fatal; the pipeline degrades to a weaker, honestly-labelled tier. The pipeline deliberately does **not** scrape platforms in violation of their terms.
5. **Provider coverage varies.** Neither Lens nor TinEye indexes all social content, especially login-walled posts. A negative result is a genuine negative, not a bug — and the pipeline says so rather than inventing a match.
6. **Sepolia is a testnet.** Its data is not economically secured like mainnet and testnets can be deprecated. The same contract deploys unchanged to any EVM chain; the integrity model is identical.
7. **The blockchain proves record integrity, not real-world truth.** It is a notary, not an oracle: it proves *what* was recorded and *when*, not that the recorded claim is true.
8. **A face encoding is not an identity.** No identity claim is made anywhere.
9. **The URL is committed as a hash**, so an observer cannot enumerate URLs from the chain alone — by design. Verification requires the off-chain record too.
10. **Perceptual hash thresholds (10 bits) are a judgement call.** They are recorded in every record so any reviewer can recompute the verdict under their own threshold.
11. **`created_at_utc` is the local machine's clock** and is self-asserted. The trustworthy timestamp is `blockTime`, set by the network.
12. **Third-party API dependency.** SerpAPI and TinEye are paid services with quotas; the pipeline cannot run without one.

---

## 21. What the blockchain proves

✅ A record with **exactly this SHA-256 hash existed before block N** at the recorded timestamp.
✅ The evidence file has **not been altered by a single byte** since anchoring.
✅ The matched URL is **exactly** the one committed at anchoring time (keccak256 binding).
✅ The address in `submitter` **paid for and authorised** that anchoring.
✅ The commitment is **immutable** — no owner, no update, no delete, no upgrade path.
✅ **Ordering:** the evidence provably predates the block, so it cannot be back-dated.

---

## 22. What the blockchain does NOT prove

❌ **The identity of any person in the photograph.** Face detection finds *a* face; it does not name anyone.
❌ **That anyone owns or controls the matched social-media account.** A photo appearing somewhere says nothing about account ownership.
❌ **That the matched page contains the identical image**, unless the tier is `VERIFIED_EXACT` or `PROVIDER_EXACT` — and even then it reflects the provider's index at search time.
❌ **That the reverse-image-search provider is correct.** We record what the provider returned; we do not vouch for it.
❌ **That the page still exists**, is public, or is unchanged since the search.
❌ **That the input photograph is authentic** or unedited. We hash what we were given.
❌ **That the submitter is honest.** Anyone can anchor any hash; the chain records *who* and *when*, not *whether it is true*.

> **In one sentence:** this system provides a cryptographically verifiable, timestamped, tamper-evident record of *what a reverse-image search returned for a specific image at a specific moment* — and nothing more.

---

## 23. Implementation status

### ✅ IMPLEMENTED — working, tested, demonstrable

- Face detection + 128-d encoding with full input validation and multi-face handling
- Live Google Lens (SerpAPI) and TinEye reverse-image search adapters
- Three image transports: direct upload, verified public URL, ephemeral anonymous bin
- Exact / page / visual match-type fidelity, never upgraded
- 19-platform social classification with post-shape detection
- Independent perceptual-hash corroboration (aHash/dHash/pHash)
- Four-level evidence-tier system with plain-English meanings
- Deterministic, evidence-based ranking
- Canonical JSON + SHA-256 evidence record, schema v2.0
- Immutable Sepolia registry contract with `verify()` / `exists()` helpers
- Pure-Python compile + deploy (`py-solc-x`, no Node toolchain)
- Independent verification requiring no keys
- Tamper demonstration script
- Typed errors with remedies for every failure mode
- 191 automated tests, no keys or network required

### 🧪 EXPERIMENTAL — implemented, but with caveats

- **Perceptual corroboration of provider thumbnails.** Works well, but operates on downscaled derivatives, and platforms frequently block thumbnail fetches. Never fatal; degrades to a lower tier.
- **Ephemeral upload transport.** Depends on third-party bins that may rate-limit or disappear; three are tried in order.
- **Post-shape regexes.** Platforms change URL formats; patterns are best-effort and only *demote*, never hard-reject by default.

### ⚙️ OPTIONAL

- TinEye provider (needs a separate paid key) — enables the strongest, upload-free transport
- `--require-tier`, `--require-post-url` strictness gates
- `--face-model cnn` for higher detection sensitivity
- `--skip-chain` dry runs

### 🔭 FUTURE WORK — deliberately NOT claimed as working

- Fetching the social post's **own** image for byte-level comparison (blocked by platform ToS and login walls)
- Cryptographic proof of social-account **ownership** (would require a challenge–response signature from the account holder — the only sound way to prove control)
- Mainnet or L2 (Base/Arbitrum) deployment for economic security
- Merkle-batching many records into one transaction to cut gas
- C2PA / content-credential provenance checks on the input image
- Multi-provider cross-confirmation (requiring two independent providers to agree)

---

## 24. Screen-recording checklist

A clean, unedited run for judging. Total ≈ 4–5 minutes.

**Before recording**
- [ ] `source .venv/bin/activate`; `.env` filled in; wallet has faucet ETH
- [ ] Contract deployed, `CONTRACT_ADDRESS` set
- [ ] Test image with a clear face at `samples/photo.jpg`
- [ ] Terminal font enlarged; `output/` cleared
- [ ] **Confirm `.env` is never opened or visible on screen**

**Record this sequence**

```bash
# 1. Show the code is real and nothing is hardcoded  (~30s)
git log --oneline -5
grep -ri "instagram.com/p/" src/ --include=*.py     # only regex patterns, no fixed URLs
pytest -q                                            # 191 passing

# 2. Show the input image  (~10s)
ls -la samples/photo.jpg

# 3. Full pipeline, end to end  (~90s)
python scripts/run_pipeline.py --image samples/photo.jpg --upload
#    → face detected, live candidate count, evidence tier,
#      image SHA-256, record SHA-256, Sepolia tx hash + block

# 4. Show the evidence record  (~20s)
cat output/verification_record.json | head -60

# 5. Independent verification  (~30s)
python scripts/verify_record.py --record output/verification_record.json
#    → STATUS: VERIFIED / UNMODIFIED

# 6. Prove tamper-evidence  (~40s)
python scripts/tamper_demo.py --record output/verification_record.json
python scripts/verify_record.py --record output/verification_record.TAMPERED.json
#    → STATUS: VERIFICATION FAILED (exit code 2)

# 7. Show the original still verifies  (~15s)
python scripts/verify_record.py --record output/verification_record.json
```

**8. Show it on the public blockchain (~30s)** — open `https://sepolia.etherscan.io/tx/<TX_HASH>` in a browser, show the transaction, then the contract's *Read Contract* → `verify(recordHash, urlHash)` returning `true, true`.

**Narration points to hit**
- [ ] "The reverse-image search is live — these candidates come from the provider's response, nothing is hardcoded."
- [ ] "The evidence tier tells you exactly how strong this match is."
- [ ] "The face encoding never leaves this machine and is never written on-chain."
- [ ] "This proves the record is unaltered and predates the block — it does **not** prove anyone's identity or that they own that account."

---

## License

MIT — see [LICENSE](LICENSE).
