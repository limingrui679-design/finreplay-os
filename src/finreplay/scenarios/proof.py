"""Machine-verifiable completion proofs for counted historical ReplayPacks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from finreplay.contracts import BitemporalRecord, EvidenceClass, ScenarioMode, TemporalCoverage
from finreplay.engines import CompiledReplayPack, ReplayStudio

InputLabelName = Literal["observed", "reported", "extracted", "inferred", "bounded", "simulated"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileEvidence(_StrictModel):
    """A repository-relative file whose exact bytes are part of the proof."""

    path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_path(self) -> FileEvidence:
        _safe_relative_path(self.path)
        return self


class InputLockEvidence(FileEvidence):
    """Content lock plus every logical source record it claims to contain."""

    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record_ids(self) -> InputLockEvidence:
        _require_sorted_unique(self.record_ids, "input-lock record IDs")
        return self


class EventLockEvidence(FileEvidence):
    """Official post-decision event records kept outside the decision input manifest."""

    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record_ids(self) -> EventLockEvidence:
        _require_sorted_unique(self.record_ids, "event-lock record IDs")
        return self


class OfficialEventLock(_StrictModel):
    """A content-addressed official event marker that must not leak into replay inputs."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    decision_time: datetime
    event_role: Literal["post_decision_official_event"] = "post_decision_official_event"
    records: tuple[BitemporalRecord, ...] = Field(min_length=1)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lock(self, info: ValidationInfo) -> OfficialEventLock:
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() is None:
            raise ValueError("event-lock decision_time must be timezone-aware")
        record_ids = tuple(record.record_id for record in self.records)
        _require_sorted_unique(record_ids, "event-lock record IDs")
        for record in self.records:
            if record.interval.available_at <= self.decision_time:
                raise ValueError(
                    "post-decision event record must become available after decision_time"
                )
            if record.interval.availability_confidence < 1.0:
                qualified_schedule = (
                    record.source.source_id == "cftc.cot.tff_scheduled_ust2y"
                    and record.interval.availability_confidence == 0.98
                    and record.payload.get("schedule_self_describes_as_tentative") is True
                    and record.payload.get("actual_row_publication_log_available") is False
                    and record.payload.get("availability_method")
                    == "official_current_schedule_exact_time_no_actual_row_log"
                )
                if not qualified_schedule:
                    raise ValueError(
                        "post-decision event timing must be exact or satisfy the qualified "
                        "CFTC schedule boundary"
                    )
            if record.source.temporal_coverage is TemporalCoverage.LATEST_ONLY:
                raise ValueError("post-decision event timing cannot use a latest-only source")
            if not str(record.source.url).startswith("https://"):
                raise ValueError("post-decision event timing must use an HTTPS official source")
        payload = self.model_dump(mode="json", exclude={"lock_sha256"})
        skip_hash = isinstance(info.context, dict) and info.context.get("skip_hash") is True
        if not skip_hash and _hash(payload) != self.lock_sha256:
            raise ValueError("lock_sha256 does not match official event-lock content")
        return self


def seal_official_event_lock(payload: dict[str, Any]) -> OfficialEventLock:
    """Validate and self-hash a JSON-compatible official event lock."""

    values = dict(payload)
    values.pop("lock_sha256", None)
    normalized = OfficialEventLock.model_validate(
        {**values, "lock_sha256": "0" * 64},
        context={"skip_hash": True},
    ).model_dump(mode="json", exclude={"lock_sha256"})
    return OfficialEventLock.model_validate({**normalized, "lock_sha256": _hash(normalized)})


class ArtifactValueExpectation(_StrictModel):
    """Exact machine value used to establish a baseline or deliberate failure mode."""

    artifact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,239}$")
    payload_pointer: str = Field(pattern=r"^(?:|/.*)$", max_length=500)
    expected_value: Any
    description: str = Field(min_length=10, max_length=1_000)

    @model_validator(mode="after")
    def validate_json_value(self) -> ArtifactValueExpectation:
        _canonical_json(self.expected_value)
        return self


class ScenarioInputLabels(_StrictModel):
    """Keep source facts and every derived/bounded/simulated input family separate."""

    observed_record_ids: tuple[str, ...] = ()
    reported_record_ids: tuple[str, ...] = ()
    extracted_artifact_ids: tuple[str, ...] = ()
    inferred_artifact_ids: tuple[str, ...] = ()
    bounded_artifact_ids: tuple[str, ...] = ()
    simulated_artifact_ids: tuple[str, ...] = ()
    absence_reasons: dict[InputLabelName, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_labels(self) -> ScenarioInputLabels:
        groups: dict[InputLabelName, tuple[str, ...]] = {
            "observed": self.observed_record_ids,
            "reported": self.reported_record_ids,
            "extracted": self.extracted_artifact_ids,
            "inferred": self.inferred_artifact_ids,
            "bounded": self.bounded_artifact_ids,
            "simulated": self.simulated_artifact_ids,
        }
        for name, values in groups.items():
            _require_sorted_unique(values, f"{name} input labels")
            if not values and not self.absence_reasons.get(name, "").strip():
                raise ValueError(f"empty {name} input label requires an absence reason")
        if set(self.observed_record_ids) & set(self.reported_record_ids):
            raise ValueError("a source record cannot be both observed and reported")
        for name, reason in self.absence_reasons.items():
            if not reason.strip():
                raise ValueError(f"absence reason for {name} must be non-empty")
            if groups[name]:
                raise ValueError(f"absence reason for populated {name} label is contradictory")
        return self


class ScenarioProof(_StrictModel):
    """Eight-gate evidence record for exactly one counted scenario."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    pack_directory: str = Field(min_length=1, max_length=300)
    expected_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_trace_id: str = Field(pattern=r"^trace:[0-9a-f]{64}$")
    expected_input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    official_adapter_inventory: str = "verification/live/latest-summary.json"
    input_locks: tuple[InputLockEvidence, ...] = Field(min_length=1)
    event_locks: tuple[EventLockEvidence, ...] = Field(min_length=1)
    build_script: FileEvidence
    verify_script: FileEvidence
    rebuild_receipt: FileEvidence
    timing_record_ids: tuple[str, ...] = Field(min_length=1)
    input_labels: ScenarioInputLabels
    naive_baselines: tuple[ArtifactValueExpectation, ...] = Field(min_length=1)
    deliberate_failure_modes: tuple[ArtifactValueExpectation, ...] = Field(min_length=1)
    required_rebuild_assertions: tuple[str, ...] = Field(min_length=1)
    claim_boundary: str = Field(min_length=100, max_length=4_000)
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_proof(self) -> ScenarioProof:
        _safe_relative_path(self.pack_directory)
        _safe_relative_path(self.official_adapter_inventory)
        _require_sorted_unique(self.timing_record_ids, "timing record IDs")
        _require_sorted_unique(self.required_rebuild_assertions, "rebuild assertion names")
        locked = {record_id for lock in self.input_locks for record_id in lock.record_ids}
        event_records = {record_id for lock in self.event_locks for record_id in lock.record_ids}
        if locked & event_records:
            raise ValueError("event-lock records must be disjoint from decision input locks")
        if not set(self.timing_record_ids) <= locked:
            raise ValueError("timing record IDs must be present in an input lock")
        labelled_records = set(self.input_labels.observed_record_ids) | set(
            self.input_labels.reported_record_ids
        )
        if not labelled_records <= locked:
            raise ValueError("labelled source record IDs must be present in an input lock")
        payload = self.model_dump(mode="json", exclude={"proof_sha256"})
        if _hash(payload) != self.proof_sha256:
            raise ValueError("proof_sha256 does not match scenario proof content")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedScenarioProof:
    scenario_id: str
    scenario_version: str
    replay_id: str
    mode: str
    decision_time: str
    pack_sha256: str
    trace_id: str
    distinct_input_records: int
    proof_sha256: str
    proof_path: Path


def seal_scenario_proof(payload: dict[str, Any]) -> ScenarioProof:
    """Validate and self-hash a JSON-compatible proof payload."""

    values = dict(payload)
    values.pop("proof_sha256", None)
    return ScenarioProof.model_validate({**values, "proof_sha256": _hash(values)})


def load_scenario_proof(path: Path) -> ScenarioProof:
    try:
        return ScenarioProof.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid scenario proof: {path}") from error


def verify_scenario_proof(path: Path, *, repository_root: Path) -> VerifiedScenarioProof:
    """Verify every counted-scenario gate against current repository bytes."""

    repository_root = repository_root.expanduser().resolve()
    proof = load_scenario_proof(path)
    pack_directory = _resolve(repository_root, proof.pack_directory)
    receipt = ReplayStudio().verify(pack_directory)
    compiled = CompiledReplayPack.model_validate_json(
        (pack_directory / "report.json").read_text(encoding="utf-8")
    )
    spec = compiled.spec
    identity = (spec.scenario_id, spec.scenario_version, spec.replay_id)
    if identity != (proof.scenario_id, proof.scenario_version, proof.replay_id):
        raise ValueError("scenario proof identity does not match ReplayPack")
    if compiled.pack_sha256 != proof.expected_pack_sha256:
        raise ValueError("scenario proof expected_pack_sha256 does not match ReplayPack")
    if compiled.trace_id != proof.expected_trace_id:
        raise ValueError("scenario proof expected_trace_id does not match ReplayPack")
    if compiled.input_manifest_sha256 != proof.expected_input_manifest_sha256:
        raise ValueError("scenario proof input manifest does not match ReplayPack")
    if spec.status.value != "reproduced":
        raise ValueError("counted scenario ReplayPack status must be reproduced")

    inventory_path = _resolve(repository_root, proof.official_adapter_inventory)
    inventory = _json_object(json.loads(inventory_path.read_text()), "adapter inventory")
    adapters = inventory.get("adapters")
    if not isinstance(adapters, list):
        raise ValueError("adapter inventory adapters must be a list")
    official_ids = {
        item.get("adapter_id")
        for item in adapters
        if isinstance(item, dict) and isinstance(item.get("adapter_id"), str)
    }

    event_records: dict[str, BitemporalRecord] = {}
    for event_reference in proof.event_locks:
        event_path = _verify_file(event_reference, repository_root)
        try:
            event_lock = OfficialEventLock.model_validate_json(event_path.read_text())
        except ValueError as error:
            raise ValueError(f"invalid official event lock: {event_reference.path}") from error
        if event_lock.scenario_id != proof.scenario_id:
            raise ValueError("event-lock scenario_id does not match proof")
        if event_lock.scenario_version != proof.scenario_version:
            raise ValueError("event-lock scenario_version does not match proof")
        if event_lock.decision_time != spec.decision_time:
            raise ValueError("event-lock decision_time does not match ReplayPack")
        if event_lock.lock_sha256 != event_reference.lock_sha256:
            raise ValueError("event-lock claimed hash does not match proof reference")
        record_ids = tuple(record.record_id for record in event_lock.records)
        if record_ids != event_reference.record_ids:
            raise ValueError("event-lock record IDs do not match proof reference")
        duplicate = set(event_records).intersection(record_ids)
        if duplicate:
            raise ValueError(f"duplicate record across event locks: {min(duplicate)}")
        for record in event_lock.records:
            if record.source.source_id not in official_ids:
                raise ValueError(
                    "scenario event source is absent from official adapter inventory: "
                    f"{record.source.source_id}"
                )
        event_records.update((record.record_id, record) for record in event_lock.records)

    locked_records: dict[str, BitemporalRecord] = {}
    for input_reference in proof.input_locks:
        lock_path = _verify_file(input_reference, repository_root)
        lock_root = _json_object(json.loads(lock_path.read_text()), "scenario input lock")
        lock_payload = {key: value for key, value in lock_root.items() if key != "lock_sha256"}
        if lock_root.get("lock_sha256") != input_reference.lock_sha256:
            raise ValueError("input-lock claimed hash does not match proof reference")
        if _hash(lock_payload) != input_reference.lock_sha256:
            raise ValueError("input-lock canonical content hash is invalid")
        if not any(
            _payload_contains(artifact.payload, "input_lock_sha256", input_reference.lock_sha256)
            for artifact in spec.artifacts
        ):
            raise ValueError("ReplayPack artifacts do not bind the input-lock hash")
        if lock_root.get("scenario_id") != proof.scenario_id:
            raise ValueError("input-lock scenario_id does not match proof")
        if lock_root.get("scenario_version") != proof.scenario_version:
            raise ValueError("input-lock scenario_version does not match proof")
        raw_records = lock_root.get("records")
        if not isinstance(raw_records, list):
            raise ValueError("scenario input lock records must be a list")
        records = tuple(BitemporalRecord.model_validate(item) for item in raw_records)
        record_ids = tuple(sorted(record.record_id for record in records))
        if record_ids != input_reference.record_ids:
            raise ValueError("input-lock record IDs do not match proof reference")
        duplicate = set(locked_records).intersection(record_ids)
        if duplicate:
            raise ValueError(f"duplicate record across scenario input locks: {min(duplicate)}")
        locked_records.update((record.record_id, record) for record in records)

    if tuple(sorted(locked_records)) != compiled.source_record_ids:
        raise ValueError("ReplayPack source record IDs do not exactly match input locks")
    if spec.distinct_input_records != len(locked_records):
        raise ValueError("ReplayPack distinct_input_records does not match input locks")
    for record in locked_records.values():
        if record.source.source_id not in official_ids:
            raise ValueError(
                "scenario source is absent from official adapter inventory: "
                f"{record.source.source_id}"
            )
        if record.source.temporal_coverage is TemporalCoverage.LATEST_ONLY:
            raise ValueError("counted historical scenario cannot use a latest-only decision input")
        if record.interval.available_at > spec.decision_time:
            raise ValueError("scenario input became available after decision_time")
        if not record.interval.availability_rule.strip():
            raise ValueError("scenario input lacks an availability rule")

    if set(event_records) & set(compiled.source_record_ids):
        raise ValueError("post-decision event evidence leaked into ReplayPack source records")

    for record_id in proof.timing_record_ids:
        record = locked_records[record_id]
        if record.interval.availability_confidence < 1.0:
            raise ValueError("timing evidence must have exact availability confidence")
        if not str(record.source.url).startswith("https://"):
            raise ValueError("timing evidence must use an HTTPS official source")

    _verify_input_labels(proof.input_labels, locked_records, compiled)
    artifacts = {artifact.artifact_id: artifact for artifact in spec.artifacts}
    for expectation in (*proof.naive_baselines, *proof.deliberate_failure_modes):
        artifact = artifacts.get(expectation.artifact_id)
        if artifact is None:
            raise ValueError(
                f"scenario expectation references unknown artifact: {expectation.artifact_id}"
            )
        actual = _resolve_json_pointer(artifact.payload, expectation.payload_pointer)
        if actual != expectation.expected_value:
            raise ValueError(
                f"scenario expectation differs at {expectation.artifact_id}"
                f"{expectation.payload_pointer}"
            )

    _verify_file(proof.build_script, repository_root)
    _verify_file(proof.verify_script, repository_root)
    rebuild_path = _verify_file(proof.rebuild_receipt, repository_root)
    rebuild = _verify_rebuild_receipt(rebuild_path)
    if rebuild.get("pack_sha256") != receipt.pack_sha256:
        raise ValueError("rebuild receipt pack_sha256 does not match ReplayPack receipt")
    if rebuild.get("pack_receipt_sha256") != receipt.receipt_sha256:
        raise ValueError("rebuild receipt does not bind the current ReplayPack receipt")
    if rebuild.get("trace_id") != compiled.trace_id:
        raise ValueError("rebuild receipt trace_id does not match ReplayPack")
    if rebuild.get("code_commit") != spec.code_commit:
        raise ValueError("rebuild receipt code_commit does not match ReplayPack")
    runtime = _json_object(rebuild.get("runtime"), "rebuild runtime")
    if runtime.get("runner_dirty") is not False:
        raise ValueError("counted scenario rebuild receipt must come from a clean worktree")
    assertions = _json_object(rebuild.get("assertions"), "rebuild assertions")
    for name in proof.required_rebuild_assertions:
        if assertions.get(name) is not True:
            raise ValueError(f"required rebuild assertion is not true: {name}")
    if len(spec.limitations) < 3 or not spec.claim_boundary.strip():
        raise ValueError("counted scenario requires explicit pack limitations and claim boundary")

    return VerifiedScenarioProof(
        scenario_id=proof.scenario_id,
        scenario_version=proof.scenario_version,
        replay_id=proof.replay_id,
        mode=spec.mode.value,
        decision_time=spec.decision_time.isoformat(),
        pack_sha256=compiled.pack_sha256,
        trace_id=compiled.trace_id,
        distinct_input_records=spec.distinct_input_records,
        proof_sha256=proof.proof_sha256,
        proof_path=path.expanduser().resolve(),
    )


def verify_scenario_catalog(
    proof_directory: Path,
    *,
    repository_root: Path,
) -> tuple[VerifiedScenarioProof, ...]:
    """Verify all proof files and reject duplicate counted scenario versions."""

    paths = sorted(proof_directory.expanduser().resolve().glob("*.json"))
    verified = tuple(verify_scenario_proof(path, repository_root=repository_root) for path in paths)
    identities = [(item.scenario_id, item.scenario_version) for item in verified]
    if len(identities) != len(set(identities)):
        raise ValueError("scenario proof catalog contains duplicate scenario versions")
    return tuple(sorted(verified, key=lambda item: (item.scenario_id, item.scenario_version)))


def scenario_catalog_summary(
    verified: tuple[VerifiedScenarioProof, ...], *, proof_directory: Path
) -> dict[str, Any]:
    root = proof_directory.expanduser().resolve()
    return {
        "schema_version": "1.1.0",
        "verified_scenario_count": len(verified),
        "claim_boundary": (
            "Each counted scenario passed the repository's eight-gate proof verifier, including "
            "a disjoint official post-decision event lock. This is internal reproducibility "
            "evidence, not external method validation, deployment, investment performance, or "
            "real-world impact."
        ),
        "scenarios": [
            {
                "scenario_id": item.scenario_id,
                "scenario_version": item.scenario_version,
                "replay_id": item.replay_id,
                "mode": item.mode,
                "decision_time": item.decision_time,
                "distinct_input_records": item.distinct_input_records,
                "pack_sha256": item.pack_sha256,
                "trace_id": item.trace_id,
                "proof_sha256": item.proof_sha256,
                "proof": item.proof_path.relative_to(root).as_posix(),
            }
            for item in verified
        ],
    }


def _verify_input_labels(
    labels: ScenarioInputLabels,
    records: dict[str, BitemporalRecord],
    compiled: CompiledReplayPack,
) -> None:
    for record_id in labels.observed_record_ids:
        if records[record_id].evidence_class is not EvidenceClass.OBSERVED:
            raise ValueError("observed label points to a non-observed source record")
    for record_id in labels.reported_record_ids:
        if records[record_id].evidence_class is not EvidenceClass.REPORTED:
            raise ValueError("reported label points to a non-reported source record")
    artifacts = {artifact.artifact_id: artifact for artifact in compiled.spec.artifacts}
    evidence_groups = (
        (labels.extracted_artifact_ids, EvidenceClass.EXTRACTED, "extracted"),
        (labels.inferred_artifact_ids, EvidenceClass.INFERRED, "inferred"),
        (labels.simulated_artifact_ids, EvidenceClass.SIMULATED, "simulated"),
    )
    for artifact_ids, evidence_class, name in evidence_groups:
        for artifact_id in artifact_ids:
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.evidence_counts.get(evidence_class, 0) <= 0:
                raise ValueError(f"{name} label lacks matching artifact evidence: {artifact_id}")
    if labels.bounded_artifact_ids and compiled.spec.mode not in {
        ScenarioMode.BOUNDED_RECONSTRUCTION,
        ScenarioMode.COUNTERFACTUAL,
        ScenarioMode.ADVERSARIAL,
    }:
        raise ValueError("bounded labels require a bounded/counterfactual/adversarial scenario")
    for artifact_id in labels.bounded_artifact_ids:
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            raise ValueError(f"bounded label references unknown artifact: {artifact_id}")
        kind = artifact.artifact_kind.lower()
        if not any(marker in kind for marker in ("bound", "envelope", "interval")):
            raise ValueError(f"bounded label lacks a bounded artifact kind: {artifact_id}")


def _verify_file(reference: FileEvidence, repository_root: Path) -> Path:
    path = _resolve(repository_root, reference.path)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.sha256:
        raise ValueError(f"scenario proof file hash mismatch: {reference.path}")
    return path


def _verify_rebuild_receipt(path: Path) -> dict[str, Any]:
    root = _json_object(json.loads(path.read_text()), "scenario rebuild receipt")
    claimed_receipt = root.get("receipt_sha256")
    receipt_payload = {key: value for key, value in root.items() if key != "receipt_sha256"}
    if claimed_receipt != _hash(receipt_payload):
        raise ValueError("scenario rebuild receipt self-hash mismatch")
    semantic_payload = dict(receipt_payload)
    semantic_payload.pop("runtime", None)
    claimed_semantic = semantic_payload.pop("semantic_sha256", None)
    if claimed_semantic != _hash(semantic_payload):
        raise ValueError("scenario rebuild semantic hash mismatch")
    return root


def _resolve(repository_root: Path, relative_path: str) -> Path:
    _safe_relative_path(relative_path)
    resolved = (repository_root / relative_path).resolve()
    if not resolved.is_relative_to(repository_root):
        raise ValueError(f"scenario proof path escaped repository: {relative_path}")
    if not resolved.exists():
        raise ValueError(f"scenario proof path does not exist: {relative_path}")
    return resolved


def _resolve_json_pointer(document: Any, pointer: str) -> Any:
    current = document
    if pointer == "":
        return current
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError(f"JSON pointer does not resolve: {pointer}")
    return current


def _payload_contains(document: Any, key: str, expected: Any) -> bool:
    if isinstance(document, dict):
        if document.get(key) == expected:
            return True
        return any(_payload_contains(value, key, expected) for value in document.values())
    if isinstance(document, list):
        return any(_payload_contains(value, key, expected) for value in document)
    return False


def _safe_relative_path(value: str) -> None:
    if "\\" in value:
        raise ValueError("scenario proof paths must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("scenario proof path must be a safe repository-relative POSIX path")


def _require_sorted_unique(values: tuple[str, ...], context: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{context} must be non-empty")
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ValueError(f"{context} must be unique and sorted")


def _json_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
