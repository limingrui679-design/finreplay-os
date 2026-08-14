from __future__ import annotations

import json
from pathlib import Path

from finreplay.adapters import (
    CFTC_COT_SPECS,
    FDIC_DATASET_SPECS,
    FISCAL_DATA_SPECS,
    NYFED_DATASET_SPECS,
    ALFREDGDPVintageAdapter,
    ALFREDTreasuryYieldVintageAdapter,
    BEAPersonalIncomeOutlaysArchiveAdapter,
    BLSCPIArchiveAdapter,
    BLSCPIUAllItemsAdapter,
    BLSEmploymentSituationArchiveAdapter,
    CensusC30ArchiveAdapter,
    CensusHUDNRCArchiveAdapter,
    CensusMARTSArchiveAdapter,
    DOLWeeklyClaimsArchiveAdapter,
    EIAWPSRCommercialCrudeStocksAdapter,
    FDICFinancialsAdapter,
    FederalReserveFOMCStatementAdapter,
    FederalReserveG17ArchiveAdapter,
    FederalReserveG19ArchiveAdapter,
    FederalReserveH41BTFPAdapter,
    NYFedSOFRHistoricalAdapter,
    SECCompanyFactsAdapter,
    SECHistoricalSubmissionsAdapter,
    SECSubmissionsAdapter,
    TreasuryAuction91DayArchiveAdapter,
    TreasuryDTSPublishedReportAdapter,
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


def test_alfred_is_a_verified_supporting_source_not_a_thirty_first_counted_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/alfred"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert ALFREDGDPVintageAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert supporting["adapters"][0]["adapter_id"] == ALFREDGDPVintageAdapter.metadata.adapter_id
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_alfred_treasury_yields_are_supporting_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/alfred-treasury-yields"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert ALFREDTreasuryYieldVintageAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == ALFREDTreasuryYieldVintageAdapter.metadata.adapter_id
    )
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_h41_is_a_verified_supporting_source_not_a_thirty_first_counted_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/fed-h41"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert FederalReserveH41BTFPAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"] == FederalReserveH41BTFPAdapter.metadata.adapter_id
    )
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_bls_employment_is_supporting_source_not_a_thirty_first_counted_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/bls-employment"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert BLSEmploymentSituationArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == BLSEmploymentSituationArchiveAdapter.metadata.adapter_id
    )
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_bls_cpi_release_is_supporting_source_not_a_thirty_first_counted_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/bls-cpi"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert BLSCPIArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert supporting["adapters"][0]["adapter_id"] == BLSCPIArchiveAdapter.metadata.adapter_id
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_fomc_is_a_verified_supporting_source_not_a_thirty_first_counted_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/fed-fomc"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert FederalReserveFOMCStatementAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == FederalReserveFOMCStatementAdapter.metadata.adapter_id
    )
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_treasury_dts_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/treasury-dts"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert TreasuryDTSPublishedReportAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == TreasuryDTSPublishedReportAdapter.metadata.adapter_id
    )
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_sofr_history_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/nyfed-sofr-history"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert NYFedSOFRHistoricalAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert supporting["adapters"][0]["adapter_id"] == NYFedSOFRHistoricalAdapter.metadata.adapter_id
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_eia_wpsr_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/eia-wpsr"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert EIAWPSRCommercialCrudeStocksAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == EIAWPSRCommercialCrudeStocksAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_dol_ui_claims_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/dol-ui-claims"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert DOLWeeklyClaimsArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"] == DOLWeeklyClaimsArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_treasury_auction_results_are_supporting_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/treasury-auction-results"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert TreasuryAuction91DayArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == TreasuryAuction91DayArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_bea_pio_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/bea-pio"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert BEAPersonalIncomeOutlaysArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == BEAPersonalIncomeOutlaysArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert supporting["adapters"][0]["idempotent_records"] == 3
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_fed_g17_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/fed-g17"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert FederalReserveG17ArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == FederalReserveG17ArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert supporting["adapters"][0]["idempotent_records"] == 3
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_census_marts_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/census-marts"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert CensusMARTSArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == CensusMARTSArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert supporting["adapters"][0]["idempotent_records"] == 3
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_census_nrc_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/census-nrc"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert CensusHUDNRCArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == CensusHUDNRCArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 3
    assert supporting["adapters"][0]["idempotent_records"] == 3
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_census_c30_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/census-c30"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert CensusC30ArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == CensusC30ArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 6
    assert supporting["adapters"][0]["idempotent_records"] == 6
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()


def test_fed_g19_is_a_verified_supporting_source_not_a_thirty_first_adapter() -> None:
    repository = Path(__file__).resolve().parents[2]
    formal = json.loads((repository / "verification/live/latest-summary.json").read_text())
    supporting_root = repository / "verification/supporting/fed-g19"
    supporting = json.loads((supporting_root / "latest-summary.json").read_text())

    formal_ids = {item["adapter_id"] for item in formal["adapters"]}
    assert FederalReserveG19ArchiveAdapter.metadata.adapter_id not in formal_ids
    assert supporting["verified_adapter_count"] == 1
    assert supporting["historical_replay_eligible_count"] == 1
    assert supporting["latest_only_count"] == 0
    assert (
        supporting["adapters"][0]["adapter_id"]
        == FederalReserveG19ArchiveAdapter.metadata.adapter_id
    )
    assert supporting["adapters"][0]["record_count"] == 6
    assert supporting["adapters"][0]["idempotent_records"] == 6
    assert supporting["adapters"][0]["inserted_records"] == 0
    assert (supporting_root / "live" / supporting["adapters"][0]["receipt"]).is_file()
