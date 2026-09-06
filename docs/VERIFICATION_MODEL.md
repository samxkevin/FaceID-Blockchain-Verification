# The verification model, precisely

This document states exactly what each stage of the pipeline establishes, the
adversary model it defends against, and where the guarantees stop. It is the
technical companion to README §21/§22.

## 1. Trust boundaries

| Component | Trusted? | Why it matters |
|---|---|---|
| The input photograph | **No** | We hash whatever we are given. We cannot tell an authentic photo from an edited or AI-generated one. |
| dlib / face_recognition | Partially | Used only to establish "a face is present". No identity conclusion depends on it. |
| SerpAPI / TinEye | **No** | Their claims are *recorded as claims*, attributed to them by name, and independently cross-checked where possible. |
| Provider thumbnails | **No** | Fetched and hashed by us; a fetch failure is recorded, never assumed to be a pass. |
| Ephemeral upload bins | **No** | Only transport. They cannot influence the result: the record commits to the *local* file's SHA-256, not the uploaded copy. |
| Our own SHA-256 / canonical JSON | Yes | Pure, deterministic, independently reproducible with `sha256sum`. |
| Ethereum Sepolia consensus | Yes | For ordering and immutability only — not for the truth of the claim. |
| The submitter | **No** | Anyone can anchor any hash. The chain records *who* and *when*, not *whether it is true*. |

## 2. What each stage actually establishes

### Stage 1 — Face
**Establishes:** a face-shaped region was detected in the input image and a
128-dimensional encoding was computed from it, using a named detector at a named
upsample setting.

**Does not establish:** who the person is; that the image is of a real person;
that the same person appears in any matched result. No identity database is ever
consulted.

### Stage 2 — Reverse image search
**Establishes:** at time T, provider P returned this specific set of results for
this specific image.

**Does not establish:** that the results are correct, complete, or current.
Absence of a result is not evidence of absence from the internet — it is only
evidence of absence from that provider's index at that moment.

### Stage 3 — Selection and corroboration
**Establishes:** among the returned results, this one ranked highest under a
published deterministic rule; and, when a thumbnail was fetchable, our own
perceptual hashes place it within a stated bit-distance of the input.

**Does not establish:** byte equality with the file on the social post. The
thumbnail is a downscaled, re-encoded derivative held by the *search provider*,
not the post's original asset. This is why the tier is named
`CORROBORATED_VISUAL` rather than "confirmed identical".

### Stage 4 — Evidence record
**Establishes:** a single canonical byte string capturing every observation
above, whose SHA-256 changes if any meaningful field changes.

### Stage 5 — Blockchain anchor
**Establishes:** that byte string existed before block N, was submitted by
address A, and has not changed since.

**Does not establish:** anything about the truth of its contents.

## 3. Adversary model

### Adversary A — tampers with the record after anchoring
Edits `matched_url` in the JSON.
**Caught by:** check 2. The recomputed hash no longer equals the stored hash.

### Adversary B — tampers *and* rewrites the stored hash
Makes the file internally self-consistent.
**Caught by:** check 3. The new hash was never anchored, so the on-chain lookup
returns "not found". The original entry is immutable and cannot be overwritten
(duplicate `recordHash` reverts), and a new anchor would carry a later block
timestamp.

### Adversary C — swaps the URL but keeps the record hash
**Caught by:** check 5. `keccak256(url)` is committed separately on-chain; the
comparison fails.

### Adversary D — back-dates a record
**Caught by:** block timestamps are set by network consensus, not the submitter.
`created_at_utc` is self-asserted and explicitly *not* the trusted timestamp.

### Adversary E — anchors a fabricated evidence record
**NOT prevented.** Anyone can hash arbitrary JSON and anchor it. The chain proves
the record existed at that time and who submitted it — it does not audit the
pipeline that produced it. This is inherent to any notarization system and is why
`submitter` is recorded. Mitigation is social, not cryptographic: run the
pipeline live, on camera, from an unmodified checkout.

### Adversary F — poisons the search provider's index
**NOT prevented**, and out of scope. The record attributes the claim to the named
provider precisely so this remains visible.

## 4. Why no confidence score

A number like "94% confident" would imply a calibrated probabilistic model. We
have no labelled dataset, no calibration, and no basis for such a figure —
inventing one would be the single most misleading thing this project could do.

Instead we report:
- the provider's **own** classification (exact / page / visual), verbatim;
- **raw integer** Hamming distances we measured ourselves;
- the **thresholds** applied, stored in the record so any reviewer can re-derive
  the verdict under different thresholds;
- a **discrete tier** with a plain-English statement of exactly what it asserts.

Every number in the record is either a hash, a count, or a measured distance.

## 5. The one sound way to prove account ownership

To actually prove a person controls a social account, they must demonstrate
control of it — for example by posting a challenge nonce, or signing it with a
key bound to a verified profile. That is a fundamentally different protocol
requiring the subject's cooperation, and it is listed under FUTURE WORK rather
than implied to work today.

A reverse-image match can never substitute for it.
