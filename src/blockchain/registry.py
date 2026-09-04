import os

from web3 import Web3

ABI = [
    {"inputs": [{"internalType": "bytes32", "name": "recordHash", "type": "bytes32"}, {"internalType": "string", "name": "matchedUrl", "type": "string"}], "name": "registerVerification", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "bytes32", "name": "recordHash", "type": "bytes32"}], "name": "getVerification", "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}, {"internalType": "string", "name": "", "type": "string"}, {"internalType": "uint256", "name": "", "type": "uint256"}, {"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]


def get_client() -> Web3:
    rpc = os.getenv("SEPOLIA_RPC_URL")
    if not rpc:
        raise RuntimeError("SEPOLIA_RPC_URL is not configured.")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to the configured Sepolia RPC endpoint.")
    return w3


def register(record_hash_hex: str, matched_url: str) -> dict:
    private_key = os.getenv("PRIVATE_KEY")
    contract_address = os.getenv("CONTRACT_ADDRESS")
    if not private_key or not contract_address:
        raise RuntimeError("PRIVATE_KEY and CONTRACT_ADDRESS are required.")

    w3 = get_client()
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(address=w3.to_checksum_address(contract_address), abi=ABI)
    record_bytes = bytes.fromhex(record_hash_hex)
    nonce = w3.eth.get_transaction_count(account.address, "pending")

    tx_params = {"from": account.address, "nonce": nonce, "chainId": w3.eth.chain_id}
    try:
        tx_params["gas"] = contract.functions.registerVerification(record_bytes, matched_url).estimate_gas({"from": account.address})
    except Exception as exc:
        raise RuntimeError(f"Could not estimate contract gas: {exc}") from exc

    try:
        tx_params["maxPriorityFeePerGas"] = w3.to_wei(1, "gwei")
        tx_params["maxFeePerGas"] = max(w3.to_wei(2, "gwei"), int(w3.eth.gas_price * 2))
    except Exception:
        tx_params["gasPrice"] = w3.eth.gas_price

    tx = contract.functions.registerVerification(record_bytes, matched_url).build_transaction(tx_params)
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError("Blockchain transaction failed.")

    return {"transaction_hash": tx_hash.hex(), "block_number": receipt.blockNumber, "submitter": account.address, "chain_id": w3.eth.chain_id}


def fetch(record_hash_hex: str) -> dict:
    contract_address = os.getenv("CONTRACT_ADDRESS")
    if not contract_address:
        raise RuntimeError("CONTRACT_ADDRESS is not configured.")
    w3 = get_client()
    contract = w3.eth.contract(address=w3.to_checksum_address(contract_address), abi=ABI)
    result = contract.functions.getVerification(bytes.fromhex(record_hash_hex)).call()
    return {"record_hash": result[0].hex(), "matched_url": result[1], "timestamp": int(result[2]), "submitter": result[3]}
