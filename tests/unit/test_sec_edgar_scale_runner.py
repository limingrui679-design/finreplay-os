from __future__ import annotations

import runpy
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from finreplay.scale import SECLogPartition


def test_runner_rejects_unreceipted_existing_archive(tmp_path: Path) -> None:
    runner = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/build_sec_edgar_log_lake.py")
    )
    build_partition = cast(Callable[..., Any], runner["_build_partition"])
    archive_directory = tmp_path / "archives"
    archive_directory.mkdir()
    (archive_directory / "log20120101.zip").write_bytes(b"unreceipted-local-bytes")

    with pytest.raises(ValueError, match="without its download receipt"):
        build_partition(
            _partition(),
            user_agent="FinReplay OS test contact test@example.com",
            archive_directory=archive_directory,
            parquet_directory=tmp_path / "parquet",
            download_receipt_directory=tmp_path / "downloads",
            partition_receipt_directory=tmp_path / "partitions",
            download_attempts=1,
            retry_delay_seconds=0.0,
        )

    assert (archive_directory / "log20120101.zip").read_bytes() == b"unreceipted-local-bytes"
    assert not (tmp_path / "downloads").exists()
    assert not (tmp_path / "parquet").exists()


def _partition() -> SECLogPartition:
    return SECLogPartition.model_validate(
        {
            "partition_date": date(2012, 1, 1),
            "listed_url": (
                "http://www.sec.gov/dera/data/Public-EDGAR-log-file-data/"
                "2012/Qtr1/log20120101.zip"
            ),
            "source_url": (
                "https://www.sec.gov/dera/data/Public-EDGAR-log-file-data/"
                "2012/Qtr1/log20120101.zip"
            ),
            "list_page_url": "https://www.sec.gov/files/edgar2012.html",
        }
    )
