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
from finreplay.adapters.bls import BLSCPIUAllItemsAdapter
from finreplay.adapters.bls_cpi_release import BLSCPIArchiveAdapter
from finreplay.adapters.bls_employment import BLSEmploymentSituationArchiveAdapter
from finreplay.adapters.cftc import (
    CFTC_COT_BY_SLUG,
    CFTC_COT_SPECS,
    CFTCCOTAdapter,
    CFTCCOTSpec,
    CFTCReportKind,
)
from finreplay.adapters.fdic import FDICFinancialsAdapter
from finreplay.adapters.fdic_catalog import (
    FDIC_DATASET_BY_SLUG,
    FDIC_DATASET_SPECS,
    FDICDatasetAdapter,
    FDICDatasetSpec,
)
from finreplay.adapters.fed_fomc import FederalReserveFOMCStatementAdapter
from finreplay.adapters.fed_h41 import FederalReserveH41BTFPAdapter
from finreplay.adapters.fiscaldata import (
    FISCAL_DATA_BY_SLUG,
    FISCAL_DATA_SPECS,
    FiscalDataAdapter,
    FiscalDataSemanticKind,
    FiscalDataSpec,
)
from finreplay.adapters.nyfed import (
    NYFED_DATASET_BY_SLUG,
    NYFED_DATASET_SPECS,
    NYFedDatasetSpec,
    NYFedMarketsAdapter,
    NYFedSemanticKind,
)
from finreplay.adapters.sec import SECHistoricalSubmissionsAdapter, SECSubmissionsAdapter
from finreplay.adapters.sec_xbrl import SECCompanyFactsAdapter

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
    "BLSCPIArchiveAdapter",
    "BLSCPIUAllItemsAdapter",
    "BLSEmploymentSituationArchiveAdapter",
    "CFTCCOTAdapter",
    "CFTCCOTSpec",
    "CFTCReportKind",
    "FDICDatasetAdapter",
    "FDICDatasetSpec",
    "FDICFinancialsAdapter",
    "FederalReserveFOMCStatementAdapter",
    "FederalReserveH41BTFPAdapter",
    "FetchReceipt",
    "FiscalDataAdapter",
    "FiscalDataSemanticKind",
    "FiscalDataSpec",
    "NYFedDatasetSpec",
    "NYFedMarketsAdapter",
    "NYFedSemanticKind",
    "RawArtifact",
    "ResponseLimitError",
    "SECCompanyFactsAdapter",
    "SECHistoricalSubmissionsAdapter",
    "SECSubmissionsAdapter",
    "SourceSchemaError",
]
