from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, cast

REPOSITORY = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = REPOSITORY / "web/public/replaypacks"
MANIFEST = PUBLIC_ROOT / "manifest.json"


def test_public_replaypack_downloads_rebuild_byte_identically() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/build_public_replaypack_downloads.py", "--check"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "public_replaypacks_current=true scenarios=30" in completed.stdout


def test_public_replaypack_manifest_binds_every_archive() -> None:
    values = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    claimed_hash = values.pop("manifest_sha256")
    assert claimed_hash == _hash(values)
    assert values["scenario_count"] == 30
    assert len(values["bundles"]) == 30
    assert {path.name for path in PUBLIC_ROOT.glob("*.zip")} == {
        f"{entry['slug']}.zip" for entry in values["bundles"]
    }

    required_names = {
        "README.md",
        "assets/styles.css",
        "checksums.sha256",
        "index.html",
        "manifest.json",
        "report.json",
    }
    for entry in values["bundles"]:
        archive_path = PUBLIC_ROOT / f"{entry['slug']}.zip"
        content = archive_path.read_bytes()
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        with zipfile.ZipFile(archive_path) as archive:
            assert set(archive.namelist()) == required_names
            assert archive.comment.decode() == entry["pack_sha256"]


def _hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
