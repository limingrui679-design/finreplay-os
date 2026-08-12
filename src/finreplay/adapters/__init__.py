"""Official-source adapters and evidence receipts."""

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
from finreplay.adapters.sec import SECHistoricalSubmissionsAdapter, SECSubmissionsAdapter
from finreplay.adapters.sec_xbrl import SECCompanyFactsAdapter

__all__ = [
    "FDIC_DATASET_BY_SLUG",
    "FDIC_DATASET_SPECS",
    "FISCAL_DATA_BY_SLUG",
    "FISCAL_DATA_SPECS",
    "AdapterBatch",
    "AdapterError",
    "AdapterMetadata",
    "AuthenticationMode",
    "FDICDatasetAdapter",
    "FDICDatasetSpec",
    "FDICFinancialsAdapter",
    "FetchReceipt",
    "FiscalDataAdapter",
    "FiscalDataSemanticKind",
    "FiscalDataSpec",
    "RawArtifact",
    "ResponseLimitError",
    "SECCompanyFactsAdapter",
    "SECHistoricalSubmissionsAdapter",
    "SECSubmissionsAdapter",
    "SourceSchemaError",
]
