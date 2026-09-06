"""Integration tests for FaceVerificationRegistry.sol on a real in-process EVM.

These compile the actual Solidity source with solc and execute it on py-evm via
eth-tester. No testnet, no faucet, no private key of yours, no network.

They are skipped automatically when solc or eth-tester is unavailable (for
example in an offline CI sandbox), so the suite never fails for environmental
reasons. Install them with:

    pip install py-solc-x "web3[tester]"
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "FaceVerificationRegistry.sol"
SOLC_VERSION = "0.8.24"


def _compile():
    solcx = pytest.importorskip("solcx", reason="py-solc-x not installed")
    try:
        installed = [str(v) for v in solcx.get_installed_solc_versions()]
        if SOLC_VERSION not in installed:
            solcx.install_solc(SOLC_VERSION)
    except Exception as exc:  # noqa: BLE001 - offline sandbox cannot fetch solc
        pytest.skip(f"solc {SOLC_VERSION} unavailable: {type(exc).__name__}")

    compiled = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {CONTRACT.name: {"content": CONTRACT.read_text(encoding="utf-8")}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        },
        solc_version=SOLC_VERSION,
    )
    contract = compiled["contracts"][CONTRACT.name]["FaceVerificationRegistry"]
    return contract["abi"], contract["evm"]["bytecode"]["object"]


def revert_errors():
    """Exception types a revert can surface as.

    Solidity *custom errors* (which this contract uses) are reported by
    eth-tester as `TransactionFailed`, while `require`-style string reverts and
    eth_call reverts surface as web3's `ContractLogicError`. The exact type also
    varies across web3/eth-tester versions, so tests accept either.
    """
    from web3.exceptions import ContractLogicError

    types: tuple[type[BaseException], ...] = (ContractLogicError,)
    try:
        from eth_tester.exceptions import TransactionFailed

        types += (TransactionFailed,)
    except ImportError:  # pragma: no cover - eth-tester always present here
        pass
    return types


@pytest.fixture(scope="module")
def deployed():
    pytest.importorskip("eth_tester", reason="eth-tester not installed")
    from web3 import EthereumTesterProvider, Web3

    abi, bytecode = _compile()
    web3 = Web3(EthereumTesterProvider())
    web3.eth.default_account = web3.eth.accounts[0]
    factory = web3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = factory.constructor().transact()
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    return web3, web3.eth.contract(address=receipt.contractAddress, abi=abi)


RECORD_A = bytes.fromhex("a" * 64)
RECORD_B = bytes.fromhex("b" * 64)
URL_A = bytes.fromhex("1" * 64)
URL_B = bytes.fromhex("2" * 64)
ZERO = b"\x00" * 32


class TestRegistration:
    def test_a_record_can_be_registered_and_read_back(self, deployed):
        web3, contract = deployed
        contract.functions.registerVerification(RECORD_A, URL_A).transact()
        record_hash, url_hash, block_time, submitter = contract.functions.getVerification(
            RECORD_A
        ).call()
        assert record_hash == RECORD_A
        assert url_hash == URL_A
        assert block_time > 0
        assert submitter == web3.eth.accounts[0]

    def test_exists_reflects_registration(self, deployed):
        _, contract = deployed
        assert contract.functions.exists(RECORD_A).call() is True
        assert contract.functions.exists(RECORD_B).call() is False

    def test_total_records_increments(self, deployed):
        _, contract = deployed
        before = contract.functions.totalRecords().call()
        contract.functions.registerVerification(RECORD_B, URL_B).transact()
        assert contract.functions.totalRecords().call() == before + 1

    def test_an_event_is_emitted_with_the_indexed_hashes(self, deployed):
        web3, contract = deployed
        record = bytes.fromhex("c" * 64)
        tx_hash = contract.functions.registerVerification(record, URL_A).transact()
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        logs = contract.events.VerificationRegistered().process_receipt(receipt)
        assert len(logs) == 1
        assert logs[0]["args"]["recordHash"] == record
        assert logs[0]["args"]["urlHash"] == URL_A


class TestImmutability:
    def test_duplicate_registration_is_rejected(self, deployed):
        """The first anchoring is authoritative and can never be overwritten."""
        _, contract = deployed
        with pytest.raises(revert_errors()):
            contract.functions.registerVerification(RECORD_A, URL_B).transact()

    def test_the_original_url_commitment_survives_a_duplicate_attempt(self, deployed):
        _, contract = deployed
        assert contract.functions.getVerification(RECORD_A).call()[1] == URL_A

    def test_there_is_no_update_or_delete_function(self, deployed):
        _, contract = deployed
        names = {item["name"] for item in contract.abi if item.get("type") == "function"}
        assert not names & {"update", "remove", "delete", "setOwner", "upgrade", "destroy"}


class TestInputValidation:
    def test_a_zero_record_hash_is_rejected(self, deployed):
        _, contract = deployed
        with pytest.raises(revert_errors()):
            contract.functions.registerVerification(ZERO, URL_A).transact()

    def test_a_zero_url_hash_is_rejected(self, deployed):
        _, contract = deployed
        with pytest.raises(revert_errors()):
            contract.functions.registerVerification(RECORD_B, ZERO).transact()

    def test_reading_an_unknown_record_reverts_rather_than_returning_zeros(self, deployed):
        _, contract = deployed
        with pytest.raises(revert_errors()):
            contract.functions.getVerification(bytes.fromhex("f" * 64)).call()


class TestVerifyHelper:
    def test_verify_confirms_a_correct_record_and_url(self, deployed):
        _, contract = deployed
        anchored, url_matches, block_time, _ = contract.functions.verify(RECORD_A, URL_A).call()
        assert anchored and url_matches and block_time > 0

    def test_verify_detects_a_substituted_url(self, deployed):
        _, contract = deployed
        anchored, url_matches, _, _ = contract.functions.verify(RECORD_A, URL_B).call()
        assert anchored is True
        assert url_matches is False

    def test_verify_reports_an_unanchored_record_without_reverting(self, deployed):
        _, contract = deployed
        anchored, url_matches, block_time, _ = contract.functions.verify(
            bytes.fromhex("e" * 64), URL_A
        ).call()
        assert anchored is False and url_matches is False and block_time == 0


class TestEndToEndIntegrity:
    def test_a_tampered_record_hash_is_not_found_on_chain(self, deployed):
        """The core tamper-evidence property, proven against a real EVM."""
        from src.core.hashing import sha256_json

        _, contract = deployed
        evidence = {"matched_url": "https://www.instagram.com/p/real/", "faces": 1}
        genuine = bytes.fromhex(sha256_json(evidence))
        contract.functions.registerVerification(genuine, URL_A).transact()
        assert contract.functions.exists(genuine).call() is True

        tampered = dict(evidence, matched_url="https://www.instagram.com/p/forged/")
        forged = bytes.fromhex(sha256_json(tampered))
        assert forged != genuine
        assert contract.functions.exists(forged).call() is False
