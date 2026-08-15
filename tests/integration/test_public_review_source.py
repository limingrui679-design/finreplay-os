from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


def test_review_source_archive_excludes_prior_review_archives(tmp_path: Path) -> None:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    output = tmp_path / "review-source.zip"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_public_review_source.py",
            "--subject",
            revision,
            "--output",
            str(output),
        ],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"revision={revision}" in completed.stdout
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert archive.testzip() is None

    root = f"finreplay-os-{revision[:12]}/"
    assert names
    assert names[0] == root
    assert not any(
        name.startswith(f"{root}web/public/review/finreplay-os-")
        and name.endswith(".zip")
        for name in names
    )
