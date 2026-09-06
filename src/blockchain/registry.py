"""Web3 client for the FaceVerificationRegistry contract on Ethereum Sepolia.

Security posture
----------------
* The private key is read from the environment only, is never logged, never
  written to the evidence record, and never included in an error message.
* Only the derived public address is ever displayed or recorded.
* Reading from the chain (`fetch`, `verify_on_chain`) requires no key at all, so
  a third party can independently verify a record with nothing but an RPC URL
  and the contract address.

Every failure mode named in the task brief - missing RPC, missing key, bad
contract address, wrong network, transaction failure, record not found - maps to
a distinct typed error with a remedy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any

from src.core.errors import (
    ChainConfigError,
    ChainConnectionError,
    ContractError,
    RecordNotFoundError,
    TransactionError,
    WrongNetworkError,
)
from src.core.hashing import is_sha256_hex, normalize_hex

#: Sepolia. Kept explicit so the client refuses to anchor on the wrong chain.
SEPOLIA_CHAIN_ID = 11155111
EXPLORER = "https://sepolia.etherscan.io"

ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "recordHash", "type": "bytes32"},
            {"internalType": "bytes32", "name": "urlHash", "type": "bytes32"},
        ],
        "name": "registerVerification",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "recordHash", "type": "bytes32"}],
        "name": "getVerification",
        "outputs": [
            {"internalType": "bytes32", "name": "", "type": "bytes32"},
            {"internalType": "bytes32", "name": "", "type": "bytes32"},
            {"internalType": "uint64", "name": "", "type": "uint64"},
            {"internalType": "address", "name": "", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "recordHash", "type": "bytes32"}],
        "name": "exists",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "recordHash", "type": "bytes32"},
            {"internalType": "bytes32", "name": "urlHash", "type": "bytes32"},
        ],
        "name": "verify",
        "outputs": [
            {"internalType": "bool", "name": "anchored", "type": "bool"},
            {"internalType": "bool", "name": "urlMatches", "type": "bool"},
            {"internalType": "uint64", "name": "blockTime", "type": "uint64"},
            {"internalType": "address", "name": "submitter", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalRecords",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "recordHash", "type": "bytes32"},
            {"indexed": True, "internalType": "bytes32", "name": "urlHash", "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "submitter", "type": "address"},
            {"indexed": False, "internalType": "uint64", "name": "blockTime", "type": "uint64"},
            {"indexed": False, "internalType": "uint256", "name": "recordIndex", "type": "uint256"},
        ],
        "name": "VerificationRegistered",
        "type": "event",
    },
]


def url_hash(url: str) -> str:
    """keccak256 of a URL string, as 0x-prefixed hex.

    keccak256 (not SHA-256) because that is what Solidity computes natively, so
    the contract and any other on-chain consumer agree with us for free.
    """
    from web3 import Web3

    if not isinstance(url, str) or not url:
        raise ContractError(
            "Cannot hash an empty matched URL.",
            remedy="The evidence record must contain a non-empty matched_url.",
        )
    # web3 v6 returns "0x..." from .hex(); v7 returns bare hex. Normalize both.
    return _hex(Web3.keccak(text=url))


def _to_bytes32(hex_digest: str) -> bytes:
    """Convert a 64-char hex digest into exactly 32 bytes."""
    cleaned = normalize_hex(hex_digest)
    if len(cleaned) != 64:
        raise ContractError(
            f"Expected a 32-byte (64 hex character) value, got {len(cleaned)} characters.",
            remedy="Pass a valid SHA-256 or keccak256 digest.",
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ContractError(
            f"Value is not valid hexadecimal: {hex_digest!r}",
            remedy="Pass a valid hex digest.",
        ) from exc


def _hex(value: Any) -> str:
    """Normalize web3 return values (bytes / HexBytes / str) to 0x-prefixed hex."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    text = str(value)
    return text if text.startswith("0x") else "0x" + text


def get_client(rpc_url: str | None = None, expected_chain_id: int | None = SEPOLIA_CHAIN_ID):
    """Connect to the RPC endpoint and confirm we are on the expected network."""
    try:
        from web3 import Web3
    except ImportError as exc:  # pragma: no cover
        raise ChainConfigError(
            "The 'web3' package is not installed.",
            remedy="Install it with: pip install -r requirements.txt",
        ) from exc

    rpc = rpc_url or os.getenv("SEPOLIA_RPC_URL", "")
    if not rpc:
        raise ChainConfigError(
            "SEPOLIA_RPC_URL is not configured.",
            remedy=(
                "Add SEPOLIA_RPC_URL to your .env file. Free endpoints are available from "
                "Alchemy, Infura, or https://ethereum-sepolia-rpc.publicnode.com"
            ),
        )
    if not rpc.lower().startswith(("http://", "https://", "ws://", "wss://")):
        raise ChainConfigError(
            f"SEPOLIA_RPC_URL does not look like a URL: {rpc[:40]}",
            remedy="It should start with https://",
        )

    web3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
    try:
        connected = web3.is_connected()
    except Exception as exc:  # noqa: BLE001
        raise ChainConnectionError(
            f"Could not reach the RPC endpoint: {exc}",
            remedy="Check the URL, your API key allowance, and your network connection.",
        ) from exc
    if not connected:
        raise ChainConnectionError(
            "The configured RPC endpoint did not respond.",
            remedy="Verify SEPOLIA_RPC_URL; free public endpoints are frequently rate limited.",
        )

    if expected_chain_id is not None:
        actual = web3.eth.chain_id
        if actual != expected_chain_id:
            raise WrongNetworkError(
                f"Connected to chain id {actual}, but {expected_chain_id} (Sepolia) was expected.",
                remedy="Point SEPOLIA_RPC_URL at an Ethereum Sepolia endpoint.",
            )
    return web3


def get_contract(web3, address: str | None = None):
    """Load the registry contract, verifying the address holds deployed code."""
    raw = address or os.getenv("CONTRACT_ADDRESS", "")
    if not raw:
        raise ChainConfigError(
            "CONTRACT_ADDRESS is not configured.",
            remedy="Deploy contracts/FaceVerificationRegistry.sol and put its address in .env.",
        )
    try:
        checksum = web3.to_checksum_address(raw.strip())
    except Exception as exc:  # noqa: BLE001
        raise ChainConfigError(
            f"CONTRACT_ADDRESS is not a valid Ethereum address: {raw!r}",
            remedy="It must be a 42-character 0x-prefixed hex address.",
        ) from exc

    code = web3.eth.get_code(checksum)
    if not code or code in (b"", b"0x"):
        raise ContractError(
            f"No contract code is deployed at {checksum} on this network.",
            remedy=(
                "The address may belong to a different network, or the deployment "
                "did not confirm. Re-run scripts/deploy_contract.py."
            ),
        )
    return web3.eth.contract(address=checksum, abi=ABI)


def _load_account(web3, private_key: str | None = None):
    """Derive the signing account. The key itself is never returned or logged."""
    key = private_key or os.getenv("PRIVATE_KEY", "")
    if not key:
        raise ChainConfigError(
            "PRIVATE_KEY is not configured.",
            remedy=(
                "Add a Sepolia TEST wallet key to .env as PRIVATE_KEY. "
                "Never use a key that holds real mainnet funds."
            ),
        )
    key = key.strip()
    if not key.startswith("0x"):
        key = "0x" + key
    if len(key) != 66:
        raise ChainConfigError(
            "PRIVATE_KEY is not a valid 32-byte hex key.",
            remedy="It should be 64 hex characters, optionally prefixed with 0x.",
        )
    try:
        return web3.eth.account.from_key(key)
    except Exception as exc:  # noqa: BLE001 - message deliberately omits the key
        raise ChainConfigError(
            "PRIVATE_KEY could not be parsed as an Ethereum private key.",
            remedy="Export the key again from your wallet; it must be 64 hex characters.",
        ) from exc


@dataclass(frozen=True)
class RegistrationReceipt:
    """Everything needed to locate and re-check the anchoring transaction."""

    transaction_hash: str
    block_number: int
    block_time_unix: int
    submitter: str
    chain_id: int
    network: str
    contract_address: str
    record_sha256: str
    url_keccak256: str
    gas_used: int
    explorer_tx_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def register(
    record_hash_hex: str,
    matched_url: str,
    rpc_url: str | None = None,
    private_key: str | None = None,
    contract_address: str | None = None,
) -> RegistrationReceipt:
    """Anchor a record hash + URL commitment on Sepolia."""
    if not is_sha256_hex(record_hash_hex):
        raise ContractError(
            f"record_sha256 is not a valid SHA-256 digest: {record_hash_hex!r}",
            remedy="Regenerate the evidence record.",
        )

    web3 = get_client(rpc_url)
    contract = get_contract(web3, contract_address)
    account = _load_account(web3, private_key)

    record_bytes = _to_bytes32(record_hash_hex)
    url_digest = url_hash(matched_url)
    url_bytes = _to_bytes32(url_digest)

    balance = web3.eth.get_balance(account.address)
    if balance == 0:
        raise TransactionError(
            f"Wallet {account.address} has 0 Sepolia ETH and cannot pay for gas.",
            remedy="Fund it from a Sepolia faucet, e.g. https://sepoliafaucet.com",
        )

    if contract.functions.exists(record_bytes).call():
        raise TransactionError(
            "This exact evidence record hash is already anchored on-chain.",
            remedy=(
                "That is itself a valid result - verify it with scripts/verify_record.py. "
                "To create a new anchor, re-run the pipeline (the timestamp will differ, "
                "producing a new record hash)."
            ),
        )

    function = contract.functions.registerVerification(record_bytes, url_bytes)
    try:
        gas_estimate = function.estimate_gas({"from": account.address})
    except Exception as exc:  # noqa: BLE001
        raise TransactionError(
            f"Gas estimation failed, so the transaction would revert: {exc}",
            remedy="Confirm CONTRACT_ADDRESS points at FaceVerificationRegistry on Sepolia.",
        ) from exc

    tx_params: dict[str, Any] = {
        "from": account.address,
        "nonce": web3.eth.get_transaction_count(account.address, "pending"),
        "chainId": web3.eth.chain_id,
        # 25% headroom over the estimate.
        "gas": int(gas_estimate * 125 // 100),
    }
    try:
        base_fee = web3.eth.get_block("latest").get("baseFeePerGas")
        priority = web3.to_wei(1.5, "gwei")
        tx_params["maxPriorityFeePerGas"] = priority
        tx_params["maxFeePerGas"] = int(base_fee * 2) + priority
    except Exception:  # noqa: BLE001 - legacy/no EIP-1559 endpoint
        tx_params["gasPrice"] = web3.eth.gas_price

    try:
        transaction = function.build_transaction(tx_params)
        signed = account.sign_transaction(transaction)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = web3.eth.send_raw_transaction(raw)
    except Exception as exc:  # noqa: BLE001
        raise TransactionError(
            f"The transaction could not be broadcast: {exc}",
            remedy="Check the wallet balance and that the RPC endpoint accepts transactions.",
        ) from exc

    try:
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    except Exception as exc:  # noqa: BLE001
        raise TransactionError(
            f"Transaction {tx_hash.hex()} was broadcast but not confirmed within 300s: {exc}",
            remedy=f"Check {EXPLORER}/tx/{_hex(tx_hash)} - it may still confirm.",
        ) from exc

    if receipt.status != 1:
        raise TransactionError(
            f"Transaction {_hex(tx_hash)} was mined but reverted.",
            remedy=f"Inspect it at {EXPLORER}/tx/{_hex(tx_hash)}",
        )

    block = web3.eth.get_block(receipt.blockNumber)
    tx_hex = _hex(tx_hash)
    return RegistrationReceipt(
        transaction_hash=tx_hex,
        block_number=int(receipt.blockNumber),
        block_time_unix=int(block["timestamp"]),
        submitter=account.address,
        chain_id=int(web3.eth.chain_id),
        network="ethereum-sepolia",
        contract_address=contract.address,
        record_sha256=normalize_hex(record_hash_hex),
        url_keccak256=_hex(url_digest),
        gas_used=int(receipt.gasUsed),
        explorer_tx_url=f"{EXPLORER}/tx/{tx_hex}",
    )


def fetch(
    record_hash_hex: str,
    rpc_url: str | None = None,
    contract_address: str | None = None,
) -> dict[str, Any]:
    """Read an anchored record. Requires no private key."""
    if not is_sha256_hex(record_hash_hex):
        raise ContractError(
            f"record_sha256 is not a valid SHA-256 digest: {record_hash_hex!r}",
            remedy="Pass the record_sha256 value from the evidence record.",
        )
    web3 = get_client(rpc_url)
    contract = get_contract(web3, contract_address)
    record_bytes = _to_bytes32(record_hash_hex)

    if not contract.functions.exists(record_bytes).call():
        raise RecordNotFoundError(
            f"No on-chain record exists for hash {normalize_hex(record_hash_hex)}.",
            remedy=(
                "Either the evidence was never anchored, it was anchored by a different "
                "contract/network, or the local record was modified after anchoring "
                "(which changes its hash). Check CONTRACT_ADDRESS and SEPOLIA_RPC_URL."
            ),
        )

    result = contract.functions.getVerification(record_bytes).call()
    return {
        "record_sha256": _hex(result[0]),
        "url_keccak256": _hex(result[1]),
        "block_time_unix": int(result[2]),
        "submitter": result[3],
        "contract_address": contract.address,
        "chain_id": int(web3.eth.chain_id),
        "network": "ethereum-sepolia",
    }


def verify_on_chain(
    record_hash_hex: str,
    matched_url: str,
    rpc_url: str | None = None,
    contract_address: str | None = None,
) -> dict[str, Any]:
    """Single eth_call check that a record is anchored against a specific URL."""
    web3 = get_client(rpc_url)
    contract = get_contract(web3, contract_address)
    anchored, url_matches, block_time, submitter = contract.functions.verify(
        _to_bytes32(record_hash_hex), _to_bytes32(url_hash(matched_url))
    ).call()
    return {
        "anchored": bool(anchored),
        "url_matches": bool(url_matches),
        "block_time_unix": int(block_time),
        "submitter": submitter,
        "contract_address": contract.address,
        "chain_id": int(web3.eth.chain_id),
    }
