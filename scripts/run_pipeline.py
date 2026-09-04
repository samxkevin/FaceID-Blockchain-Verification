import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.blockchain.registry import register
from src.face.encoder import detect_and_encode
from src.reverse_search.serpapi_lens import choose_social_match, reverse_image_search
from src.verification.record import canonical_record, record_hash, save_record, sha256_file


def main():
    parser = argparse.ArgumentParser(description="Face -> reverse image search -> blockchain verification")
    parser.add_argument("--image", required=True, help="Local input photograph")
    parser.add_argument("--image-url", required=True, help="Public HTTPS URL of the same image for Google Lens")
    parser.add_argument("--record", default="output/verification_record.json")
    args = parser.parse_args()
    load_dotenv()

    print("[1/6] Detecting and encoding face...")
    face_result = detect_and_encode(args.image)
    print(f"      Faces detected: {face_result.face_count}")
    print(f"      Encoding dimensions: {face_result.encodings[0].shape[0]}")

    print("[2/6] Hashing input image...")
    image_hash = sha256_file(args.image)
    print(f"      SHA-256: {image_hash}")

    print("[3/6] Running genuine reverse-image search...")
    search = reverse_image_search(args.image_url)
    print(f"      Provider: {search['provider']}")
    print(f"      Candidates returned: {search['raw_result_count']}")

    print("[4/6] Selecting a returned social-media result...")
    match = choose_social_match(search)
    print(f"      Title: {match['title']}")
    print(f"      URL: {match['url']}")
    print(f"      Source: {match['source']}")

    record = canonical_record(image_hash, face_result.face_count, face_result.encodings[0].shape[0], match, search["provider"])
    digest = record_hash(record)
    record["record_sha256"] = digest
    save_record(record, args.record)

    print("[5/6] Registering record on Ethereum Sepolia...")
    chain = register(digest, match["url"])
    print(f"      Transaction: {chain['transaction_hash']}")
    print(f"      Block: {chain['block_number']}")
    print(f"      Wallet: {chain['submitter']}")

    print("[6/6] Complete")
    print(f"      Record: {Path(args.record).resolve()}")
    print(f"      Record hash: {digest}")
    print(f"      Explorer: https://sepolia.etherscan.io/tx/{chain['transaction_hash']}")
    print("\nSTATUS: VERIFICATION REGISTERED")


if __name__ == "__main__":
    main()
