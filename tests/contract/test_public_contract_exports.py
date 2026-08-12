from finreplay import contracts


def test_public_contract_exports_are_stable() -> None:
    assert set(contracts.__all__) == {
        "ArtifactStatus",
        "BitemporalInterval",
        "BitemporalRecord",
        "CostModel",
        "EdgeEvidence",
        "EvidenceClass",
        "LicenseClass",
        "ReplayPackManifest",
        "ScenarioMode",
        "ScenarioSpec",
        "SourceReference",
        "TemporalCoverage",
        "TrialDisposition",
        "TrialSpec",
    }
