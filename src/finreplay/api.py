"""Small, typed programmatic API for deterministic ReplayPack operations."""

from __future__ import annotations

from pathlib import Path

from finreplay.engines import (
    CompiledReplayPack,
    ReplayBuildResult,
    ReplayPackReceipt,
    ReplayPackSpec,
    ReplayStudio,
)

__all__ = [
    "build_replaypack",
    "load_verified_replaypack",
    "verify_replaypack",
]


def build_replaypack(
    spec: ReplayPackSpec,
    destination: Path,
    *,
    archive: Path | None = None,
) -> ReplayBuildResult:
    """Build a deterministic ReplayPack and optionally write its deterministic ZIP."""

    studio = ReplayStudio()
    result = studio.build(spec, destination)
    if archive is not None:
        studio.archive(result.root, archive)
    return result


def verify_replaypack(root: Path) -> ReplayPackReceipt:
    """Fail closed unless every file, hash, semantic invariant, and render is valid."""

    return ReplayStudio().verify(root)


def load_verified_replaypack(root: Path) -> CompiledReplayPack:
    """Return the typed machine report only after full directory verification."""

    root = root.expanduser().resolve()
    receipt = verify_replaypack(root)
    report = CompiledReplayPack.model_validate_json(
        (root / "report.json").read_text(encoding="utf-8")
    )
    if report.pack_sha256 != receipt.pack_sha256:
        raise ValueError("verified manifest and machine report identify different ReplayPacks")
    return report
