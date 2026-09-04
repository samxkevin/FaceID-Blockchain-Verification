# FaceID-Blockchain-Verification

AI pipeline for face detection and encoding, genuine reverse-image search, social-media match verification, and tamper-evident blockchain recording.

## HHgoa Task #3

The pipeline performs the complete workflow:

1. Detect a face in an input photograph.
2. Generate a local face encoding.
3. Run a genuine reverse-image search through Google Lens via SerpAPI.
4. Inspect returned results and select a returned social-media URL when available.
5. Build a canonical evidence record and compute its SHA-256 digest.
6. Register the digest and matched URL on Ethereum Sepolia.
7. Read the record back from the blockchain and independently verify integrity.

No social-media result is hardcoded into the search stage.

## Architecture

```text
Input photograph
      |
      v
Face detection + encoding
      |
      v
Genuine reverse-image search
      |
      v
Returned web/social results
      |
      v
Evidence normalization
      |
      v
Canonical JSON + SHA-256
      |
      v
Ethereum Sepolia registry
      |
      v
Transaction confirmation
      |
      v
Independent on-chain verification
```

## Components

### Face processing

The project uses `face_recognition` for face detection and 128-dimensional face encodings. The encoding is generated locally and is not written to the blockchain.

### Genuine reverse-image search

The default adapter uses SerpAPI's Google Lens engine. The input image must be available at a public HTTP(S) URL because the search provider retrieves the image from that URL.

Set:

```env
SERPAPI_API_KEY=your_key
```

The application reads the provider response dynamically. It does not contain a hardcoded Instagram, Facebook, X, TikTok, LinkedIn, or Threads result.

The social-domain filter is only used to classify returned candidates. A URL is accepted only after it has actually been returned by the reverse-image-search provider.

### Tamper-evident record

The record contains non-biometric evidence such as the input-image SHA-256, face count, encoding dimension, reverse-search provider, returned URL, title/source metadata, result type, and recording timestamp.

The record is serialized deterministically with sorted JSON keys and compact separators before SHA-256 hashing. This makes the digest reproducible for an unchanged record.

### Blockchain

Blockchain: **Ethereum Sepolia testnet**.

The Solidity contract stores:

- `recordHash`
- matched URL
- block timestamp
- submitting wallet address

The photograph and face encoding remain off-chain.

## Repository structure

```text
FaceID-Blockchain-Verification/
├── contracts/
│   └── FaceVerificationRegistry.sol
├── src/
│   ├── face/
│   │   └── encoder.py
│   ├── reverse_search/
│   │   └── serpapi_lens.py
│   ├── verification/
│   │   └── record.py
│   └── blockchain/
│       └── registry.py
├── scripts/
│   ├── run_pipeline.py
│   └── verify_record.py
├── .env.example
├── .gitignore
└── requirements.txt
```

## Setup

Python 3.10+ is recommended.

```bash
python -m venv .venv

# Windows
.venv\\Scripts\\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

Create `.env` from `.env.example`:

```env
SERPAPI_API_KEY=...
SEPOLIA_RPC_URL=...
PRIVATE_KEY=...
CONTRACT_ADDRESS=0x...
```

The wallet must be funded with Sepolia test ETH. Never commit `.env` or a private key.

## Deploying the contract

Compile and deploy `contracts/FaceVerificationRegistry.sol` to Ethereum Sepolia using a Solidity development tool such as Remix or Hardhat. Put the deployed contract address in `CONTRACT_ADDRESS`.

The contract requires Solidity `^0.8.20`.

## Running the pipeline

Because Google Lens via SerpAPI expects an image URL, provide both the local image and a public URL for that same image:

```bash
python scripts/run_pipeline.py \
  --image examples/input.jpg \
  --image-url https://example.com/input.jpg
```

The command prints:

- detected face count
- encoding dimensions
- input-image SHA-256
- reverse-search provider
- returned candidate count
- selected returned social-media URL
- verification-record hash
- Ethereum transaction hash
- block number
- Sepolia explorer transaction URL

The generated evidence record is written to `output/verification_record.json`.

## Independent verification

After the transaction is confirmed:

```bash
python scripts/verify_record.py --record output/verification_record.json
```

The verifier recomputes the local digest, reads the corresponding record from the smart contract, and checks both the record hash and matched URL.

Expected result:

```text
STATUS: VERIFIED / UNMODIFIED
```

## What the blockchain proves

The blockchain does not prove that a person legally owns a social-media account or that two people are the same individual. It provides a tamper-evident timestamped commitment to the verification record.

If the local evidence record is modified after registration, its recomputed SHA-256 no longer matches the value stored on-chain and verification fails.

## Known limitations

- Google Lens/SerpAPI may return visually similar results instead of an exact duplicate.
- A returned social-media page may be inaccessible, deleted, private, or changed after the search.
- Reverse-image matching is evidence of image/web association, not conclusive identity verification.
- The current adapter requires a publicly reachable image URL for the search provider.
- The project deliberately does not place biometric face embeddings on-chain.
- Ethereum Sepolia is a test network and is not suitable as a production evidence ledger without additional security, privacy, key-management, and legal controls.

## Security

Keep `PRIVATE_KEY` and `SERPAPI_API_KEY` in environment variables. Never commit secrets. Use a dedicated testnet wallet for Sepolia.

## License

MIT
