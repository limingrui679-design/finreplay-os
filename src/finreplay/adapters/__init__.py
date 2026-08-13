"""Official-source adapters and evidence receipts."""

from finreplay.adapters.alfred import ALFREDGDPVintageAdapter
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
    "AdapterBatch",
    "AdapterError",
    "AdapterMetadata",
    "AuthenticationMode",
    "BLSCPIUAllItemsAdapter",
    "CFTCCOTAdapter",
    "CFTCCOTSpec",
    "CFTCReportKind",
    "FDICDatasetAdapter",
    "FDICDatasetSpec",
    "FDICFinancialsAdapter",
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
