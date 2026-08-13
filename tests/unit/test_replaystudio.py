from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from finreplay.cli import app
from finreplay.contracts import ArtifactStatus, EvidenceClass, ScenarioMode
from finreplay.engines import (
    CompiledReplayPack,
    EngineName,
    ReplayArtifact,
    ReplayClaim,
    ReplayFileEntry,
    ReplayPackMutationError,
    ReplayPackReceipt,
    ReplayPackSpec,
    ReplayStudio,
    ReplayStudioError,
)

DECISION = datetime(2023, 3, 8, 15, tzinfo=UTC)
CREATED = DECISION + timedelta(hours=1)


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def artifact(
    engine: EngineName,
    *,
    upstream: tuple[str, ...] = (),
    evidence_class: EvidenceClass = EvidenceClass.SIMULATED,
    sourced: bool = False,
    artifact_id: str | None = None,
) -> ReplayArtifact:
    resolved_id = artifact_id or f"artifact-{engine.value}"
    source_ids = ("official:test:record-2", "official:test:record-1") if sourced else ()
    source_hashes = ("2" * 64, "1" * 64) if sourced else ()
    return ReplayArtifact.create(
        artifact_id=resolved_id,
        engine=engine,
        artifact_kind="fixture-result",
        status=ArtifactStatus.FIXTURE_VALIDATED,
        evidence_counts={evidence_class: 1},
        source_set_historical_replay_eligible=sourced,
        source_record_ids=source_ids,
        source_hashes=source_hashes,
        upstream_artifact_ids=upstream,
        payload={"message": "<script>alert('fixture')</script>", "value": 1},
        limitations=("Deterministic synthetic fixture; not realized performance.",),
    )


def seven_artifacts() -> tuple[ReplayArtifact, ...]:
    timevault = artifact(
        EngineName.TIMEVAULT,
        evidence_class=EvidenceClass.OBSERVED,
        sourced=True,
    )
    trialcourt = artifact(
        EngineName.TRIALCOURT,
        upstream=(timevault.artifact_id,),
        evidence_class=EvidenceClass.INFERRED,
    )
    markettwin = artifact(
        EngineName.MARKETTWIN,
        upstream=(timevault.artifact_id,),
    )
    shockcompiler = artifact(
        EngineName.SHOCKCOMPILER,
        upstream=(markettwin.artifact_id, trialcourt.artifact_id),
    )
    executionlab = artifact(
        EngineName.EXECUTIONLAB,
        upstream=(shockcompiler.artifact_id,),
    )
    capitalallocator = artifact(
        EngineName.CAPITALALLOCATOR,
        upstream=(executionlab.artifact_id, trialcourt.artifact_id),
    )
    replaystudio = artifact(
        EngineName.REPLAYSTUDIO,
        upstream=(capitalallocator.artifact_id, shockcompiler.artifact_id),
    )
    return (
        replaystudio,
        capitalallocator,
        executionlab,
        shockcompiler,
        markettwin,
        trialcourt,
        timevault,
    )


def claims() -> tuple[ReplayClaim, ...]:
    return (
        ReplayClaim(
            claim_id="claim-simulated",
            statement="A <script>alert('claim')</script> value is a simulated fixture.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=("artifact-replaystudio",),
            boundary="This statement describes a deterministic fixture, not realized performance.",
            limitations=("It is not evidence of a live deployment.",),
        ),
        ReplayClaim(
            claim_id="claim-observed",
            statement="Two content-addressed source records are represented in the fixture.",
            evidence_class=EvidenceClass.OBSERVED,
            support_artifact_ids=("artifact-timevault",),
            boundary=(
                "Observed means fixture source bytes were represented, not externally reviewed."
            ),
            limitations=("The publisher in this unit test is synthetic.",),
        ),
    )


def replay_spec(
    *,
    artifacts: tuple[ReplayArtifact, ...] | None = None,
    replay_claims: tuple[ReplayClaim, ...] | None = None,
    require_all_engines: bool = True,
    replay_id: str = "replay-seven-engine-fixture",
) -> ReplayPackSpec:
    return ReplayPackSpec(
        replay_id=replay_id,
        scenario_id="scenario-seven-engine-fixture",
        scenario_version="1.0.0",
        title="Seven-engine <script>fixture</script> replay",
        mode=ScenarioMode.BOUNDED_RECONSTRUCTION,
        decision_time=DECISION,
        created_at=CREATED,
        code_commit="uncommitted",
        status=ArtifactStatus.FIXTURE_VALIDATED,
        artifacts=artifacts or seven_artifacts(),
        claims=replay_claims or claims(),
        require_all_engines=require_all_engines,
        distinct_input_records=2,
        derived_records=7,
        compressed_input_bytes=128,
        elapsed_seconds=0.25,
        claim_boundary=(
            "Synthetic fixture evidence demonstrates deterministic packaging only; "
            "it is not historical performance or external validation."
        ),
        limitations=("All numerical payloads in this pack are synthetic fixtures.",),
    )


class AccessibilityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append((tag, dict(attrs)))


def test_artifact_factory_is_content_addressed_and_canonical() -> None:
    first = artifact(EngineName.TIMEVAULT, sourced=True, evidence_class=EvidenceClass.OBSERVED)
    second = artifact(EngineName.TIMEVAULT, sourced=True, evidence_class=EvidenceClass.OBSERVED)

    assert first == second
    assert first.source_record_ids == tuple(sorted(first.source_record_ids))
    assert first.source_hashes == tuple(sorted(first.source_hashes))

    changed = first.model_dump(mode="json")
    changed["payload"]["value"] = 2
    with pytest.raises(ValidationError, match="artifact_sha256"):
        ReplayArtifact.model_validate(changed)


def test_artifact_and_claim_reject_ambiguous_evidence_or_order() -> None:
    with pytest.raises(ValidationError, match="require source record IDs"):
        artifact(EngineName.TIMEVAULT, evidence_class=EvidenceClass.OBSERVED)

    with pytest.raises(ValidationError, match="sorted"):
        ReplayClaim(
            claim_id="claim-unsorted",
            statement="This claim deliberately has noncanonical support ordering.",
            evidence_class=EvidenceClass.SIMULATED,
            support_artifact_ids=("artifact-z", "artifact-a"),
            boundary="This is a validation-only fixture with no substantive claim.",
            limitations=("Validation fixture only.",),
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("negative_count", "cannot be negative"),
        ("zero_count", "count at least one"),
        ("blank_source_id", "must be non-empty"),
        ("duplicate_source_id", "must be unique"),
        ("unsorted_source_id", "must be sorted"),
        ("invalid_source_hash", "lowercase SHA-256"),
        ("duplicate_source_hash", "must be unique"),
        ("unsorted_source_hash", "must be sorted"),
        ("duplicate_upstream", "must be unique"),
        ("unsorted_upstream", "must be sorted"),
        ("self_dependency", "depend on itself"),
        ("eligible_without_hash", "eligibility requires"),
        ("blank_limitation", "must be non-empty"),
        ("nonfinite_payload", "Out of range float values"),
    ],
)
def test_artifact_rejects_each_noncanonical_invariant(case: str, message: str) -> None:
    base = ReplayArtifact.create(
        artifact_id="artifact-validation",
        engine=EngineName.TIMEVAULT,
        artifact_kind="validation-fixture",
        status=ArtifactStatus.FIXTURE_VALIDATED,
        evidence_counts={EvidenceClass.SIMULATED: 1},
        source_set_historical_replay_eligible=False,
        source_record_ids=("source-a", "source-b"),
        source_hashes=("1" * 64, "2" * 64),
        upstream_artifact_ids=("artifact-a", "artifact-b"),
        payload={"value": 1},
        limitations=("Validation fixture only.",),
    )
    values = base.model_dump(mode="json")
    if case == "negative_count":
        values["evidence_counts"] = {"simulated": -1}
    elif case == "zero_count":
        values["evidence_counts"] = {"simulated": 0}
    elif case == "blank_source_id":
        values["source_record_ids"] = ["", "source-b"]
    elif case == "duplicate_source_id":
        values["source_record_ids"] = ["source-a", "source-a"]
    elif case == "unsorted_source_id":
        values["source_record_ids"] = ["source-b", "source-a"]
    elif case == "invalid_source_hash":
        values["source_hashes"] = ["1" * 64, "g" * 64]
    elif case == "duplicate_source_hash":
        values["source_hashes"] = ["1" * 64, "1" * 64]
    elif case == "unsorted_source_hash":
        values["source_hashes"] = ["2" * 64, "1" * 64]
    elif case == "duplicate_upstream":
        values["upstream_artifact_ids"] = ["artifact-a", "artifact-a"]
    elif case == "unsorted_upstream":
        values["upstream_artifact_ids"] = ["artifact-b", "artifact-a"]
    elif case == "self_dependency":
        values["upstream_artifact_ids"] = [
            "artifact-a",
            "artifact-b",
            "artifact-validation",
        ]
    elif case == "eligible_without_hash":
        values["source_record_ids"] = []
        values["source_hashes"] = []
        values["source_set_historical_replay_eligible"] = True
    elif case == "blank_limitation":
        values["limitations"] = [""]
    elif case == "nonfinite_payload":
        values["payload"] = {"value": float("nan")}
    with pytest.raises(ValidationError, match=message):
        ReplayArtifact.model_validate(values)


def test_claim_rejects_duplicate_support_and_blank_limitations() -> None:
    common: dict[str, object] = {
        "claim_id": "claim-validation",
        "statement": "This is a claim-contract validation fixture.",
        "evidence_class": EvidenceClass.SIMULATED,
        "boundary": "This fixture makes no empirical or performance claim.",
        "limitations": ("Validation fixture only.",),
    }
    with pytest.raises(ValidationError, match="must be unique"):
        ReplayClaim.model_validate(
            {
                **common,
                "support_artifact_ids": ("artifact-a", "artifact-a"),
            }
        )
    with pytest.raises(ValidationError, match="must be non-empty"):
        ReplayClaim.model_validate(
            {
                **common,
                "limitations": ("",),
                "support_artifact_ids": ("artifact-a",),
            }
        )


def test_spec_rejects_missing_dependencies_cycles_and_bad_claim_support() -> None:
    missing = artifact(
        EngineName.TIMEVAULT,
        upstream=("artifact-not-present",),
        artifact_id="artifact-missing-dependency",
    )
    with pytest.raises(ValidationError, match="missing upstream IDs"):
        replay_spec(
            artifacts=(missing,),
            replay_claims=(
                ReplayClaim(
                    claim_id="claim-missing-dependency",
                    statement="A simulated validation fixture exists for a missing dependency.",
                    evidence_class=EvidenceClass.SIMULATED,
                    support_artifact_ids=(missing.artifact_id,),
                    boundary="The fixture is intentionally invalid and proves no result.",
                    limitations=("Validation fixture only.",),
                ),
            ),
            require_all_engines=False,
        )

    left = artifact(
        EngineName.TIMEVAULT,
        upstream=("artifact-cycle-right",),
        artifact_id="artifact-cycle-left",
    )
    right = artifact(
        EngineName.TRIALCOURT,
        upstream=("artifact-cycle-left",),
        artifact_id="artifact-cycle-right",
    )
    with pytest.raises(ValidationError, match="cycle"):
        replay_spec(
            artifacts=(left, right),
            replay_claims=(
                ReplayClaim(
                    claim_id="claim-cycle",
                    statement="A simulated validation graph deliberately contains a cycle.",
                    evidence_class=EvidenceClass.SIMULATED,
                    support_artifact_ids=(left.artifact_id,),
                    boundary="The graph is intentionally invalid and proves no result.",
                    limitations=("Validation fixture only.",),
                ),
            ),
            require_all_engines=False,
        )

    only = artifact(EngineName.TIMEVAULT, sourced=True, evidence_class=EvidenceClass.OBSERVED)
    bad_claim = ReplayClaim(
        claim_id="claim-wrong-label",
        statement="This inferred claim is unsupported by an inferred artifact.",
        evidence_class=EvidenceClass.INFERRED,
        support_artifact_ids=(only.artifact_id,),
        boundary="The claim deliberately declares the wrong evidence class.",
        limitations=("Validation fixture only.",),
    )
    with pytest.raises(ValidationError, match="no support"):
        replay_spec(
            artifacts=(only,),
            replay_claims=(bad_claim,),
            require_all_engines=False,
        )


def test_spec_requires_every_engine_when_complete_pack_is_requested() -> None:
    only = artifact(EngineName.TIMEVAULT, sourced=True, evidence_class=EvidenceClass.OBSERVED)
    one_claim = ReplayClaim(
        claim_id="claim-one-engine",
        statement="A single observed fixture artifact is present in this pack.",
        evidence_class=EvidenceClass.OBSERVED,
        support_artifact_ids=(only.artifact_id,),
        boundary="This statement concerns fixture presence only, not analytical quality.",
        limitations=("It does not constitute a complete ReplayPack.",),
    )
    with pytest.raises(ValidationError, match="requires every engine"):
        replay_spec(artifacts=(only,), replay_claims=(one_claim,))


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("naive_decision", "decision_time must be timezone-aware"),
        ("naive_created", "created_at must be timezone-aware"),
        ("created_before_decision", "must not precede"),
        ("infinite_elapsed", "must be finite"),
        ("duplicate_artifact", "artifact IDs must be unique"),
        ("duplicate_claim", "claim IDs must be unique"),
        ("missing_claim_support", "missing support artifacts"),
        ("blank_limitation", "limitations must be non-empty"),
    ],
)
def test_spec_rejects_timeline_identity_and_boundary_invariants(
    case: str,
    message: str,
) -> None:
    values = replay_spec().model_dump(mode="python")
    if case == "naive_decision":
        values["decision_time"] = DECISION.replace(tzinfo=None)
    elif case == "naive_created":
        values["created_at"] = CREATED.replace(tzinfo=None)
    elif case == "created_before_decision":
        values["created_at"] = DECISION - timedelta(seconds=1)
    elif case == "infinite_elapsed":
        values["elapsed_seconds"] = float("inf")
    elif case == "duplicate_artifact":
        values["artifacts"] = [*values["artifacts"], values["artifacts"][0]]
    elif case == "duplicate_claim":
        values["claims"] = [*values["claims"], values["claims"][0]]
    elif case == "missing_claim_support":
        values["claims"][0]["support_artifact_ids"] = ["artifact-not-present"]
    elif case == "blank_limitation":
        values["limitations"] = [""]
    with pytest.raises(ValidationError, match=message):
        ReplayPackSpec.model_validate(values)


def test_compile_is_deterministic_and_canonical_across_input_order() -> None:
    studio = ReplayStudio()
    first_spec = replay_spec()
    second_spec = replay_spec(
        artifacts=tuple(reversed(first_spec.artifacts)),
        replay_claims=tuple(reversed(first_spec.claims)),
    )

    first = studio.compile(first_spec)
    second = studio.compile(second_spec)

    assert first == second
    assert first.pack_sha256 == second.pack_sha256
    assert first.trace_id == second.trace_id
    assert tuple(item.artifact_id for item in first.spec.artifacts) == tuple(
        sorted(item.artifact_id for item in first.spec.artifacts)
    )
    assert set(first.engine_artifact_counts) == set(EngineName)
    assert first.topological_artifact_ids[0] == "artifact-timevault"
    assert first.topological_artifact_ids[-1] == "artifact-replaystudio"
    assert first.source_set_historical_replay_eligible is True
    assert first.contains_simulation is True


def test_compiled_pack_rejects_derived_or_canonical_order_tampering() -> None:
    compiled = ReplayStudio().compile(replay_spec())
    changed = compiled.model_dump(mode="json")
    changed["trace_id"] = f"trace:{'0' * 64}"
    changed["pack_sha256"] = canonical_hash(
        {key: value for key, value in changed.items() if key != "pack_sha256"}
    )
    with pytest.raises(ValidationError, match="trace_id"):
        CompiledReplayPack.model_validate(changed)

    unsorted = compiled.model_dump(mode="json")
    unsorted["spec"]["artifacts"] = list(reversed(unsorted["spec"]["artifacts"]))
    unsorted["pack_sha256"] = canonical_hash(
        {key: value for key, value in unsorted.items() if key != "pack_sha256"}
    )
    with pytest.raises(ValidationError, match="canonically sorted"):
        CompiledReplayPack.model_validate(unsorted)

    unsorted_claims = compiled.model_dump(mode="json")
    unsorted_claims["spec"]["claims"] = list(reversed(unsorted_claims["spec"]["claims"]))
    unsorted_claims["pack_sha256"] = canonical_hash(
        {key: value for key, value in unsorted_claims.items() if key != "pack_sha256"}
    )
    with pytest.raises(ValidationError, match="claims must be canonically sorted"):
        CompiledReplayPack.model_validate(unsorted_claims)

    wrong_pack_hash = compiled.model_dump(mode="json")
    wrong_pack_hash["pack_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="pack_sha256"):
        CompiledReplayPack.model_validate(wrong_pack_hash)


def test_build_verify_and_idempotence_use_fixed_portable_files(tmp_path: Path) -> None:
    studio = ReplayStudio()
    root = tmp_path / "pack"
    first = studio.build(replay_spec(), root)
    second = studio.build(replay_spec(), root)

    assert first.idempotent is False
    assert second.idempotent is True
    assert first.receipt == second.receipt == studio.verify(root)
    assert {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()} == {
        "README.md",
        "assets/styles.css",
        "checksums.sha256",
        "index.html",
        "manifest.json",
        "report.json",
    }
    checksum_text = (root / "checksums.sha256").read_text()
    assert str(tmp_path) not in checksum_text
    assert all(not line.split("  ", 1)[1].startswith("/") for line in checksum_text.splitlines())


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("duplicate_file", "file paths must be unique"),
        ("unsorted_files", "files must be canonically sorted"),
        ("missing_file", "fixed portable file set"),
        ("wrong_output_hash", "output manifest hash"),
        ("wrong_report_hash", "report hash"),
        ("wrong_receipt_hash", "receipt_sha256"),
    ],
)
def test_receipt_rejects_noncanonical_or_inconsistent_content(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    root = ReplayStudio().build(replay_spec(), tmp_path / case).root
    values = json.loads((root / "manifest.json").read_text())
    if case == "duplicate_file":
        values["files"].append(values["files"][0])
    elif case == "unsorted_files":
        values["files"] = list(reversed(values["files"]))
    elif case == "missing_file":
        values["files"] = [
            entry for entry in values["files"] if entry["relative_path"] != "README.md"
        ]
    elif case == "wrong_output_hash":
        values["replay_manifest"]["output_manifest_sha256"] = "0" * 64
    elif case == "wrong_report_hash":
        values["report_sha256"] = "0" * 64
    elif case == "wrong_receipt_hash":
        values["receipt_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match=message):
        ReplayPackReceipt.model_validate(values)


def test_file_entry_cannot_list_manifest_itself() -> None:
    with pytest.raises(ValidationError, match="cannot include itself"):
        ReplayFileEntry(
            relative_path="manifest.json",
            bytes=1,
            sha256="0" * 64,
            media_type="application/json",
            role="machine-manifest",
        )


def test_two_builds_are_byte_identical_and_mutation_is_refused(tmp_path: Path) -> None:
    studio = ReplayStudio()
    first_root = studio.build(replay_spec(), tmp_path / "first").root
    second_root = studio.build(replay_spec(), tmp_path / "second").root

    first_files = {
        path.relative_to(first_root).as_posix(): path.read_bytes()
        for path in first_root.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second_root).as_posix(): path.read_bytes()
        for path in second_root.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files

    with pytest.raises(ReplayPackMutationError, match="different ReplayPack"):
        studio.build(replay_spec(replay_id="replay-different-fixture"), first_root)


@pytest.mark.parametrize("target", ["index.html", "report.json", "checksums.sha256"])
def test_verify_rejects_file_tampering(tmp_path: Path, target: str) -> None:
    studio = ReplayStudio()
    root = studio.build(replay_spec(), tmp_path / "pack").root
    with (root / target).open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(ReplayStudioError, match="hash mismatch"):
        studio.verify(root)


def test_verify_rejects_rehashed_nondeterministic_render(tmp_path: Path) -> None:
    studio = ReplayStudio()
    root = studio.build(replay_spec(), tmp_path / "pack").root
    index_path = root / "index.html"
    changed_index = index_path.read_bytes().replace(b"Replay summary", b"Altered summary")
    index_path.write_bytes(changed_index)

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    index_entry = next(
        entry for entry in manifest["files"] if entry["relative_path"] == "index.html"
    )
    index_entry["bytes"] = len(changed_index)
    index_entry["sha256"] = hashlib.sha256(changed_index).hexdigest()
    manifest["replay_manifest"]["output_manifest_sha256"] = canonical_hash(manifest["files"])
    manifest["receipt_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "receipt_sha256"}
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))

    with pytest.raises(ReplayStudioError, match="deterministic render"):
        studio.verify(root)


def test_verify_rejects_extra_files_directories_and_symlinks(tmp_path: Path) -> None:
    studio = ReplayStudio()

    extra_file_root = studio.build(replay_spec(), tmp_path / "extra-file").root
    (extra_file_root / "extra.txt").write_text("extra")
    with pytest.raises(ReplayStudioError, match="unlisted files"):
        studio.verify(extra_file_root)

    extra_directory_root = studio.build(replay_spec(), tmp_path / "extra-directory").root
    (extra_directory_root / "empty").mkdir()
    with pytest.raises(ReplayStudioError, match="unlisted directories"):
        studio.verify(extra_directory_root)

    symlink_root = studio.build(replay_spec(), tmp_path / "symlink").root
    os.symlink(symlink_root / "report.json", symlink_root / "report-link.json")
    with pytest.raises(ReplayStudioError, match="symlinks"):
        studio.verify(symlink_root)


def test_root_and_destination_symlinks_are_rejected(tmp_path: Path) -> None:
    studio = ReplayStudio()
    root = studio.build(replay_spec(), tmp_path / "pack").root
    root_link = tmp_path / "pack-link"
    os.symlink(root, root_link)
    with pytest.raises(ReplayStudioError, match="root cannot be a symlink"):
        studio.verify(root_link)

    destination_target = tmp_path / "destination-target"
    destination_target.mkdir()
    destination_link = tmp_path / "destination-link"
    os.symlink(destination_target, destination_link)
    with pytest.raises(ReplayStudioError, match="destination cannot be a symlink"):
        studio.build(replay_spec(), destination_link)


def test_verify_and_archive_reject_invalid_destinations(tmp_path: Path) -> None:
    studio = ReplayStudio()
    with pytest.raises(ReplayStudioError, match="existing non-root directory"):
        studio.verify(tmp_path / "missing")

    invalid_manifest = tmp_path / "invalid-manifest"
    invalid_manifest.mkdir()
    (invalid_manifest / "manifest.json").write_text("not-json")
    with pytest.raises(ReplayStudioError, match="invalid ReplayPack manifest"):
        studio.verify(invalid_manifest)

    root = studio.build(replay_spec(), tmp_path / "pack").root
    with pytest.raises(ReplayStudioError, match=r"non-root \.zip"):
        studio.archive(root, tmp_path / "archive.tar")
    with pytest.raises(ReplayStudioError, match="inside the ReplayPack"):
        studio.archive(root, root / "inside.zip")

    archive_target = tmp_path / "archive-target.zip"
    archive_target.write_bytes(b"target")
    archive_link = tmp_path / "archive-link.zip"
    os.symlink(archive_target, archive_link)
    with pytest.raises(ReplayStudioError, match="destination cannot be a symlink"):
        studio.archive(root, archive_link)


def test_verify_rejects_manifest_report_identity_tampering(tmp_path: Path) -> None:
    studio = ReplayStudio()
    root = studio.build(replay_spec(), tmp_path / "pack").root
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["trace_id"] = f"trace:{'0' * 64}"
    manifest["receipt_sha256"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "receipt_sha256"}
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))

    with pytest.raises(ReplayStudioError, match="identity mismatch"):
        studio.verify(root)


def test_html_is_escaped_static_and_has_accessibility_landmarks(tmp_path: Path) -> None:
    root = ReplayStudio().build(replay_spec(), tmp_path / "pack").root
    rendered = (root / "index.html").read_text()
    parser = AccessibilityParser()
    parser.feed(rendered)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert not any(tag == "script" for tag, _ in parser.tags)
    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in parser.tags)
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.tags)
    assert any(tag == "caption" for tag, _ in parser.tags)
    assert any(tag == "th" and attrs.get("scope") == "col" for tag, attrs in parser.tags)
    assert any(
        tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
        for tag, attrs in parser.tags
    )
    assert "Skip to report" in rendered
    assert "No brokerage connection" in rendered
    styles = (root / "assets/styles.css").read_text()
    assert "code { overflow-wrap: anywhere; word-break: break-word; }" in styles


def test_archive_is_deterministic_portable_and_immutable(tmp_path: Path) -> None:
    studio = ReplayStudio()
    first_root = studio.build(replay_spec(), tmp_path / "first-pack").root
    second_root = studio.build(replay_spec(), tmp_path / "second-pack").root
    first_zip = studio.archive(first_root, tmp_path / "first.zip")
    second_zip = studio.archive(second_root, tmp_path / "second.zip")

    assert first_zip.read_bytes() == second_zip.read_bytes()
    assert studio.archive(first_root, first_zip) == first_zip
    with zipfile.ZipFile(first_zip) as archive:
        assert archive.comment.decode() == studio.verify(first_root).pack_sha256
        assert set(archive.namelist()) == {
            "README.md",
            "assets/styles.css",
            "checksums.sha256",
            "index.html",
            "manifest.json",
            "report.json",
        }
        assert all(
            not name.startswith("/") and ".." not in Path(name).parts for name in archive.namelist()
        )

    different_zip = tmp_path / "different.zip"
    different_zip.write_bytes(b"different")
    with pytest.raises(ReplayPackMutationError, match="different bytes"):
        studio.archive(first_root, different_zip)


@pytest.mark.parametrize(
    "relative_path",
    ["/absolute.json", "../escape.json", "a//b.json", "a\\b.json", "./a.json"],
)
def test_file_entries_require_canonical_relative_posix_paths(relative_path: str) -> None:
    with pytest.raises(ValidationError, match="canonical relative POSIX"):
        ReplayFileEntry(
            relative_path=relative_path,
            bytes=1,
            sha256="0" * 64,
            media_type="application/json",
            role="machine-report",
        )


def test_cli_builds_archives_and_verifies_replaypack(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    root = tmp_path / "pack"
    archive_path = tmp_path / "pack.zip"
    spec_path.write_text(replay_spec().model_dump_json())
    runner = CliRunner()

    built = runner.invoke(
        app,
        [
            "build-replaypack",
            str(spec_path),
            str(root),
            "--archive",
            str(archive_path),
        ],
    )
    verified = runner.invoke(app, ["verify-replaypack", str(root)])

    assert built.exit_code == 0, built.output
    assert "idempotent=false" in built.stdout
    assert f"archive={archive_path}" in built.stdout
    assert archive_path.is_file()
    assert verified.exit_code == 0, verified.output
    assert "verified=true" in verified.stdout


def test_cli_rejects_invalid_spec_and_tampered_pack(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid_spec = tmp_path / "invalid.json"
    invalid_spec.write_text("{}")
    invalid = runner.invoke(
        app,
        ["build-replaypack", str(invalid_spec), str(tmp_path / "invalid-pack")],
    )
    assert invalid.exit_code != 0
    assert "valid ReplayPackSpec" in invalid.output

    root = ReplayStudio().build(replay_spec(), tmp_path / "pack").root
    (root / "index.html").write_text("tampered")
    tampered = runner.invoke(app, ["verify-replaypack", str(root)])
    assert tampered.exit_code == 1
    assert "verification failed" in tampered.output
