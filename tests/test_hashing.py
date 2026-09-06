"""Canonical JSON and hashing determinism - the foundation of the integrity model."""

from __future__ import annotations

import hashlib

import pytest

from src.core.hashing import (
    canonical_bytes,
    canonical_json,
    is_sha256_hex,
    normalize_hex,
    sha256_file,
    sha256_hex,
    sha256_json,
)


class TestCanonicalJson:
    def test_key_order_does_not_affect_output(self):
        a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
        b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
        assert canonical_json(a) == canonical_json(b)

    def test_output_has_no_insignificant_whitespace(self):
        assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'

    def test_unicode_is_preserved_not_escaped(self):
        assert canonical_json({"name": "José"}) == '{"name":"José"}'

    def test_nan_and_infinity_are_rejected(self):
        with pytest.raises(ValueError):
            canonical_json({"x": float("nan")})
        with pytest.raises(ValueError):
            canonical_json({"x": float("inf")})

    def test_list_order_is_significant(self):
        assert canonical_json([1, 2]) != canonical_json([2, 1])

    def test_canonical_bytes_is_utf8_of_canonical_json(self):
        data = {"k": "ü"}
        assert canonical_bytes(data) == canonical_json(data).encode("utf-8")


class TestSha256:
    def test_sha256_json_is_stable_across_key_order(self):
        assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})

    def test_changing_any_value_changes_the_digest(self):
        base = {"a": 1, "b": {"c": "x"}}
        changed = {"a": 1, "b": {"c": "y"}}
        assert sha256_json(base) != sha256_json(changed)

    def test_adding_a_field_changes_the_digest(self):
        assert sha256_json({"a": 1}) != sha256_json({"a": 1, "b": 2})

    def test_digest_matches_the_reference_implementation(self):
        data = {"a": 1}
        expected = hashlib.sha256(b'{"a":1}').hexdigest()
        assert sha256_json(data) == expected

    def test_digest_is_lowercase_hex_of_length_64(self):
        digest = sha256_hex(b"abc")
        assert len(digest) == 64
        assert digest == digest.lower()

    def test_sha256_file_matches_in_memory_hash(self, tmp_path, image_bytes):
        path = tmp_path / "x.png"
        path.write_bytes(image_bytes)
        assert sha256_file(path) == sha256_hex(image_bytes)

    def test_one_flipped_byte_changes_the_file_hash(self, tmp_path, image_bytes):
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(image_bytes)
        mutated = bytearray(image_bytes)
        mutated[-1] ^= 0x01
        b.write_bytes(bytes(mutated))
        assert sha256_file(a) != sha256_file(b)


class TestHexHelpers:
    @pytest.mark.parametrize("value", ["0x" + "a" * 64, "A" * 64, " " + "b" * 64 + " "])
    def test_valid_digests_are_accepted(self, value):
        assert is_sha256_hex(value)

    @pytest.mark.parametrize("value", ["", "abc", "z" * 64, "a" * 63, None, 123])
    def test_invalid_digests_are_rejected(self, value):
        assert not is_sha256_hex(value)

    def test_normalize_strips_prefix_and_lowercases(self):
        assert normalize_hex("0xABCDEF") == "abcdef"

    def test_normalize_rejects_non_strings(self):
        with pytest.raises(TypeError):
            normalize_hex(b"abc")
