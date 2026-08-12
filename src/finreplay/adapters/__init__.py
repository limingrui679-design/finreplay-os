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
from finreplay.adapters.sec import SECHistoricalSubmissionsAdapter, SECSubmissionsAdapter
from finreplay.adapters.sec_xbrl import SECCompanyFactsAdapter

__all__ = [
    "FDIC_DATASET_BY_SLUG",
    "FDIC_DATASET_SPECS",
    "AdapterBatch",
    "AdapterError",
    "AdapterMetadata",
    "AuthenticationMode",
    "FDICDatasetAdapter",
    "FDICDatasetSpec",
    "FDICFinancialsAdapter",
    "FetchReceipt",
    "RawArtifact",
    "ResponseLimitError",
    "SECCompanyFactsAdapter",
    "SECHistoricalSubmissionsAdapter",
    "SECSubmissionsAdapter",
    "SourceSchemaError",
]
