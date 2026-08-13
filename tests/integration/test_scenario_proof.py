from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from finreplay.scenarios import (
    OfficialEventLock,
    ScenarioInputLabels,
    scenario_catalog_summary,
    seal_official_event_lock,
    seal_scenario_proof,
    verify_scenario_catalog,
    verify_scenario_proof,
)

REPOSITORY = Path(__file__).resolve().parents[2]
PROOF_DIRECTORY = REPOSITORY / "verification/scenarios/proofs"
PROOF_PATH = PROOF_DIRECTORY / "svb-2023-boundary-v1.json"
PACWEST_PROOF_PATH = PROOF_DIRECTORY / "pacwest-2023-funding-boundary-v1.json"
WESTERN_ALLIANCE_PROOF_PATH = (
    PROOF_DIRECTORY / "western-alliance-2023-deposit-boundary-v1.json"
)


def proof_values() -> dict[str, Any]:
    values = cast(dict[str, Any], json.loads(PROOF_PATH.read_text()))
    values.pop("proof_sha256")
    return values


def write_sealed(tmp_path: Path, values: dict[str, Any], name: str = "proof.json") -> Path:
    proof = seal_scenario_proof(values)
    path = tmp_path / name
    path.write_text(json.dumps(proof.model_dump(mode="json"), sort_keys=True, indent=2) + "\n")
    return path


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def copy_scenario_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    directories = ["verification/replaypacks/svb-2023-seven-engine"]
    files = [
        "verification/live/latest-summary.json",
        "scenarios/svb-2023/input-lock.json",
        "scenarios/svb-2023/event-lock.json",
        "scripts/build_svb_replaypack.py",
        "scripts/verify_svb_replaypack.py",
        "verification/evidence/svb-seven-engine-rebuild.json",
    ]
    for relative in directories:
        shutil.copytree(REPOSITORY / relative, root / relative)
    for relative in files:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / relative, destination)
    return root


def reseal_rebuild_receipt(path: Path, *, semantic_changed: bool) -> None:
    values = json.loads(path.read_text())
    values.pop("receipt_sha256")
    if semantic_changed:
        semantic = dict(values)
        semantic.pop("runtime")
        semantic.pop("semantic_sha256")
        values["semantic_sha256"] = canonical_hash(semantic)
    values["receipt_sha256"] = canonical_hash(values)
    path.write_text(json.dumps(values, sort_keys=True, indent=2) + "\n")


def test_committed_svb_proof_and_deterministic_catalog_are_fully_verified() -> None:
    verified = verify_scenario_proof(PROOF_PATH, repository_root=REPOSITORY)
    assert verified.scenario_id == "svb-2023-boundary"
    assert verified.mode == "bounded_reconstruction"
    assert verified.distinct_input_records == 7

    pacwest = verify_scenario_proof(PACWEST_PROOF_PATH, repository_root=REPOSITORY)
    assert pacwest.scenario_id == "pacwest-2023-funding-boundary"
    assert pacwest.mode == "bounded_reconstruction"
    assert pacwest.distinct_input_records == 7

    western_alliance = verify_scenario_proof(
        WESTERN_ALLIANCE_PROOF_PATH,
        repository_root=REPOSITORY,
    )
    assert western_alliance.scenario_id == "western-alliance-2023-deposit-boundary"
    assert western_alliance.mode == "bounded_reconstruction"
    assert western_alliance.distinct_input_records == 7

    catalog = verify_scenario_catalog(PROOF_DIRECTORY, repository_root=REPOSITORY)
    assert len(catalog) == 3
    summary = scenario_catalog_summary(catalog, proof_directory=PROOF_DIRECTORY)
    committed = json.loads((REPOSITORY / "verification/scenarios/latest-summary.json").read_text())
    assert summary == committed

    event_lock = OfficialEventLock.model_validate_json(
        (REPOSITORY / "scenarios/svb-2023/event-lock.json").read_text()
    )
    assert len(event_lock.records) == 1
    assert event_lock.records[0].interval.available_at > event_lock.decision_time
    assert event_lock.records[0].record_id not in {
        record_id
        for lock in values_from_proof(PROOF_PATH)["input_locks"]
        for record_id in lock["record_ids"]
    }


def test_fabricated_baseline_and_json_pointer_fail_closed(tmp_path: Path) -> None:
    values = proof_values()
    values["naive_baselines"][0]["expected_value"] = 0.5
    path = write_sealed(tmp_path, values)
    with pytest.raises(ValueError, match="scenario expectation differs"):
        verify_scenario_proof(path, repository_root=REPOSITORY)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("scenario_id", "different-scenario", "identity does not match"),
        ("expected_pack_sha256", "0" * 64, "expected_pack_sha256"),
        ("expected_trace_id", "trace:" + "0" * 64, "expected_trace_id"),
        ("expected_input_manifest_sha256", "0" * 64, "input manifest"),
        ("pack_directory", "verification/replaypacks/missing", "path does not exist"),
        (
            "official_adapter_inventory",
            "verification/scenarios/proofs/svb-2023-boundary-v1.json",
            "adapters must be a list",
        ),
    ],
)
def test_proof_identity_hash_and_locator_mismatches_fail_closed(
    tmp_path: Path, field: str, value: Any, match: str
) -> None:
    values = proof_values()
    values[field] = value
    path = write_sealed(tmp_path, values)
    with pytest.raises(ValueError, match=match):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["deliberate_failure_modes"][0]["payload_pointer"] = "/missing/value"
    path = write_sealed(tmp_path, values, "missing-pointer.json")
    with pytest.raises(ValueError, match="JSON pointer does not resolve"):
        verify_scenario_proof(path, repository_root=REPOSITORY)


def test_file_hash_assertion_and_label_tamper_fail_closed(tmp_path: Path) -> None:
    values = proof_values()
    values["build_script"]["sha256"] = "0" * 64
    path = write_sealed(tmp_path, values)
    with pytest.raises(ValueError, match="file hash mismatch"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    first = values["input_labels"]["reported_record_ids"].pop(0)
    values["input_labels"]["observed_record_ids"] = [first]
    values["input_labels"]["absence_reasons"] = {}
    path = write_sealed(tmp_path, values, "bad-label.json")
    with pytest.raises(ValueError, match="observed label points"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["required_rebuild_assertions"].append("fabricated_assertion")
    values["required_rebuild_assertions"].sort()
    path = write_sealed(tmp_path, values, "bad-assertion.json")
    with pytest.raises(ValueError, match="required rebuild assertion"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["naive_baselines"][0]["artifact_id"] = "unknown.artifact"
    path = write_sealed(tmp_path, values, "unknown-artifact.json")
    with pytest.raises(ValueError, match="unknown artifact"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["input_labels"]["inferred_artifact_ids"] = ["svb.replaystudio.render"]
    path = write_sealed(tmp_path, values, "wrong-inference.json")
    with pytest.raises(ValueError, match="inferred label lacks"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["input_labels"]["bounded_artifact_ids"] = ["svb.replaystudio.render"]
    path = write_sealed(tmp_path, values, "wrong-bound.json")
    with pytest.raises(ValueError, match="bounded artifact kind"):
        verify_scenario_proof(path, repository_root=REPOSITORY)


def test_self_hash_unsafe_paths_and_label_absence_fail_before_counting(tmp_path: Path) -> None:
    values = proof_values()
    proof = seal_scenario_proof(values)
    serialized = json.dumps(proof.model_dump(mode="json"), sort_keys=True)
    path = tmp_path / "tampered.json"
    path.write_text(serialized.replace("internally reproduced", "self-reported"))
    with pytest.raises(ValueError, match="invalid scenario proof"):
        verify_scenario_proof(path, repository_root=REPOSITORY)

    values = proof_values()
    values["pack_directory"] = "../outside"
    with pytest.raises(ValidationError, match="safe repository-relative"):
        seal_scenario_proof(values)

    with pytest.raises(ValidationError, match="empty observed"):
        ScenarioInputLabels(
            reported_record_ids=("reported:1",),
            extracted_artifact_ids=("artifact:1",),
            inferred_artifact_ids=("artifact:2",),
            bounded_artifact_ids=("artifact:3",),
            simulated_artifact_ids=("artifact:4",),
        )

    values = proof_values()
    values["timing_record_ids"] = ["missing:record"]
    with pytest.raises(ValidationError, match="timing record IDs must be present"):
        seal_scenario_proof(values)

    values = proof_values()
    values["input_labels"]["reported_record_ids"].append("missing:record")
    values["input_labels"]["reported_record_ids"].sort()
    with pytest.raises(ValidationError, match="labelled source record IDs"):
        seal_scenario_proof(values)

    values = proof_values()
    first = values["input_labels"]["reported_record_ids"][0]
    values["input_labels"]["observed_record_ids"] = [first]
    values["input_labels"]["absence_reasons"] = {}
    with pytest.raises(ValidationError, match="both observed and reported"):
        seal_scenario_proof(values)

    with pytest.raises(ValidationError, match="contradictory"):
        ScenarioInputLabels(
            observed_record_ids=("observed:1",),
            absence_reasons={
                "observed": "This reason contradicts the populated observed group.",
                "reported": "No reported facts are used.",
                "extracted": "No extracted inputs are used.",
                "inferred": "No inferred inputs are used.",
                "bounded": "No bounded inputs are used.",
                "simulated": "No simulated inputs are used.",
            },
        )

    values = proof_values()
    values["event_locks"][0]["record_ids"] = [values["input_locks"][0]["record_ids"][0]]
    with pytest.raises(ValidationError, match="disjoint"):
        seal_scenario_proof(values)


def test_rebuild_receipt_and_input_lock_are_cryptographically_bound(tmp_path: Path) -> None:
    root = copy_scenario_repository(tmp_path)
    values = proof_values()
    rebuild_path = root / values["rebuild_receipt"]["path"]
    rebuild = json.loads(rebuild_path.read_text())
    rebuild["runtime"]["runner_dirty"] = True
    rebuild_path.write_text(json.dumps(rebuild, sort_keys=True, indent=2) + "\n")
    reseal_rebuild_receipt(rebuild_path, semantic_changed=False)
    values["rebuild_receipt"]["sha256"] = hashlib.sha256(rebuild_path.read_bytes()).hexdigest()
    path = write_sealed(tmp_path, values, "dirty-rebuild.json")
    with pytest.raises(ValueError, match="clean worktree"):
        verify_scenario_proof(path, repository_root=root)

    root = copy_scenario_repository(tmp_path / "lock-case")
    values = proof_values()
    lock_path = root / values["input_locks"][0]["path"]
    lock = json.loads(lock_path.read_text())
    lock["records"][0]["payload"]["val"] += 1
    lock_path.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n")
    values["input_locks"][0]["sha256"] = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    path = write_sealed(tmp_path, values, "tampered-lock.json")
    with pytest.raises(ValueError, match="canonical content hash is invalid"):
        verify_scenario_proof(path, repository_root=root)

    lock.pop("lock_sha256")
    lock_hash = canonical_hash(lock)
    lock["lock_sha256"] = lock_hash
    lock_path.write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n")
    values["input_locks"][0]["sha256"] = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    values["input_locks"][0]["lock_sha256"] = lock_hash
    path = write_sealed(tmp_path, values, "resealed-lock.json")
    with pytest.raises(ValueError, match="do not bind the input-lock hash"):
        verify_scenario_proof(path, repository_root=root)


def test_post_decision_event_lock_fails_closed_and_cannot_leak(tmp_path: Path) -> None:
    values = proof_values()
    event_path = REPOSITORY / values["event_locks"][0]["path"]
    event = json.loads(event_path.read_text())
    event.pop("lock_sha256")
    event["decision_time"] = event["records"][0]["interval"]["available_at"]
    with pytest.raises(ValidationError, match="after decision_time"):
        seal_official_event_lock(event)

    root = copy_scenario_repository(tmp_path)
    copied_event_path = root / values["event_locks"][0]["path"]
    tampered = json.loads(copied_event_path.read_text())
    tampered["records"][0]["payload"]["form"] = "10-K"
    copied_event_path.write_text(json.dumps(tampered, sort_keys=True, indent=2) + "\n")
    values["event_locks"][0]["sha256"] = hashlib.sha256(copied_event_path.read_bytes()).hexdigest()
    path = write_sealed(tmp_path, values, "tampered-event.json")
    with pytest.raises(ValueError, match="invalid official event lock"):
        verify_scenario_proof(path, repository_root=root)


def test_catalog_rejects_duplicate_scenario_versions(tmp_path: Path) -> None:
    content = PROOF_PATH.read_text()
    (tmp_path / "first.json").write_text(content)
    (tmp_path / "second.json").write_text(content)
    with pytest.raises(ValueError, match="duplicate scenario versions"):
        verify_scenario_catalog(tmp_path, repository_root=REPOSITORY)


def values_from_proof(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))
