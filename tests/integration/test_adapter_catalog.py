from __future__ import annotations

import json
from pathlib import Path

from finreplay.adapters import (
    CFTC_COT_SPECS,
    FDIC_DATASET_SPECS,
    FISCAL_DATA_SPECS,
    NYFED_DATASET_SPECS,
    BLSCPIUAllItemsAdapter,
    FDICFinancialsAdapter,
    SECCompanyFactsAdapter,
    SECHistoricalSubmissionsAdapter,
    SECSubmissionsAdapter,
)


def test_thirty_declared_adapters_exactly_match_committed_live_inventory() -> None:
    declared = {
        FDICFinancialsAdapter.metadata.adapter_id,
        BLSCPIUAllItemsAdapter.metadata.adapter_id,
        SECSubmissionsAdapter.metadata.adapter_id,
        SECHistoricalSubmissionsAdapter.metadata.adapter_id,
        SECCompanyFactsAdapter.metadata.adapter_id,
        *(spec.adapter_id for spec in FDIC_DATASET_SPECS),
        *(spec.adapter_id for spec in FISCAL_DATA_SPECS),
        *(spec.adapter_id for spec in NYFED_DATASET_SPECS),
        *(spec.adapter_id for spec in CFTC_COT_SPECS),
    }
    repository = Path(__file__).resolve().parents[2]
    summary = json.loads((repository / "verification/live/latest-summary.json").read_text())
    inventory = {item["adapter_id"] for item in summary["adapters"]}

    assert len(declared) == 30
    assert summary["verified_adapter_count"] == 30
    assert inventory == declared
    assert summary["historical_replay_eligible_count"] == 3
    assert summary["latest_only_count"] == 23
    for item in summary["adapters"]:
        assert (repository / "verification/live" / item["receipt"]).is_file()
