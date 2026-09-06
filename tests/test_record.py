"""Evidence record construction, determinism and tamper detection."""

from __future__ import annotations

import json

import pytest

from src.core.errors import EvidenceError, TamperDetectedError
from src.verification.record import (
    SCHEMA_VERSION,
    assert_local_integrity,
    attach_blockchain_anchor,
    build_evidence,
    build_record,
    check_local_integrity,
    compute_record_hash,
    load_record,
    save_record,
)


class TestConstruction:
    def test_record_contains_the_expected_sections(self, sample_evidence):
        record = build_record(sample_evidence)
        assert set(record) == {"evidence", "integrity", "blockchain"}
        assert record["integrity"]["hash_algorithm"] == "SHA-256"
        assert record["integrity"]["hashed_subtree"] == "evidence"
        assert record["blockchain"] is None

    def test_evidence_carries_the_schema_version(self, sample_evidence):
        assert sample_evidence["schema_version"] == SCHEMA_VERSION

    def test_all_required_evidence_fields_are_present(self, sample_evidence):
        assert sample_evidence["input_image"]["sha256"]
        assert sample_evidence["input_image"]["byte_size"] > 0
        assert sample_evidence["face_analysis"]["face_count"] == 1
        assert sample_evidence["face_analysis"]["encoding_dimensions"] == 128
        assert sample_evidence["reverse_image_search"]["provider"]
        assert sample_evidence["match"]["matched_url"]
        assert sample_evidence["match"]["evidence_tier"]
        assert sample_evidence["image_transport"]["mode"]
        assert sample_evidence["created_at_utc"]

    def test_claim_scope_is_embedded_in_every_record(self, sample_evidence):
        scope = sample_evidence["claim_scope"]
        assert scope["proves"] and scope["does_not_prove"]
        joined = " ".join(scope["does_not_prove"]).lower()
        assert "identity" in joined
        assert "owns or controls" in joined

    def test_no_biometric_encoding_is_stored(self, sample_evidence):
        blob = json.dumps(sample_evidence)
        # Only a one-way commitment, never the vector itself.
        assert "encoding_commitment_sha256" in blob
        assert "encoding_vector" not in blob
        assert "embedding" not in blob

    def test_absolute_paths_are_not_leaked(self, sample_evidence, image_file):
        blob = json.dumps(sample_evidence)
        assert str(image_file.parent) not in blob
        assert sample_evidence["input_image"]["file_name"] == "input.png"

    def test_floats_are_rejected_because_they_are_not_portable(self, image_file):
        with pytest.raises(EvidenceError) as exc:
            build_evidence(
                image_path=image_file,
                image_fingerprint={"score": 0.5},
                face_metadata={},
                search_summary={},
                selection={"selected": {}},
                transport={},
            )
        assert "Float value" in str(exc.value)


class TestDeterminism:
    def test_identical_evidence_hashes_identically(self, sample_evidence):
        import copy

        assert compute_record_hash(sample_evidence) == compute_record_hash(copy.deepcopy(sample_evidence))

    def test_hash_is_independent_of_key_insertion_order(self, sample_evidence):
        reordered = dict(reversed(list(sample_evidence.items())))
        assert compute_record_hash(reordered) == compute_record_hash(sample_evidence)

    @pytest.mark.parametrize(
        "path,value",
        [
            (["match", "matched_url"], "https://evil.example/"),
            (["match", "evidence_tier"], "VERIFIED_EXACT_FAKE"),
            (["input_image", "sha256"], "0" * 64),
            (["face_analysis", "face_count"], 7),
            (["face_analysis", "encoding_dimensions"], 512),
            (["reverse_image_search", "provider"], "Some Other Provider"),
            (["created_at_utc"], "2030-01-01T00:00:00+00:00"),
            (["schema_version"], "9.9"),
            (["image_transport", "mode"], "direct"),
        ],
    )
    def test_changing_any_meaningful_field_changes_the_hash(self, sample_evidence, path, value):
        import copy

        before = compute_record_hash(sample_evidence)
        mutated = copy.deepcopy(sample_evidence)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert compute_record_hash(mutated) != before

    def test_adding_a_new_evidence_field_changes_the_hash(self, sample_evidence):
        import copy

        before = compute_record_hash(sample_evidence)
        mutated = copy.deepcopy(sample_evidence)
        mutated["injected"] = "value"
        assert compute_record_hash(mutated) != before


class TestTamperDetection:
    def test_an_untouched_record_verifies(self, sample_evidence):
        record = build_record(sample_evidence)
        report = check_local_integrity(record)
        assert report.matches
        assert report.status == "UNMODIFIED"

    def test_editing_the_url_is_detected(self, sample_evidence):
        record = build_record(sample_evidence)
        record["evidence"]["match"]["matched_url"] = "https://instagram.com/p/forged/"
        report = check_local_integrity(record)
        assert not report.matches
        assert report.status == "TAMPERED"
        with pytest.raises(TamperDetectedError):
            assert_local_integrity(record)

    def test_editing_the_image_hash_is_detected(self, sample_evidence):
        record = build_record(sample_evidence)
        record["evidence"]["input_image"]["sha256"] = "f" * 64
        assert not check_local_integrity(record).matches

    def test_deleting_a_field_is_detected(self, sample_evidence):
        record = build_record(sample_evidence)
        del record["evidence"]["face_analysis"]
        assert not check_local_integrity(record).matches

    def test_a_forger_who_also_rewrites_the_stored_hash_still_fails_on_chain(self, sample_evidence):
        """Self-consistent forgery passes locally but produces a hash that was never anchored."""
        record = build_record(sample_evidence)
        original_hash = record["integrity"]["record_sha256"]
        record["evidence"]["match"]["matched_url"] = "https://x.com/attacker/status/1"
        record["integrity"]["record_sha256"] = compute_record_hash(record["evidence"])
        assert check_local_integrity(record).matches  # locally consistent...
        assert record["integrity"]["record_sha256"] != original_hash  # ...but a different commitment

    def test_anchor_metadata_does_not_invalidate_the_hash(self, sample_evidence):
        record = build_record(sample_evidence)
        before = record["integrity"]["record_sha256"]
        attach_blockchain_anchor(record, {"transaction_hash": "0xabc", "block_number": 1})
        assert check_local_integrity(record).matches
        assert record["integrity"]["record_sha256"] == before


class TestPersistence:
    def test_round_trip_preserves_the_hash(self, sample_evidence, tmp_path):
        record = build_record(sample_evidence)
        path = save_record(record, tmp_path / "r.json")
        assert check_local_integrity(load_record(path)).matches

    def test_missing_file_gives_an_actionable_error(self, tmp_path):
        with pytest.raises(EvidenceError) as exc:
            load_record(tmp_path / "nope.json")
        assert "run_pipeline" in exc.value.remedy

    def test_invalid_json_is_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(EvidenceError) as exc:
            load_record(path)
        assert "not valid JSON" in str(exc.value)

    def test_missing_sections_are_rejected(self, tmp_path):
        path = tmp_path / "part.json"
        path.write_text(json.dumps({"evidence": {}}), encoding="utf-8")
        with pytest.raises(EvidenceError) as exc:
            load_record(path)
        assert "integrity" in str(exc.value)

    def test_invalid_stored_digest_is_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps({"evidence": {}, "integrity": {"record_sha256": "nope"}}), encoding="utf-8"
        )
        with pytest.raises(EvidenceError) as exc:
            load_record(path)
        assert "valid SHA-256" in str(exc.value)

    def test_saved_file_is_human_readable_json(self, sample_evidence, tmp_path):
        path = save_record(build_record(sample_evidence), tmp_path / "r.json")
        text = path.read_text(encoding="utf-8")
        assert text.startswith("{\n")
        assert text.endswith("\n")
