from __future__ import annotations

from pathlib import Path

import pytest

from finreplay.api import build_replaypack, load_verified_replaypack, verify_replaypack
from finreplay.engines import CompiledReplayPack, ReplayPackSpec, ReplayStudioError

REPOSITORY = Path(__file__).resolve().parents[2]
GOLDEN_REPORT = REPOSITORY / "verification/replaypacks/replaystudio-golden/report.json"


def test_python_api_builds_archives_loads_and_verifies(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    archive = tmp_path / "pack.zip"
    spec = golden_spec()
    result = build_replaypack(spec, root, archive=archive)
    receipt = verify_replaypack(root)
    report = load_verified_replaypack(root)

    assert result.receipt == receipt
    assert archive.is_file()
    assert report.pack_sha256 == receipt.pack_sha256
    assert report.spec.replay_id == spec.replay_id


def test_python_api_fails_closed_on_tampered_pack(tmp_path: Path) -> None:
    root = build_replaypack(golden_spec(), tmp_path / "pack").root
    (root / "report.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ReplayStudioError, match="hash mismatch"):
        load_verified_replaypack(root)


def golden_spec() -> ReplayPackSpec:
    return CompiledReplayPack.model_validate_json(
        GOLDEN_REPORT.read_text(encoding="utf-8")
    ).spec
