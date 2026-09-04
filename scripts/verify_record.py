import argparse
import json

from dotenv import load_dotenv

from src.blockchain.registry import fetch
from src.verification.record import record_hash


def main():
    parser = argparse.ArgumentParser(description="Verify a local record against Ethereum Sepolia")
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    load_dotenv()

    with open(args.record, encoding="utf-8") as f:
        record = json.load(f)

    stored_hash = record.pop("record_sha256")
    local_hash = record_hash(record)
    chain = fetch(stored_hash)

    print(f"Local SHA-256:  {local_hash}")
    print(f"Stored SHA-256: {stored_hash}")
    print(f"Chain SHA-256:  {chain['record_hash']}")
    print(f"Chain URL:      {chain['matched_url']}")
    print(f"Submitter:      {chain['submitter']}")
    print(f"Block timestamp:{chain['timestamp']}")

    if local_hash.lower() != stored_hash.lower():
        raise SystemExit("FAILED: local record has been modified.")
    if chain["record_hash"].lower() != stored_hash.lower():
        raise SystemExit("FAILED: blockchain hash does not match.")
    if chain["matched_url"] != record["matched_url"]:
        raise SystemExit("FAILED: blockchain URL does not match.")

    print("\nSTATUS: VERIFIED / UNMODIFIED")


if __name__ == "__main__":
    main()
