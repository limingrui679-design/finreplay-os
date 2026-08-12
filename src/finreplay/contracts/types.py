"""Controlled vocabularies that prevent evidence and completion-status inflation."""

from enum import StrEnum


class EvidenceClass(StrEnum):
    """How directly a fact or relationship is supported."""

    OBSERVED = "observed"
    REPORTED = "reported"
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    SIMULATED = "simulated"


class ArtifactStatus(StrEnum):
    """Evidence maturity for data, adapters, scenarios, and releases."""

    PLANNED = "planned"
    CONTRACT_VALIDATED = "contract_validated"
    FIXTURE_VALIDATED = "fixture_validated"
    LIVE_VALIDATED = "live_validated"
    REPRODUCED = "reproduced"
    EXTERNALLY_VALIDATED = "externally_validated"


class LicenseClass(StrEnum):
    """Machine-readable redistribution boundary."""

    REDISTRIBUTABLE = "redistributable"
    DOWNLOAD_ONLY = "download_only"
    DERIVED_ONLY = "derived_only"
    BYO = "bring_your_own"
    REVIEW_REQUIRED = "review_required"


class TemporalCoverage(StrEnum):
    """What an upstream artifact can honestly establish about historical knowledge."""

    VINTAGE_NATIVE = "vintage_native"
    VERSIONED_SNAPSHOT = "versioned_snapshot"
    IMMUTABLE_EVENT = "immutable_event"
    LATEST_ONLY = "latest_only"


class ScenarioMode(StrEnum):
    """Relationship between a replay result and observed history."""

    OBSERVED_RECONSTRUCTION = "observed_reconstruction"
    BOUNDED_RECONSTRUCTION = "bounded_reconstruction"
    COUNTERFACTUAL = "counterfactual"
    ADVERSARIAL = "adversarial"


class TrialDisposition(StrEnum):
    """Allowed outcome of the adversarial research gate."""

    REJECT = "reject"
    REVISE = "revise"
    SHADOW = "shadow"
    LIMITED_RISK_TRIAL = "limited_risk_trial"
    ELIGIBLE = "eligible"
