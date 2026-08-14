"""Official-source adapters and evidence receipts."""

from finreplay.adapters.alfred import ALFREDGDPVintageAdapter
from finreplay.adapters.alfred_treasury_yields import ALFREDTreasuryYieldVintageAdapter
from finreplay.adapters.base import (
    AdapterBatch,
    AdapterError,
    AdapterMetadata,
    AuthenticationMode,
    FetchReceipt,
    RawArtifact,
    ResponseLimitError,
    SourceSchemaError,
)
from finreplay.adapters.bea_pio import BEAPersonalIncomeOutlaysArchiveAdapter
from finreplay.adapters.bls import BLSCPIUAllItemsAdapter
from finreplay.adapters.bls_cpi_release import BLSCPIArchiveAdapter
from finreplay.adapters.bls_employment import BLSEmploymentSituationArchiveAdapter
from finreplay.adapters.bls_ppi import BLSPPIArchiveAdapter
from finreplay.adapters.census_c30 import CensusC30ArchiveAdapter
from finreplay.adapters.census_durable_goods import CensusDurableGoodsArchiveAdapter
from finreplay.adapters.census_ft900 import CensusBEAFT900ArchiveAdapter
from finreplay.adapters.census_marts import CensusMARTSArchiveAdapter
from finreplay.adapters.census_nrc import CensusHUDNRCArchiveAdapter
from finreplay.adapters.census_nrs import CensusHUDNRSArchiveAdapter
from finreplay.adapters.cftc import (
    CFTC_COT_BY_SLUG,
    CFTC_COT_SPECS,
    CFTCCOTAdapter,
    CFTCCOTSpec,
    CFTCReportKind,
)
from finreplay.adapters.dol_ui_claims import DOLWeeklyClaimsArchiveAdapter
from finreplay.adapters.eia_wngsr import EIAWNGSRWorkingGasHistoryAdapter
from finreplay.adapters.eia_wpsr import EIAWPSRCommercialCrudeStocksAdapter
from finreplay.adapters.fdic import FDICFinancialsAdapter
from finreplay.adapters.fdic_catalog import (
    FDIC_DATASET_BY_SLUG,
    FDIC_DATASET_SPECS,
    FDICDatasetAdapter,
    FDICDatasetSpec,
)
from finreplay.adapters.fed_fomc import FederalReserveFOMCStatementAdapter
from finreplay.adapters.fed_g17 import FederalReserveG17ArchiveAdapter
from finreplay.adapters.fed_g19 import FederalReserveG19ArchiveAdapter
from finreplay.adapters.fed_h41 import FederalReserveH41BTFPAdapter
from finreplay.adapters.fhfa_hpi import FHFAHPIArchiveAdapter
from finreplay.adapters.fiscaldata import (
    FISCAL_DATA_BY_SLUG,
    FISCAL_DATA_SPECS,
    FiscalDataAdapter,
    FiscalDataSemanticKind,
    FiscalDataSpec,
)
from finreplay.adapters.fiscaldata_dts_report import TreasuryDTSPublishedReportAdapter
from finreplay.adapters.nyfed import (
    NYFED_DATASET_BY_SLUG,
    NYFED_DATASET_SPECS,
    NYFedDatasetSpec,
    NYFedMarketsAdapter,
    NYFedSemanticKind,
)
from finreplay.adapters.nyfed_sofr_history import NYFedSOFRHistoricalAdapter
from finreplay.adapters.sec import SECHistoricalSubmissionsAdapter, SECSubmissionsAdapter
from finreplay.adapters.sec_xbrl import SECCompanyFactsAdapter
from finreplay.adapters.treasury_auction_results import TreasuryAuction91DayArchiveAdapter

__all__ = [
    "CFTC_COT_BY_SLUG",
    "CFTC_COT_SPECS",
    "FDIC_DATASET_BY_SLUG",
    "FDIC_DATASET_SPECS",
    "FISCAL_DATA_BY_SLUG",
    "FISCAL_DATA_SPECS",
    "NYFED_DATASET_BY_SLUG",
    "NYFED_DATASET_SPECS",
    "ALFREDGDPVintageAdapter",
    "ALFREDTreasuryYieldVintageAdapter",
    "AdapterBatch",
    "AdapterError",
    "AdapterMetadata",
    "AuthenticationMode",
    "BEAPersonalIncomeOutlaysArchiveAdapter",
    "BLSCPIArchiveAdapter",
    "BLSCPIUAllItemsAdapter",
    "BLSEmploymentSituationArchiveAdapter",
    "BLSPPIArchiveAdapter",
    "CFTCCOTAdapter",
    "CFTCCOTSpec",
    "CFTCReportKind",
    "CensusBEAFT900ArchiveAdapter",
    "CensusC30ArchiveAdapter",
    "CensusDurableGoodsArchiveAdapter",
    "CensusHUDNRCArchiveAdapter",
    "CensusHUDNRSArchiveAdapter",
    "CensusMARTSArchiveAdapter",
    "DOLWeeklyClaimsArchiveAdapter",
    "EIAWNGSRWorkingGasHistoryAdapter",
    "EIAWPSRCommercialCrudeStocksAdapter",
    "FDICDatasetAdapter",
    "FDICDatasetSpec",
    "FDICFinancialsAdapter",
    "FHFAHPIArchiveAdapter",
    "FederalReserveFOMCStatementAdapter",
    "FederalReserveG17ArchiveAdapter",
    "FederalReserveG19ArchiveAdapter",
    "FederalReserveH41BTFPAdapter",
    "FetchReceipt",
    "FiscalDataAdapter",
    "FiscalDataSemanticKind",
    "FiscalDataSpec",
    "NYFedDatasetSpec",
    "NYFedMarketsAdapter",
    "NYFedSOFRHistoricalAdapter",
    "NYFedSemanticKind",
    "RawArtifact",
    "ResponseLimitError",
    "SECCompanyFactsAdapter",
    "SECHistoricalSubmissionsAdapter",
    "SECSubmissionsAdapter",
    "SourceSchemaError",
    "TreasuryAuction91DayArchiveAdapter",
    "TreasuryDTSPublishedReportAdapter",
]
