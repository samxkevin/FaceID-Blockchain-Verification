#!/usr/bin/env python3
"""Compile and deploy FaceVerificationRegistry.sol to Ethereum Sepolia.

  pip install py-solc-x
  python scripts/deploy_contract.py

The solc binary is downloaded and cached automatically on first run. The printed
CONTRACT_ADDRESS goes into your .env file.

No Hardhat, Foundry or Node.js toolchain is required - one Python dependency
keeps the whole project installable with a single pip command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from src.core import console  # noqa: E402
from src.core.errors import ChainConfigError, PipelineError, TransactionError  # noqa: E402

SOLC_VERSION = "0.8.24"
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "FaceVerificationRegistry.sol"
ARTIFACT_PATH = Path(__file__).resolve().parents[1] / "output" / "FaceVerificationRegistry.abi.json"


def compile_contract() -> tuple[list, str]:
    """Compile the registry and return (abi, bytecode)."""
    try:
        import solcx
    except ImportError as exc:
        raise ChainConfigError(
            "The 'py-solc-x' package is not installed.",
            remedy="Install it with: pip install py-solc-x",
        ) from exc

    try:
        installed = [str(v) for v in solcx.get_installed_solc_versions()]
    except Exception:  # noqa: BLE001 - treat an unreadable cache as "nothing installed"
        installed = []

    if SOLC_VERSION not in installed:
        console.note(f"Downloading solc {SOLC_VERSION} (one time only) ...")
        try:
            solcx.install_solc(SOLC_VERSION)
        except Exception as exc:  # noqa: BLE001 - network/TLS/proxy failures
            raise ChainConfigError(
                f"Could not download the Solidity compiler {SOLC_VERSION}: "
                f"{type(exc).__name__}: {exc}",
                remedy=(
                    "The compiler is fetched from binaries.soliditylang.org, which your "
                    "network appears to block.\n"
                    "Options:\n"
                    "  1. Install solc yourself, then point py-solc-x at it:\n"
                    "       (Linux)  sudo add-apt-repository ppa:ethereum/ethereum \\\n"
                    "                && sudo apt-get update && sudo apt-get install solc\n"
                    "       (macOS)  brew install solidity\n"
                    "     then:  export SOLCX_BINARY_PATH=$(dirname $(which solc))\n"
                    "  2. Compile and deploy once in Remix (https://remix.ethereum.org),\n"
                    "     paste in contracts/FaceVerificationRegistry.sol, deploy to\n"
                    "     Sepolia via Injected Provider, and put the resulting address in\n"
                    "     .env as CONTRACT_ADDRESS. The pipeline and verifier then work\n"
                    "     normally - only this deploy script needs solc."
                ),
            ) from exc

    compiled = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {CONTRACT_PATH.name: {"content": CONTRACT_PATH.read_text(encoding="utf-8")}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        },
        solc_version=SOLC_VERSION,
    )
    contract = compiled["contracts"][CONTRACT_PATH.name]["FaceVerificationRegistry"]
    return contract["abi"], contract["evm"]["bytecode"]["object"]


def run(args) -> int:
    load_dotenv()
    console.banner("DEPLOY FaceVerificationRegistry", "Ethereum Sepolia testnet")

    console.step(1, 3, "Compiling contract")
    abi, bytecode = compile_contract()
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(abi, indent=2), encoding="utf-8")
    console.field("Source:", CONTRACT_PATH.name)
    console.field("Compiler:", f"solc {SOLC_VERSION} (optimizer on, 200 runs)")
    console.field("Bytecode size:", f"{len(bytecode) // 2} bytes")
    console.field("ABI written:", ARTIFACT_PATH)

    if args.compile_only:
        console.status("COMPILED (deployment skipped)", good=True)
        return 0

    console.step(2, 3, "Connecting to Sepolia")
    from src.blockchain.registry import EXPLORER, _load_account, get_client

    web3 = get_client(args.rpc_url)
    account = _load_account(web3, args.private_key)
    balance = web3.eth.get_balance(account.address)
    console.field("Chain id:", web3.eth.chain_id)
    console.field("Deployer:", account.address)
    console.field("Balance:", f"{web3.from_wei(balance, 'ether')} ETH")
    if balance == 0:
        raise TransactionError(
            "The deployer wallet has no Sepolia ETH.",
            remedy="Fund it from a faucet such as https://sepoliafaucet.com",
        )

    console.step(3, 3, "Deploying")
    contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    constructor = contract.constructor()
    gas = constructor.estimate_gas({"from": account.address})
    base_fee = web3.eth.get_block("latest").get("baseFeePerGas") or web3.eth.gas_price
    priority = web3.to_wei(1.5, "gwei")
    transaction = constructor.build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(account.address, "pending"),
            "chainId": web3.eth.chain_id,
            "gas": int(gas * 125 // 100),
            "maxPriorityFeePerGas": priority,
            "maxFeePerGas": int(base_fee * 2) + priority,
        }
    )
    signed = account.sign_transaction(transaction)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = web3.eth.send_raw_transaction(raw)
    console.field("Transaction:", tx_hash.hex())
    console.note("Waiting for confirmation ...")
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt.status != 1:
        raise TransactionError(
            "The deployment transaction reverted.",
            remedy=f"Inspect it at {EXPLORER}/tx/{tx_hash.hex()}",
        )

    address = receipt.contractAddress
    console.field("Contract address:", console.paint(address, "bold"))
    console.field("Block:", receipt.blockNumber)
    console.field("Gas used:", receipt.gasUsed)
    console.field("Explorer:", f"{EXPLORER}/address/{address}")
    console.status("CONTRACT DEPLOYED", good=True)
    print("Add this line to your .env file:\n")
    print(f"  CONTRACT_ADDRESS={address}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy FaceVerificationRegistry to Sepolia.")
    parser.add_argument("--compile-only", action="store_true", help="Compile without deploying.")
    parser.add_argument("--rpc-url", help="Override SEPOLIA_RPC_URL.")
    parser.add_argument("--private-key", help="Override PRIVATE_KEY (prefer .env).")
    args = parser.parse_args()
    try:
        return run(args)
    except PipelineError as exc:
        console.error_block(exc, code=exc.code, remedy=exc.remedy)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
