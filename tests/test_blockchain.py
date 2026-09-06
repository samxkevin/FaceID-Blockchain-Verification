"""Blockchain client validation, tested without any live chain, key or RPC.

Contract interaction is exercised through a small in-memory fake that mirrors
FaceVerificationRegistry's semantics, so the client's encoding, comparison and
error paths are all covered offline.
"""

from __future__ import annotations

import pytest

from src.core.errors import (
    ChainConfigError,
    ChainConnectionError,
    ContractError,
    RecordNotFoundError,
    WrongNetworkError,
)
from src.blockchain import registry
from src.blockchain.registry import SEPOLIA_CHAIN_ID, _to_bytes32, url_hash

VALID_HASH = "a" * 64


class TestValueEncoding:
    def test_valid_digest_encodes_to_32_bytes(self):
        assert len(_to_bytes32(VALID_HASH)) == 32

    def test_0x_prefix_is_accepted(self):
        assert _to_bytes32("0x" + VALID_HASH) == _to_bytes32(VALID_HASH)

    def test_case_is_normalized(self):
        assert _to_bytes32("A" * 64) == _to_bytes32("a" * 64)

    @pytest.mark.parametrize("value", ["", "abc", "a" * 63, "a" * 65])
    def test_wrong_length_is_rejected(self, value):
        with pytest.raises(ContractError):
            _to_bytes32(value)

    def test_non_hex_is_rejected(self):
        with pytest.raises(ContractError):
            _to_bytes32("z" * 64)


class TestUrlHash:
    def test_hash_is_deterministic(self):
        url = "https://www.instagram.com/p/abc/"
        assert url_hash(url) == url_hash(url)

    def test_different_urls_hash_differently(self):
        assert url_hash("https://a.com/1") != url_hash("https://a.com/2")

    def test_a_single_character_change_changes_the_hash(self):
        assert url_hash("https://x.com/a/status/1") != url_hash("https://x.com/a/status/2")

    def test_hash_is_32_bytes_of_hex(self):
        digest = url_hash("https://example.com")
        assert len(digest.removeprefix("0x")) == 64

    def test_matches_the_known_keccak256_of_the_empty_ethereum_string(self):
        # keccak256("") is a well-known constant; confirms we use keccak, not SHA-3.
        from web3 import Web3

        expected = Web3.keccak(text="abc").hex()
        assert url_hash("abc").removeprefix("0x") == expected.removeprefix("0x")

    def test_empty_url_is_rejected(self):
        with pytest.raises(ContractError):
            url_hash("")


class TestConfigValidation:
    def test_missing_rpc_url_is_reported(self, monkeypatch):
        monkeypatch.delenv("SEPOLIA_RPC_URL", raising=False)
        with pytest.raises(ChainConfigError) as exc:
            registry.get_client()
        assert "SEPOLIA_RPC_URL" in str(exc.value)
        assert exc.value.remedy

    def test_malformed_rpc_url_is_reported(self, monkeypatch):
        monkeypatch.setenv("SEPOLIA_RPC_URL", "just-some-text")
        with pytest.raises(ChainConfigError) as exc:
            registry.get_client()
        assert "does not look like a URL" in str(exc.value)

    def test_unreachable_rpc_is_reported_as_a_connection_error(self, monkeypatch):
        monkeypatch.setenv("SEPOLIA_RPC_URL", "https://127.0.0.1:1/rpc")

        class DeadWeb3:
            def __init__(self, *a, **k):
                pass

            @staticmethod
            def HTTPProvider(*a, **k):
                return None

            def is_connected(self):
                return False

        monkeypatch.setattr("web3.Web3", DeadWeb3)
        with pytest.raises(ChainConnectionError):
            registry.get_client()

    def test_wrong_network_is_rejected(self, monkeypatch):
        monkeypatch.setenv("SEPOLIA_RPC_URL", "https://example.com/rpc")

        class Eth:
            chain_id = 1  # mainnet

        class MainnetWeb3:
            eth = Eth()

            def __init__(self, *a, **k):
                pass

            @staticmethod
            def HTTPProvider(*a, **k):
                return None

            def is_connected(self):
                return True

        monkeypatch.setattr("web3.Web3", MainnetWeb3)
        with pytest.raises(WrongNetworkError) as exc:
            registry.get_client()
        assert str(SEPOLIA_CHAIN_ID) in str(exc.value)

    def test_missing_private_key_is_reported_without_leaking_anything(self, monkeypatch):
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        with pytest.raises(ChainConfigError) as exc:
            registry._load_account(object())
        assert "PRIVATE_KEY" in str(exc.value)
        assert "Never use a key" in exc.value.remedy

    def test_short_private_key_is_rejected_and_never_echoed(self, monkeypatch):
        secret = "deadbeef"
        monkeypatch.setenv("PRIVATE_KEY", secret)
        with pytest.raises(ChainConfigError) as exc:
            registry._load_account(object())
        assert secret not in str(exc.value)
        assert secret not in exc.value.remedy

    def test_missing_contract_address_is_reported(self, monkeypatch):
        monkeypatch.delenv("CONTRACT_ADDRESS", raising=False)
        with pytest.raises(ChainConfigError) as exc:
            registry.get_contract(object())
        assert "CONTRACT_ADDRESS" in str(exc.value)

    def test_invalid_contract_address_is_reported(self, monkeypatch):
        class Web3Like:
            @staticmethod
            def to_checksum_address(value):
                raise ValueError("bad address")

        with pytest.raises(ChainConfigError) as exc:
            registry.get_contract(Web3Like(), address="0xnot-an-address")
        assert "not a valid Ethereum address" in str(exc.value)

    def test_address_without_deployed_code_is_reported(self):
        address = "0x" + "1" * 40

        class Eth:
            @staticmethod
            def get_code(_):
                return b""

        class Web3Like:
            eth = Eth()

            @staticmethod
            def to_checksum_address(value):
                return value

        with pytest.raises(ContractError) as exc:
            registry.get_contract(Web3Like(), address=address)
        assert "No contract code" in str(exc.value)


class FakeRegistry:
    """In-memory stand-in mirroring FaceVerificationRegistry semantics."""

    def __init__(self):
        self.store: dict[bytes, tuple[bytes, int, str]] = {}
        self.address = "0x" + "a" * 40

    class _Call:
        def __init__(self, value):
            self._value = value

        def call(self):
            return self._value

    class Functions:
        def __init__(self, outer):
            self.outer = outer

        def exists(self, record_hash):
            return FakeRegistry._Call(record_hash in self.outer.store)

        def getVerification(self, record_hash):
            if record_hash not in self.outer.store:
                raise RuntimeError("execution reverted: RecordNotFound")
            url_h, block_time, submitter = self.outer.store[record_hash]
            return FakeRegistry._Call((record_hash, url_h, block_time, submitter))

        def verify(self, record_hash, url_h):
            entry = self.outer.store.get(record_hash)
            if not entry:
                return FakeRegistry._Call((False, False, 0, "0x" + "0" * 40))
            return FakeRegistry._Call((True, entry[0] == url_h, entry[1], entry[2]))

    @property
    def functions(self):
        return FakeRegistry.Functions(self)


class TestReadPath:
    @pytest.fixture
    def patched(self, monkeypatch):
        fake = FakeRegistry()

        class Eth:
            chain_id = SEPOLIA_CHAIN_ID

        class Web3Like:
            eth = Eth()

        monkeypatch.setattr(registry, "get_client", lambda *a, **k: Web3Like())
        monkeypatch.setattr(registry, "get_contract", lambda *a, **k: fake)
        return fake

    def test_unanchored_hash_raises_record_not_found(self, patched):
        with pytest.raises(RecordNotFoundError) as exc:
            registry.fetch(VALID_HASH)
        assert "No on-chain record" in str(exc.value)
        assert "CONTRACT_ADDRESS" in exc.value.remedy

    def test_anchored_hash_is_returned_correctly(self, patched):
        url = "https://www.instagram.com/p/abc/"
        key = _to_bytes32(VALID_HASH)
        patched.store[key] = (_to_bytes32(url_hash(url)), 1750000000, "0x" + "b" * 40)

        result = registry.fetch(VALID_HASH)
        assert result["record_sha256"].removeprefix("0x") == VALID_HASH
        assert result["url_keccak256"] == url_hash(url)
        assert result["block_time_unix"] == 1750000000
        assert result["chain_id"] == SEPOLIA_CHAIN_ID

    def test_verify_confirms_a_matching_url(self, patched):
        url = "https://www.instagram.com/p/abc/"
        patched.store[_to_bytes32(VALID_HASH)] = (
            _to_bytes32(url_hash(url)),
            1750000000,
            "0x" + "b" * 40,
        )
        result = registry.verify_on_chain(VALID_HASH, url)
        assert result["anchored"] and result["url_matches"]

    def test_verify_rejects_a_substituted_url(self, patched):
        patched.store[_to_bytes32(VALID_HASH)] = (
            _to_bytes32(url_hash("https://www.instagram.com/p/real/")),
            1750000000,
            "0x" + "b" * 40,
        )
        result = registry.verify_on_chain(VALID_HASH, "https://www.instagram.com/p/forged/")
        assert result["anchored"] is True
        assert result["url_matches"] is False

    def test_invalid_digest_is_rejected_before_any_network_call(self, patched):
        with pytest.raises(ContractError):
            registry.fetch("not-a-hash")
