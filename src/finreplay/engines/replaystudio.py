"""Deterministic, evidence-labelled, human- and machine-readable ReplayPacks."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
import zipfile
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.contracts import (
    ArtifactStatus,
    EvidenceClass,
    ReplayPackManifest,
    ScenarioMode,
)


class ReplayStudioError(RuntimeError):
    """Raised when a ReplayPack cannot be compiled, rendered, or verified safely."""


class ReplayPackMutationError(ReplayStudioError):
    """Raised when an existing ReplayPack path contains different content."""


class EngineName(StrEnum):
    TIMEVAULT = "timevault"
    TRIALCOURT = "trialcourt"
    MARKETTWIN = "markettwin"
    SHOCKCOMPILER = "shockcompiler"
    EXECUTIONLAB = "executionlab"
    CAPITALALLOCATOR = "capitalallocator"
    REPLAYSTUDIO = "replaystudio"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReplayArtifact(_StrictModel):
    """One content-addressed engine result and its inherited evidence boundary."""

    artifact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,239}$")
    engine: EngineName
    artifact_kind: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    status: ArtifactStatus
    evidence_counts: dict[EvidenceClass, int] = Field(min_length=1)
    source_set_historical_replay_eligible: bool
    source_record_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    upstream_artifact_ids: tuple[str, ...]
    payload: dict[str, Any]
    limitations: tuple[str, ...] = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_artifact(self) -> ReplayArtifact:
        if any(count < 0 for count in self.evidence_counts.values()):
            raise ValueError("evidence counts cannot be negative")
        if sum(self.evidence_counts.values()) <= 0:
            raise ValueError("artifact must count at least one evidence item")
        if any(not item.strip() for item in self.source_record_ids):
            raise ValueError("source_record_ids must be non-empty")
        if len(set(self.source_record_ids)) != len(self.source_record_ids):
            raise ValueError("source_record_ids must be unique")
        if tuple(sorted(self.source_record_ids)) != self.source_record_ids:
            raise ValueError("source_record_ids must be sorted")
        if any(
            len(digest) != 64 or set(digest) - set("0123456789abcdef")
            for digest in self.source_hashes
        ):
            raise ValueError("source_hashes must be lowercase SHA-256 values")
        if len(set(self.source_hashes)) != len(self.source_hashes):
            raise ValueError("source_hashes must be unique")
        if tuple(sorted(self.source_hashes)) != self.source_hashes:
            raise ValueError("source_hashes must be sorted")
        if len(set(self.upstream_artifact_ids)) != len(self.upstream_artifact_ids):
            raise ValueError("upstream_artifact_ids must be unique")
        if tuple(sorted(self.upstream_artifact_ids)) != self.upstream_artifact_ids:
            raise ValueError("upstream_artifact_ids must be sorted")
        if self.artifact_id in self.upstream_artifact_ids:
            raise ValueError("artifact cannot depend on itself")
        sourced_count = sum(
            self.evidence_counts.get(kind, 0)
            for kind in (
                EvidenceClass.OBSERVED,
                EvidenceClass.REPORTED,
                EvidenceClass.EXTRACTED,
            )
        )
        if sourced_count and (not self.source_record_ids or not self.source_hashes):
            raise ValueError("sourced evidence counts require source record IDs and hashes")
        if self.source_set_historical_replay_eligible and not self.source_hashes:
            raise ValueError("historical source eligibility requires at least one source hash")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("artifact limitations must be non-empty")
        _canonical_json(self.payload)
        if _hash(_replay_artifact_payload(self)) != self.artifact_sha256:
            raise ValueError("artifact_sha256 does not match artifact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        artifact_id: str,
        engine: EngineName,
        artifact_kind: str,
        status: ArtifactStatus,
        evidence_counts: dict[EvidenceClass, int],
        source_set_historical_replay_eligible: bool,
        source_record_ids: tuple[str, ...],
        source_hashes: tuple[str, ...],
        upstream_artifact_ids: tuple[str, ...],
        payload: dict[str, Any],
        limitations: tuple[str, ...],
    ) -> ReplayArtifact:
        values: dict[str, Any] = {
            "artifact_id": artifact_id,
            "engine": engine.value,
            "artifact_kind": artifact_kind,
            "status": status.value,
            "evidence_counts": {key.value: value for key, value in evidence_counts.items()},
            "source_set_historical_replay_eligible": (source_set_historical_replay_eligible),
            "source_record_ids": sorted(source_record_ids),
            "source_hashes": sorted(source_hashes),
            "upstream_artifact_ids": sorted(upstream_artifact_ids),
            "payload": payload,
            "limitations": list(limitations),
        }
        return cls(**values, artifact_sha256=_hash(values))


class ReplayClaim(_StrictModel):
    """One public-facing statement tied to specific engine artifacts and a truth label."""

    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,199}$")
    statement: str = Field(min_length=5, max_length=4_000)
    evidence_class: EvidenceClass
    support_artifact_ids: tuple[str, ...] = Field(min_length=1)
    boundary: str = Field(min_length=10, max_length=4_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_claim(self) -> ReplayClaim:
        if len(set(self.support_artifact_ids)) != len(self.support_artifact_ids):
            raise ValueError("claim support artifact IDs must be unique")
        if tuple(sorted(self.support_artifact_ids)) != self.support_artifact_ids:
            raise ValueError("claim support artifact IDs must be sorted")
        if any(not item.strip() for item in self.limitations):
            raise ValueError("claim limitations must be non-empty")
        return self


class ReplayPackSpec(_StrictModel):
    """Complete deterministic input to ReplayStudio before derived hashes and trace IDs."""

    replay_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,119}$")
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,99}$")
    scenario_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    title: str = Field(min_length=5, max_length=300)
    mode: ScenarioMode
    decision_time: datetime
    created_at: datetime
    code_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|uncommitted)$")
    status: ArtifactStatus
    artifacts: tuple[ReplayArtifact, ...] = Field(min_length=1)
    claims: tuple[ReplayClaim, ...] = Field(min_length=1)
    require_all_engines: bool = False
    distinct_input_records: int = Field(ge=0)
    derived_records: int = Field(ge=0)
    compressed_input_bytes: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0.0)
    claim_boundary: str = Field(min_length=20, max_length=8_000)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_spec(self) -> ReplayPackSpec:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.created_at, "created_at")
        if not self.created_at >= self.decision_time:
            raise ValueError("created_at must not precede decision_time")
        if not self.elapsed_seconds < float("inf"):
            raise ValueError("elapsed_seconds must be finite")
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact IDs must be unique")
        known = set(artifact_ids)
        for artifact in self.artifacts:
            missing = set(artifact.upstream_artifact_ids) - known
            if missing:
                raise ValueError(
                    f"artifact {artifact.artifact_id} has missing upstream IDs: {sorted(missing)}"
                )
        _topological_order(self.artifacts)
        if self.require_all_engines and {artifact.engine for artifact in self.artifacts} != set(
            EngineName
        ):
            raise ValueError("complete ReplayPack requires every engine")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique")
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in self.artifacts}
        for claim in self.claims:
            missing = set(claim.support_artifact_ids) - known
            if missing:
                raise ValueError(
                    f"claim {claim.claim_id} has missing support artifacts: {sorted(missing)}"
                )
            support_count = sum(
                artifacts_by_id[artifact_id].evidence_counts.get(claim.evidence_class, 0)
                for artifact_id in claim.support_artifact_ids
            )
            if support_count <= 0:
                raise ValueError(
                    f"claim {claim.claim_id} has no support with its declared evidence class"
                )
        if any(not item.strip() for item in self.limitations):
            raise ValueError("ReplayPack limitations must be non-empty")
        return self


class CompiledReplayPack(_StrictModel):
    spec: ReplayPackSpec
    trace_id: str = Field(pattern=r"^trace:[0-9a-f]{64}$")
    topological_artifact_ids: tuple[str, ...]
    engine_artifact_counts: dict[EngineName, int]
    evidence_totals: dict[EvidenceClass, int]
    source_record_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    source_set_historical_replay_eligible: bool
    contains_simulation: bool
    input_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_compiled(self) -> CompiledReplayPack:
        artifact_ids = tuple(artifact.artifact_id for artifact in self.spec.artifacts)
        claim_ids = tuple(claim.claim_id for claim in self.spec.claims)
        if artifact_ids != tuple(sorted(artifact_ids)):
            raise ValueError("compiled ReplayPack artifacts must be canonically sorted")
        if claim_ids != tuple(sorted(claim_ids)):
            raise ValueError("compiled ReplayPack claims must be canonically sorted")
        derived = _derive_pack(self.spec)
        for name, value in derived.items():
            if name != "pack_sha256" and getattr(self, name) != value:
                raise ValueError(f"compiled ReplayPack {name} does not match its spec")
        if self.pack_sha256 != _hash(_compiled_pack_payload(self)):
            raise ValueError("pack_sha256 does not match compiled ReplayPack content")
        return self


class ReplayFileEntry(_StrictModel):
    relative_path: str = Field(min_length=1, max_length=300)
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=3, max_length=200)
    role: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")

    @model_validator(mode="after")
    def validate_relative_path(self) -> ReplayFileEntry:
        _safe_relative_path(self.relative_path)
        if self.relative_path == "manifest.json":
            raise ValueError("manifest cannot include itself in the file table")
        return self


class ReplayPackReceipt(_StrictModel):
    schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    replay_id: str
    trace_id: str = Field(pattern=r"^trace:[0-9a-f]{64}$")
    replay_manifest: ReplayPackManifest
    pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[ReplayFileEntry, ...] = Field(min_length=1)
    claim_boundary: str = Field(min_length=20)
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_receipt(self) -> ReplayPackReceipt:
        paths = [entry.relative_path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("ReplayPack receipt file paths must be unique")
        if paths != sorted(paths):
            raise ValueError("ReplayPack receipt files must be canonically sorted")
        required_paths = {
            "README.md",
            "assets/styles.css",
            "checksums.sha256",
            "index.html",
            "report.json",
        }
        if set(paths) != required_paths:
            raise ValueError("ReplayPack receipt must contain the fixed portable file set")
        expected_output_hash = _hash([entry.model_dump(mode="json") for entry in self.files])
        if self.replay_manifest.output_manifest_sha256 != expected_output_hash:
            raise ValueError("output manifest hash does not match receipt files")
        report = next(
            (entry for entry in self.files if entry.relative_path == "report.json"),
            None,
        )
        if report is None or report.sha256 != self.report_sha256:
            raise ValueError("report hash does not match report.json file entry")
        if _hash(_receipt_payload(self)) != self.receipt_sha256:
            raise ValueError("receipt_sha256 does not match ReplayPack receipt content")
        return self


class ReplayBuildResult(_StrictModel):
    root: Path
    receipt: ReplayPackReceipt
    idempotent: bool


class ReplayStudio:
    """Compile, atomically write, verify, and deterministically archive ReplayPacks."""

    def compile(self, spec: ReplayPackSpec) -> CompiledReplayPack:
        canonical_values = spec.model_dump(mode="python")
        canonical_values["artifacts"] = tuple(
            sorted(spec.artifacts, key=lambda artifact: artifact.artifact_id)
        )
        canonical_values["claims"] = tuple(sorted(spec.claims, key=lambda claim: claim.claim_id))
        canonical_spec = ReplayPackSpec.model_validate(canonical_values)
        derived = _derive_pack(canonical_spec)
        values: dict[str, Any] = {
            "spec": canonical_spec.model_dump(mode="json"),
            **derived,
        }
        values["pack_sha256"] = _hash(values)
        return CompiledReplayPack.model_validate(values)

    def build(self, spec: ReplayPackSpec, destination: Path) -> ReplayBuildResult:
        destination = _safe_destination(destination)
        pack = self.compile(spec)
        files = self._render_files(pack)
        receipt = self._receipt(pack, files)
        all_files = {
            **files,
            "manifest.json": _pretty_json(receipt.model_dump(mode="json")),
        }
        if destination.exists():
            existing = self.verify(destination)
            if existing.receipt_sha256 != receipt.receipt_sha256:
                raise ReplayPackMutationError(
                    "destination contains a different ReplayPack; refusing overwrite"
                )
            _verify_exact_file_set(destination, all_files)
            return ReplayBuildResult(root=destination, receipt=existing, idempotent=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
        )
        try:
            for relative_path, content in sorted(all_files.items()):
                target = temporary / _safe_relative_path(relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            _fsync_tree(temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        verified = self.verify(destination)
        if verified.receipt_sha256 != receipt.receipt_sha256:
            raise ReplayStudioError("post-write verification receipt mismatch")
        return ReplayBuildResult(root=destination, receipt=verified, idempotent=False)

    def verify(self, root: Path) -> ReplayPackReceipt:
        expanded_root = root.expanduser()
        if expanded_root.is_symlink():
            raise ReplayStudioError("ReplayPack root cannot be a symlink")
        root = expanded_root.resolve()
        if root == Path(root.anchor) or not root.is_dir():
            raise ReplayStudioError("ReplayPack root must be an existing non-root directory")
        manifest_path = root / "manifest.json"
        try:
            raw_manifest = json.loads(manifest_path.read_text())
            receipt = ReplayPackReceipt.model_validate(raw_manifest)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ReplayStudioError("invalid ReplayPack manifest") from error
        expected_paths = {entry.relative_path for entry in receipt.files} | {"manifest.json"}
        expected_directories = {
            str(parent)
            for relative_path in expected_paths
            for parent in PurePosixPath(relative_path).parents
            if str(parent) != "."
        }
        actual_paths: set[str] = set()
        actual_directories: set[str] = set()
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ReplayStudioError("ReplayPack cannot contain symlinks")
            if path.is_file():
                actual_paths.add(path.relative_to(root).as_posix())
            elif path.is_dir():
                actual_directories.add(path.relative_to(root).as_posix())
        if actual_paths != expected_paths:
            raise ReplayStudioError("ReplayPack contains missing or unlisted files")
        if actual_directories != expected_directories:
            raise ReplayStudioError("ReplayPack contains unlisted directories")
        entries = {entry.relative_path: entry for entry in receipt.files}
        for relative_path, entry in entries.items():
            path = (root / _safe_relative_path(relative_path)).resolve()
            if not path.is_relative_to(root):
                raise ReplayStudioError("ReplayPack file escaped its root")
            content = path.read_bytes()
            if len(content) != entry.bytes or _sha256_bytes(content) != entry.sha256:
                raise ReplayStudioError(f"ReplayPack file hash mismatch: {relative_path}")
        expected_output_hash = _hash([entry.model_dump(mode="json") for entry in receipt.files])
        if expected_output_hash != receipt.replay_manifest.output_manifest_sha256:
            raise ReplayStudioError("ReplayPack output manifest hash mismatch")
        report_path = root / "report.json"
        try:
            report = CompiledReplayPack.model_validate_json(report_path.read_text())
        except ValueError as error:
            raise ReplayStudioError("invalid compiled report.json") from error
        if (
            report.pack_sha256 != receipt.pack_sha256
            or report.trace_id != receipt.trace_id
            or report.input_manifest_sha256 != receipt.replay_manifest.input_manifest_sha256
        ):
            raise ReplayStudioError("manifest and report identity mismatch")
        expected_files = self._render_files(report)
        for relative_path, expected_content in expected_files.items():
            actual_content = (root / _safe_relative_path(relative_path)).read_bytes()
            if actual_content != expected_content:
                raise ReplayStudioError(
                    f"ReplayPack file differs from deterministic render: {relative_path}"
                )
        expected_receipt = self._receipt(report, expected_files)
        if receipt != expected_receipt:
            raise ReplayStudioError("manifest differs from deterministic ReplayPack receipt")
        _verify_checksums(root, entries)
        index_text = (root / "index.html").read_text()
        if html.escape(report.trace_id) not in index_text:
            raise ReplayStudioError("HTML report does not display its trace ID")
        return receipt

    def archive(self, root: Path, destination: Path) -> Path:
        receipt = self.verify(root)
        root = root.expanduser().resolve()
        expanded_destination = destination.expanduser()
        if expanded_destination.is_symlink():
            raise ReplayStudioError("archive destination cannot be a symlink")
        destination = expanded_destination.resolve()
        if destination == Path(destination.anchor) or destination.suffix.lower() != ".zip":
            raise ReplayStudioError("archive destination must be a non-root .zip path")
        if destination.is_relative_to(root):
            raise ReplayStudioError("archive destination cannot be inside the ReplayPack")
        destination.parent.mkdir(parents=True, exist_ok=True)
        timestamp = receipt.replay_manifest.created_at.astimezone(UTC)
        year = min(max(timestamp.year, 1980), 2107)
        zip_time = (year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, 0)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                archive.comment = receipt.pack_sha256.encode()
                for path in sorted(item for item in root.rglob("*") if item.is_file()):
                    relative = path.relative_to(root).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=zip_time)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    info.create_system = 3
                    archive.writestr(info, path.read_bytes(), compresslevel=9)
            content = temporary.read_bytes()
            if destination.exists():
                if destination.read_bytes() != content:
                    raise ReplayPackMutationError(
                        "archive destination contains different bytes; refusing overwrite"
                    )
                return destination
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    @staticmethod
    def _render_files(pack: CompiledReplayPack) -> dict[str, bytes]:
        report = _pretty_json(pack.model_dump(mode="json"))
        index = _render_html(pack).encode()
        styles = _styles().encode()
        readme = _render_readme(pack).encode()
        base = {
            "README.md": readme,
            "assets/styles.css": styles,
            "index.html": index,
            "report.json": report,
        }
        checksum_lines = "".join(
            f"{_sha256_bytes(content)}  {relative_path}\n"
            for relative_path, content in sorted(base.items())
        ).encode()
        return {**base, "checksums.sha256": checksum_lines}

    @staticmethod
    def _receipt(
        pack: CompiledReplayPack,
        files: dict[str, bytes],
    ) -> ReplayPackReceipt:
        media_types = {
            "README.md": "text/markdown; charset=utf-8",
            "assets/styles.css": "text/css; charset=utf-8",
            "checksums.sha256": "text/plain; charset=utf-8",
            "index.html": "text/html; charset=utf-8",
            "report.json": "application/json",
        }
        roles = {
            "README.md": "human-readme",
            "assets/styles.css": "report-style",
            "checksums.sha256": "portable-checksums",
            "index.html": "human-report",
            "report.json": "machine-report",
        }
        entries = tuple(
            ReplayFileEntry(
                relative_path=relative_path,
                bytes=len(content),
                sha256=_sha256_bytes(content),
                media_type=media_types[relative_path],
                role=roles[relative_path],
            )
            for relative_path, content in sorted(files.items())
        )
        output_hash = _hash([entry.model_dump(mode="json") for entry in entries])
        spec = pack.spec
        replay_manifest = ReplayPackManifest(
            replay_id=spec.replay_id,
            scenario_id=spec.scenario_id,
            scenario_version=spec.scenario_version,
            created_at=spec.created_at,
            code_commit=spec.code_commit,
            input_manifest_sha256=pack.input_manifest_sha256,
            output_manifest_sha256=output_hash,
            distinct_input_records=spec.distinct_input_records,
            derived_records=spec.derived_records,
            compressed_input_bytes=spec.compressed_input_bytes,
            elapsed_seconds=spec.elapsed_seconds,
            status=spec.status,
        )
        values: dict[str, Any] = {
            "schema_version": "1.0.0",
            "replay_id": spec.replay_id,
            "trace_id": pack.trace_id,
            "replay_manifest": replay_manifest.model_dump(mode="json"),
            "pack_sha256": pack.pack_sha256,
            "report_sha256": _sha256_bytes(files["report.json"]),
            "files": [entry.model_dump(mode="json") for entry in entries],
            "claim_boundary": spec.claim_boundary,
        }
        return ReplayPackReceipt(**values, receipt_sha256=_hash(values))


def _derive_pack(spec: ReplayPackSpec) -> dict[str, Any]:
    ordered_ids = _topological_order(spec.artifacts)
    engine_counts = Counter(artifact.engine for artifact in spec.artifacts)
    evidence_totals: Counter[EvidenceClass] = Counter()
    for artifact in spec.artifacts:
        evidence_totals.update(artifact.evidence_counts)
    source_record_ids = tuple(
        sorted({item for artifact in spec.artifacts for item in artifact.source_record_ids})
    )
    source_hashes = tuple(
        sorted({item for artifact in spec.artifacts for item in artifact.source_hashes})
    )
    artifacts_with_sources = tuple(
        artifact for artifact in spec.artifacts if artifact.source_hashes
    )
    eligible = bool(artifacts_with_sources) and all(
        artifact.source_set_historical_replay_eligible for artifact in artifacts_with_sources
    )
    contains_simulation = evidence_totals[EvidenceClass.SIMULATED] > 0
    input_payload = {
        "replay_id": spec.replay_id,
        "scenario_id": spec.scenario_id,
        "scenario_version": spec.scenario_version,
        "mode": spec.mode.value,
        "decision_time": _canonical_datetime(spec.decision_time),
        "artifacts": [
            {
                "artifact_id": artifact.artifact_id,
                "artifact_sha256": artifact.artifact_sha256,
                "upstream_artifact_ids": sorted(artifact.upstream_artifact_ids),
            }
            for artifact in sorted(spec.artifacts, key=lambda item: item.artifact_id)
        ],
        "claims": [
            claim.model_dump(mode="json")
            for claim in sorted(spec.claims, key=lambda item: item.claim_id)
        ],
        "source_record_ids": list(source_record_ids),
        "source_hashes": list(source_hashes),
    }
    input_hash = _hash(input_payload)
    trace_id = f"trace:{_hash({'scenario': spec.scenario_id, 'input': input_hash})}"
    return {
        "trace_id": trace_id,
        "topological_artifact_ids": ordered_ids,
        "engine_artifact_counts": dict(
            sorted(engine_counts.items(), key=lambda item: item[0].value)
        ),
        "evidence_totals": dict(sorted(evidence_totals.items(), key=lambda item: item[0].value)),
        "source_record_ids": source_record_ids,
        "source_hashes": source_hashes,
        "source_set_historical_replay_eligible": eligible,
        "contains_simulation": contains_simulation,
        "input_manifest_sha256": input_hash,
    }


def _topological_order(artifacts: tuple[ReplayArtifact, ...]) -> tuple[str, ...]:
    dependencies = {
        artifact.artifact_id: set(artifact.upstream_artifact_ids) for artifact in artifacts
    }
    ordered: list[str] = []
    while dependencies:
        ready = sorted(
            artifact_id for artifact_id, upstream in dependencies.items() if not upstream
        )
        if not ready:
            raise ValueError("artifact dependency graph contains a cycle")
        ordered.extend(ready)
        for artifact_id in ready:
            dependencies.pop(artifact_id)
        for upstream in dependencies.values():
            upstream.difference_update(ready)
    return tuple(ordered)


def _replay_artifact_payload(artifact: ReplayArtifact) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json", exclude={"artifact_sha256"})
    payload["source_record_ids"] = sorted(payload["source_record_ids"])
    payload["source_hashes"] = sorted(payload["source_hashes"])
    payload["upstream_artifact_ids"] = sorted(payload["upstream_artifact_ids"])
    return payload


def _compiled_pack_payload(pack: CompiledReplayPack) -> dict[str, Any]:
    return pack.model_dump(mode="json", exclude={"pack_sha256"})


def _receipt_payload(receipt: ReplayPackReceipt) -> dict[str, Any]:
    return receipt.model_dump(mode="json", exclude={"receipt_sha256"})


def _render_html(pack: CompiledReplayPack) -> str:
    spec = pack.spec
    evidence_cards = "".join(
        (
            '<li class="metric"><span class="metric-value">'
            f"{count:,}</span><span>{html.escape(kind.value)}</span></li>"
        )
        for kind, count in sorted(pack.evidence_totals.items(), key=lambda item: item[0].value)
    )
    claim_rows: list[str] = []
    for claim in spec.claims:
        support = "<br>".join(
            f"<code>{html.escape(item)}</code>" for item in claim.support_artifact_ids
        )
        claim_rows.append(
            "<tr>"
            f'<th scope="row"><code>{html.escape(claim.claim_id)}</code></th>'
            f"<td>{html.escape(claim.statement)}</td>"
            f'<td><span class="badge evidence-{html.escape(claim.evidence_class.value)}">'
            f"{html.escape(claim.evidence_class.value.upper())}</span></td>"
            f"<td>{support}</td>"
            f"<td>{html.escape(claim.boundary)}</td>"
            "</tr>"
        )
    claims = "".join(claim_rows)
    artifacts = "".join(_render_artifact(artifact) for artifact in spec.artifacts)
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in spec.limitations)
    engine_count = len(pack.engine_artifact_counts)
    simulation_text = "Contains simulated inputs" if pack.contains_simulation else "No simulation"
    historical_text = (
        "All represented source sets are historical-replay eligible"
        if pack.source_set_historical_replay_eligible
        else "At least one represented source set is not historical-replay eligible"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'self'; img-src 'self' data:;
                 base-uri 'none'; form-action 'none'">
  <title>{html.escape(spec.title)} · FinReplay OS</title>
  <link rel="stylesheet" href="assets/styles.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to report</a>
  <header class="site-header">
    <div>
      <p class="eyebrow">FinReplay OS · evidence-labelled replay</p>
      <h1>{html.escape(spec.title)}</h1>
      <p class="lede">{html.escape(spec.claim_boundary)}</p>
    </div>
    <div class="identity" aria-label="Replay identity">
      <span>Replay <code>{html.escape(spec.replay_id)}</code></span>
      <span>Trace <code>{html.escape(pack.trace_id)}</code></span>
    </div>
  </header>
  <nav aria-label="Report sections">
    <a href="#summary">Summary</a><a href="#claims">Claims</a>
    <a href="#artifacts">Artifacts</a><a href="#limitations">Limitations</a>
  </nav>
  <main id="main">
    <section id="summary" aria-labelledby="summary-title">
      <h2 id="summary-title">Replay summary</h2>
      <div class="boundary" role="note" aria-label="Truth boundary">
        <strong>Truth boundary</strong>
        <span>{html.escape(simulation_text)}. {html.escape(historical_text)}.</span>
        <span>Historical replay is not live trading;
        modeled output is not realized performance.</span>
      </div>
      <dl class="facts">
        <div><dt>Scenario</dt><dd>{html.escape(spec.scenario_id)}
        v{html.escape(spec.scenario_version)}</dd></div>
        <div><dt>Mode</dt><dd>{html.escape(spec.mode.value)}</dd></div>
        <div><dt>Decision time</dt>
        <dd>{html.escape(_canonical_datetime(spec.decision_time))}</dd></div>
        <div><dt>Artifact status</dt><dd>{html.escape(spec.status.value)}</dd></div>
        <div><dt>Engines represented</dt><dd>{engine_count} / {len(EngineName)}</dd></div>
        <div><dt>Pack hash</dt><dd><code>{html.escape(pack.pack_sha256)}</code></dd></div>
      </dl>
      <h3>Evidence inventory</h3>
      <ul class="metrics" aria-label="Evidence counts">{evidence_cards}</ul>
      <p class="downloads"><a href="report.json">Machine-readable report</a>
      <a href="checksums.sha256">Portable checksums</a>
      <a href="README.md">Reproduction notes</a></p>
    </section>
    <section id="claims" aria-labelledby="claims-title">
      <h2 id="claims-title">Claim traceability</h2>
      <div class="table-wrap" tabindex="0" role="region" aria-label="Claim traceability table">
        <table><caption>Each statement retains its evidence label,
        supporting artifacts and boundary.</caption>
          <thead><tr><th scope="col">Claim</th><th scope="col">Statement</th>
          <th scope="col">Label</th><th scope="col">Support</th>
          <th scope="col">Boundary</th></tr></thead>
          <tbody>{claims}</tbody>
        </table>
      </div>
    </section>
    <section id="artifacts" aria-labelledby="artifacts-title">
      <h2 id="artifacts-title">Engine artifacts</h2>{artifacts}
    </section>
    <section id="limitations" aria-labelledby="limitations-title">
      <h2 id="limitations-title">Pack limitations</h2><ul>{limitations}</ul>
    </section>
  </main>
  <footer><p>Static read-only report. No brokerage connection,
  tracking script, or external request.</p></footer>
</body>
</html>
"""


def _render_artifact(artifact: ReplayArtifact) -> str:
    evidence = ", ".join(
        f"{kind.value}: {count}"
        for kind, count in sorted(artifact.evidence_counts.items(), key=lambda item: item[0].value)
    )
    sources = (
        "".join(f"<li><code>{html.escape(item)}</code></li>" for item in artifact.source_record_ids)
        or "<li>None declared</li>"
    )
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in artifact.limitations)
    payload = html.escape(
        json.dumps(artifact.payload, ensure_ascii=False, sort_keys=True, indent=2)
    )
    return f"""<article class="artifact">
  <header><div><p class="eyebrow">{html.escape(artifact.engine.value)}</p>
  <h3><code>{html.escape(artifact.artifact_id)}</code></h3></div>
  <span class="badge">{html.escape(artifact.status.value.upper())}</span></header>
  <p><strong>Evidence:</strong> {html.escape(evidence)}</p>
  <p><strong>Artifact hash:</strong> <code>{html.escape(artifact.artifact_sha256)}</code></p>
  <details><summary>Sources, limitations, and machine payload</summary>
    <h4>Source record IDs</h4><ul>{sources}</ul>
    <h4>Limitations</h4><ul>{limitations}</ul>
    <h4>Payload</h4><pre>{payload}</pre>
  </details>
</article>"""


def _render_readme(pack: CompiledReplayPack) -> str:
    spec = pack.spec
    return f"""# {spec.title}

- Replay ID: `{spec.replay_id}`
- Trace ID: `{pack.trace_id}`
- Pack SHA-256: `{pack.pack_sha256}`

## Truth boundary

{spec.claim_boundary}

This is a static research ReplayPack. Historical replay is not live trading, simulated output is
not realized performance, public data is not a client engagement, and hashes/tests are not
external validation.

## Verify

From a FinReplay OS checkout:

```bash
finreplay verify-replaypack /path/to/this/directory
```

`report.json` is the machine-readable artifact graph. `index.html` is the accessible read-only
report. `checksums.sha256` uses portable relative paths only.
"""


def _styles() -> str:
    return """:root {
  color-scheme: light dark;
  --bg: #08131f; --panel: #102235; --ink: #f4f8fc; --muted: #b7c6d6;
  --line: #36516c; --accent: #65d4c1; --warn: #ffd166;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.6 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--accent); }
a:focus-visible, summary:focus-visible, .table-wrap:focus-visible {
  outline: 3px solid var(--warn); outline-offset: 3px;
}
.skip-link { position: absolute; left: -9999px; }
.skip-link:focus {
  left: 1rem; top: 1rem; background: #fff; color: #000; padding: .75rem; z-index: 10;
}
.site-header, main, nav, footer { max-width: 1180px; margin: auto; padding: 1.5rem; }
.site-header {
  display: grid; grid-template-columns: minmax(0, 2fr) minmax(16rem, 1fr);
  gap: 2rem; padding-top: 4rem;
}
.eyebrow {
  text-transform: uppercase; letter-spacing: .12em; color: var(--accent);
  font-size: .78rem; font-weight: 800;
}
.lede { max-width: 75ch; color: var(--muted); }
.identity { display: flex; flex-direction: column; gap: .75rem; overflow-wrap: anywhere; }
.identity span, .boundary {
  border: 1px solid var(--line); border-radius: .7rem; padding: .8rem;
  background: var(--panel);
}
nav { display: flex; flex-wrap: wrap; gap: 1rem; border-block: 1px solid var(--line); }
section { padding: 2rem 0; border-bottom: 1px solid var(--line); }
.boundary { display: grid; gap: .35rem; border-left: 5px solid var(--warn); }
.facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.facts div, .metric, .artifact {
  background: var(--panel); border: 1px solid var(--line); border-radius: .7rem;
  padding: 1rem;
}
.facts dt { color: var(--muted); font-size: .85rem; }
.facts dd { margin: .35rem 0 0; overflow-wrap: anywhere; }
.metrics {
  display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: .75rem;
  list-style: none; padding: 0;
}
.metric { display: flex; flex-direction: column; }
.metric-value { font-size: 1.8rem; font-weight: 800; }
.downloads { display: flex; flex-wrap: wrap; gap: 1rem; }
.table-wrap { overflow: auto; }
table { border-collapse: collapse; min-width: 900px; width: 100%; }
caption { text-align: left; color: var(--muted); padding: .5rem 0; }
th, td {
  border: 1px solid var(--line); padding: .75rem; text-align: left; vertical-align: top;
}
.badge {
  display: inline-block; border: 1px solid var(--accent); border-radius: 999px;
  padding: .2rem .55rem; font-size: .75rem; font-weight: 800;
}
.evidence-simulated { border-color: var(--warn); }
.artifact { margin: 1rem 0; }
.artifact header { display: flex; justify-content: space-between; gap: 1rem; }
.artifact h3 { margin: .25rem 0; overflow-wrap: anywhere; }
details { border-top: 1px solid var(--line); padding-top: .75rem; }
summary { cursor: pointer; font-weight: 700; }
pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
code { overflow-wrap: anywhere; word-break: break-word; }
pre {
  white-space: pre-wrap; overflow-wrap: anywhere; background: #050d15;
  padding: 1rem; border-radius: .5rem;
}
footer { color: var(--muted); }
@media (max-width: 800px) {
  .site-header { grid-template-columns: 1fr; }
  .facts { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  main, .site-header, nav, footer { padding-inline: 1rem; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; }
}
"""


def _verify_checksums(
    root: Path,
    entries: dict[str, ReplayFileEntry],
) -> None:
    expected_paths = sorted(path for path in entries if path != "checksums.sha256")
    expected = "".join(f"{entries[path].sha256}  {path}\n" for path in expected_paths)
    actual = (root / "checksums.sha256").read_text()
    if actual != expected:
        raise ReplayStudioError("portable checksums do not match receipt files")


def _verify_exact_file_set(root: Path, expected: dict[str, bytes]) -> None:
    actual_paths = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual_paths != set(expected):
        raise ReplayPackMutationError("existing ReplayPack file set differs")
    for relative_path, content in expected.items():
        if (root / relative_path).read_bytes() != content:
            raise ReplayPackMutationError(f"existing ReplayPack bytes differ: {relative_path}")


def _safe_destination(destination: Path) -> Path:
    expanded = destination.expanduser()
    if expanded.is_symlink():
        raise ReplayStudioError("ReplayPack destination cannot be a symlink")
    destination = expanded.resolve()
    if destination == Path(destination.anchor):
        raise ReplayStudioError("ReplayPack destination cannot be a filesystem root")
    return destination


def _safe_relative_path(value: str) -> Path:
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("ReplayPack paths must be canonical relative POSIX paths")
    return Path(*pure.parts)


def _fsync_tree(root: Path) -> None:
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _pretty_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
