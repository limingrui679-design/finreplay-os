from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import HttpUrl, ValidationError

from finreplay.contracts import EvidenceClass, LicenseClass, SourceReference, TemporalCoverage
from finreplay.engines import (
    GraphMutationError,
    MarketEdge,
    MarketNode,
    MarketTwin,
    NodeKind,
    TemporalEvidence,
)

DECISION = datetime(2023, 3, 8, 18, tzinfo=UTC)
VALID = datetime(2023, 3, 8, tzinfo=UTC)


def source(
    source_id: str = "sec.edgar.submissions",
    *,
    digit: str = "1",
    coverage: TemporalCoverage = TemporalCoverage.IMMUTABLE_EVENT,
    retrieved_at: datetime = datetime(2026, 8, 12, tzinfo=UTC),
) -> SourceReference:
    return SourceReference(
        source_id=source_id,
        publisher="Official Fixture Publisher",
        url=HttpUrl("https://example.gov/source"),
        retrieved_at=retrieved_at,
        source_version=f"fixture-{digit}",
        sha256=digit * 64,
        license_class=LicenseClass.REDISTRIBUTABLE,
        temporal_coverage=coverage,
        vintage_as_of=None if coverage is TemporalCoverage.LATEST_ONLY else retrieved_at,
        redistribution_note="Fixture only.",
    )


def temporal(
    *,
    available_at: datetime = datetime(2023, 3, 1, tzinfo=UTC),
    valid_from: datetime = VALID,
    valid_to: datetime | None = None,
) -> TemporalEvidence:
    return TemporalEvidence(
        valid_from=valid_from,
        valid_to=valid_to,
        available_at=available_at,
    )


def node(
    node_id: str,
    *,
    capacity: float = 100.0,
    src: SourceReference | None = None,
    evidence: EvidenceClass = EvidenceClass.REPORTED,
    when: TemporalEvidence | None = None,
) -> MarketNode:
    return MarketNode(
        node_id=node_id,
        label=node_id.upper(),
        kind=NodeKind.BANK,
        loss_absorption_usd=capacity,
        evidence_class=evidence,
        temporal=when or temporal(),
        source=src or source(),
    )


def edge(
    edge_id: str,
    source_node: str,
    target_node: str,
    *,
    lower: float = 10.0,
    upper: float = 20.0,
    src: SourceReference | None = None,
    evidence: EvidenceClass = EvidenceClass.INFERRED,
    when: TemporalEvidence | None = None,
) -> MarketEdge:
    return MarketEdge(
        edge_id=edge_id,
        source_node=source_node,
        target_node=target_node,
        relation="credit exposure",
        exposure_lower_usd=lower,
        exposure_upper_usd=upper,
        evidence_class=evidence,
        confidence=0.7,
        temporal=when or temporal(),
        source=src,
        attributes={"fixture": True},
    )


def test_append_snapshot_and_retry_are_temporal_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "market.duckdb"
    nodes = (node("a"), node("b", src=source(digit="2")))
    relation = edge("a-to-b", "a", "b")
    with MarketTwin(database) as twin:
        first = twin.append(nodes=nodes, edges=(relation,))
        second = twin.append(nodes=nodes, edges=(relation,))
        snapshot = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        manifest = twin.manifest()
    assert first.inserted_nodes == 2
    assert first.inserted_edges == 1
    assert second.inserted_nodes == 0
    assert second.inserted_edges == 0
    assert [item.node_id for item in snapshot.nodes] == ["a", "b"]
    assert [item.edge_id for item in snapshot.edges] == ["a-to-b"]
    assert manifest.node_versions == 2
    assert manifest.edge_versions == 1
    assert manifest.object_set_sha256 != "0" * 64


def test_snapshot_excludes_future_versions_and_latest_only_by_default() -> None:
    original = node("a", capacity=100.0)
    future = node(
        "a",
        capacity=200.0,
        src=source(digit="2"),
        when=temporal(available_at=DECISION + timedelta(days=1)),
    )
    latest = node(
        "latest",
        src=source(
            "fdic.bankfind.financials",
            digit="3",
            coverage=TemporalCoverage.LATEST_ONLY,
        ),
    )
    with MarketTwin() as twin:
        twin.append(nodes=(original, future, latest))
        safe = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        current = twin.snapshot(
            decision_time=DECISION,
            valid_at=VALID,
            allow_latest_only=True,
        )
        after = twin.snapshot(
            decision_time=DECISION + timedelta(days=2),
            valid_at=VALID,
            allow_latest_only=True,
        )
    assert [item.node_id for item in safe.nodes] == ["a"]
    assert safe.excluded_latest_only_nodes == 1
    assert {item.node_id for item in current.nodes} == {"a", "latest"}
    assert next(item for item in after.nodes if item.node_id == "a").loss_absorption_usd == 200.0


def test_safe_snapshot_falls_back_to_prior_eligible_version_of_same_node() -> None:
    historical = node("a", capacity=100.0)
    current_latest = node(
        "a",
        capacity=999.0,
        src=source(
            "fdic.bankfind.financials",
            digit="3",
            coverage=TemporalCoverage.LATEST_ONLY,
        ),
        when=temporal(available_at=DECISION - timedelta(hours=1)),
    )
    with MarketTwin() as twin:
        twin.append(nodes=(historical, current_latest))
        safe = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        unsafe = twin.snapshot(
            decision_time=DECISION,
            valid_at=VALID,
            allow_latest_only=True,
        )
    assert safe.nodes[0].loss_absorption_usd == 100.0
    assert unsafe.nodes[0].loss_absorption_usd == 999.0
    assert safe.excluded_latest_only_nodes == 0


def test_edge_is_removed_when_latest_only_endpoint_is_excluded() -> None:
    safe = node("safe")
    latest = node(
        "latest",
        src=source(
            "fdic.bankfind.financials",
            digit="3",
            coverage=TemporalCoverage.LATEST_ONLY,
        ),
    )
    relation = edge("latest-to-safe", "latest", "safe")
    with MarketTwin() as twin:
        twin.append(nodes=(safe, latest), edges=(relation,))
        snapshot = twin.snapshot(decision_time=DECISION, valid_at=VALID)
    assert [item.node_id for item in snapshot.nodes] == ["safe"]
    assert snapshot.edges == ()
    assert snapshot.excluded_latest_only_edges == 1


def test_bounded_contagion_matches_two_node_hand_calculation() -> None:
    nodes = (node("a", capacity=100.0), node("b", capacity=100.0, src=source(digit="2")))
    relation = edge("a-to-b", "a", "b", lower=10.0, upper=20.0)
    with MarketTwin() as twin:
        twin.append(nodes=nodes, edges=(relation,))
        snapshot = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        result = twin.propagate(snapshot, initial_shocks={"a": 0.5})
        graph = twin.to_networkx(snapshot)
    assert result.converged is True
    assert result.lower_loss_fraction == pytest.approx({"a": 0.5, "b": 0.05})
    assert result.upper_loss_fraction == pytest.approx({"a": 0.5, "b": 0.10})
    assert result.lower_loss_fraction["b"] <= result.upper_loss_fraction["b"]
    assert set(graph.nodes) == {"a", "b"}
    assert graph.number_of_edges() == 1


def test_cycle_propagation_is_monotone_bounded_and_can_report_nonconvergence() -> None:
    nodes = (node("a", capacity=100.0), node("b", capacity=100.0, src=source(digit="2")))
    edges = (
        edge("a-to-b", "a", "b", lower=50.0, upper=50.0),
        edge("b-to-a", "b", "a", lower=50.0, upper=50.0),
    )
    with MarketTwin() as twin:
        twin.append(nodes=nodes, edges=edges)
        snapshot = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        converged = twin.propagate(snapshot, initial_shocks={"a": 0.2})
        bounded = twin.propagate(snapshot, initial_shocks={"a": 0.2}, max_rounds=1)
    assert converged.converged is True
    assert all(0 <= value <= 1 for value in converged.upper_loss_fraction.values())
    assert bounded.converged is False
    assert bounded.rounds == 1


def test_exact_sourced_edges_and_bounded_inferred_edges_have_distinct_contracts() -> None:
    official = source()
    exact = edge(
        "exact",
        "a",
        "b",
        lower=10,
        upper=10,
        src=official,
        evidence=EvidenceClass.REPORTED,
    )
    assert exact.source == official
    with pytest.raises(ValidationError, match="must be exact"):
        edge(
            "bad",
            "a",
            "b",
            lower=10,
            upper=20,
            src=official,
            evidence=EvidenceClass.REPORTED,
        )
    with pytest.raises(ValidationError, match="require a source"):
        edge(
            "missing",
            "a",
            "b",
            lower=10,
            upper=10,
            evidence=EvidenceClass.OBSERVED,
        )


def test_mutations_unknown_endpoints_and_staged_collisions_fail_atomically() -> None:
    first = node("a")
    mutated = first.model_copy(update={"label": "MUTATED"})
    with MarketTwin() as twin:
        twin.append(nodes=(first,))
        with pytest.raises(GraphMutationError, match="attempted to mutate"):
            twin.append(nodes=(mutated,))
        with pytest.raises(ValueError, match="not stored nodes"):
            twin.append(edges=(edge("bad-edge", "a", "missing"),))
        assert twin.manifest().edge_versions == 0

    with MarketTwin() as twin, pytest.raises(GraphMutationError, match="staged content"):
        twin.append(nodes=(first, mutated))


def test_invalid_graph_contracts_and_propagation_inputs_fail_closed() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        edge("self", "a", "a")
    with pytest.raises(ValidationError, match="below"):
        edge("reverse", "a", "b", lower=20, upper=10)
    with pytest.raises(ValidationError, match="valid_to"):
        temporal(valid_to=VALID - timedelta(days=1))
    with pytest.raises(ValidationError, match="require a source"):
        MarketNode(
            node_id="unsourced",
            label="Unsourced",
            kind=NodeKind.BANK,
            loss_absorption_usd=100,
            evidence_class=EvidenceClass.REPORTED,
            temporal=temporal(),
            source=None,
        )

    with MarketTwin() as twin:
        twin.append(nodes=(node("a"),))
        snapshot = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        with pytest.raises(ValueError, match="missing nodes"):
            twin.propagate(snapshot, initial_shocks={"missing": 0.1})
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            twin.propagate(snapshot, initial_shocks={"a": 1.1})
        with pytest.raises(ValueError, match="max_rounds"):
            twin.propagate(snapshot, initial_shocks={"a": 0.1}, max_rounds=0)
        with pytest.raises(ValueError, match="tolerance"):
            twin.propagate(snapshot, initial_shocks={"a": 0.1}, tolerance=0)


def test_empty_graph_append_and_snapshot_are_deterministic() -> None:
    with MarketTwin() as twin:
        assert twin.append() == twin.append()
        first = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        second = twin.snapshot(decision_time=DECISION, valid_at=VALID)
        with pytest.raises(ValueError, match="empty graph"):
            twin.propagate(first, initial_shocks={})
    assert first.graph_sha256 == second.graph_sha256
