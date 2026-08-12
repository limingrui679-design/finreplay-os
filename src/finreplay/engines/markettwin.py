"""Evidence-graded temporal financial graph and bounded contagion propagation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from finreplay.contracts import EvidenceClass, SourceReference


class GraphMutationError(RuntimeError):
    """Raised when one immutable graph object identity supplies changed content."""


class NodeKind(StrEnum):
    BANK = "bank"
    ISSUER = "issuer"
    FUND = "fund"
    SECURITY = "security"
    GOVERNMENT = "government"
    CENTRAL_BANK = "central_bank"
    MARKET = "market"
    OTHER = "other"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemporalEvidence(_StrictModel):
    """Economic and knowledge clocks shared by graph nodes and edges."""

    valid_from: datetime
    valid_to: datetime | None = None
    available_at: datetime

    @model_validator(mode="after")
    def validate_clocks(self) -> TemporalEvidence:
        _require_aware(self.valid_from, "valid_from")
        _require_aware(self.available_at, "available_at")
        if self.valid_to is not None:
            _require_aware(self.valid_to, "valid_to")
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must not precede valid_from")
        return self


class MarketNode(_StrictModel):
    """One temporally eligible institution, security, or market state node."""

    node_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=300)
    kind: NodeKind
    loss_absorption_usd: float = Field(gt=0.0)
    evidence_class: EvidenceClass
    temporal: TemporalEvidence
    source: SourceReference | None = None
    attributes: dict[str, bool | float | int | str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_node(self) -> MarketNode:
        if not math.isfinite(self.loss_absorption_usd):
            raise ValueError("loss_absorption_usd must be finite")
        if (
            self.evidence_class in {EvidenceClass.OBSERVED, EvidenceClass.REPORTED}
            and self.source is None
        ):
            raise ValueError("observed and reported nodes require a source")
        return self


class MarketEdge(_StrictModel):
    """Directed debtor/source-to-exposed/target relationship with explicit bounds."""

    edge_id: str = Field(min_length=3, max_length=240)
    source_node: str = Field(min_length=1, max_length=200)
    target_node: str = Field(min_length=1, max_length=200)
    relation: str = Field(min_length=2, max_length=200)
    exposure_lower_usd: float = Field(ge=0.0)
    exposure_upper_usd: float = Field(ge=0.0)
    evidence_class: EvidenceClass
    confidence: float = Field(ge=0.0, le=1.0)
    temporal: TemporalEvidence
    source: SourceReference | None = None
    attributes: dict[str, bool | float | int | str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_edge(self) -> MarketEdge:
        if self.source_node == self.target_node:
            raise ValueError("self-loop exposures are not allowed")
        if not math.isfinite(self.exposure_lower_usd) or not math.isfinite(
            self.exposure_upper_usd
        ):
            raise ValueError("exposure bounds must be finite")
        if self.exposure_upper_usd < self.exposure_lower_usd:
            raise ValueError("exposure_upper_usd must not be below exposure_lower_usd")
        sourced = {EvidenceClass.OBSERVED, EvidenceClass.REPORTED}
        if self.evidence_class in sourced and self.source is None:
            raise ValueError("observed and reported edges require a source")
        if self.evidence_class in sourced and self.exposure_lower_usd != self.exposure_upper_usd:
            raise ValueError("observed and reported exposures must be exact, not a range")
        return self


class MarketSnapshot(_StrictModel):
    decision_time: datetime
    valid_at: datetime
    nodes: tuple[MarketNode, ...]
    edges: tuple[MarketEdge, ...]
    excluded_latest_only_nodes: int
    excluded_latest_only_edges: int
    graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot(self) -> MarketSnapshot:
        _require_aware(self.decision_time, "decision_time")
        _require_aware(self.valid_at, "valid_at")
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("snapshot node identities must be unique")
        missing = {
            endpoint
            for edge in self.edges
            for endpoint in (edge.source_node, edge.target_node)
            if endpoint not in node_ids
        }
        if missing:
            raise ValueError(f"snapshot edges reference missing nodes: {sorted(missing)}")
        return self


class ContagionResult(_StrictModel):
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_shocks: dict[str, float]
    lower_loss_fraction: dict[str, float]
    upper_loss_fraction: dict[str, float]
    rounds: int = Field(ge=0)
    converged: bool
    tolerance: float = Field(gt=0.0)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> ContagionResult:
        keys = set(self.lower_loss_fraction)
        if keys != set(self.upper_loss_fraction):
            raise ValueError("lower and upper contagion results must cover the same nodes")
        for node_id in keys:
            lower = self.lower_loss_fraction[node_id]
            upper = self.upper_loss_fraction[node_id]
            if not 0.0 <= lower <= upper <= 1.0:
                raise ValueError(f"invalid contagion bounds for {node_id}")
        return self


@dataclass(frozen=True, slots=True)
class GraphAppendReceipt:
    attempted_nodes: int
    inserted_nodes: int
    attempted_edges: int
    inserted_edges: int


@dataclass(frozen=True, slots=True)
class MarketTwinManifest:
    node_versions: int
    edge_versions: int
    distinct_nodes: int
    distinct_edges: int
    object_set_sha256: str


class MarketTwin:
    """Append-only temporal graph store with conservative snapshot and bounds semantics."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._connection = duckdb.connect(self.database)
        self._connection.execute("SET TimeZone = 'UTC'")
        self._initialize_schema()

    def __enter__(self) -> MarketTwin:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def append(
        self,
        *,
        nodes: tuple[MarketNode, ...] = (),
        edges: tuple[MarketEdge, ...] = (),
    ) -> GraphAppendReceipt:
        if not nodes and not edges:
            return GraphAppendReceipt(0, 0, 0, 0)
        prepared_nodes = [(node, _object_hash(node)) for node in nodes]
        prepared_edges = [(edge, _object_hash(edge)) for edge in edges]
        self._reject_batch_collisions(prepared_nodes, prepared_edges)
        self._connection.execute("BEGIN TRANSACTION")
        try:
            before_nodes = int(
                self._connection.execute("SELECT COUNT(*) FROM market_nodes").fetchone()[0]
            )
            before_edges = int(
                self._connection.execute("SELECT COUNT(*) FROM market_edges").fetchone()[0]
            )
            for node, content_hash in prepared_nodes:
                self._insert_node(node, content_hash)
            for edge, content_hash in prepared_edges:
                self._insert_edge(edge, content_hash, new_nodes={node.node_id for node in nodes})
            after_nodes = int(
                self._connection.execute("SELECT COUNT(*) FROM market_nodes").fetchone()[0]
            )
            after_edges = int(
                self._connection.execute("SELECT COUNT(*) FROM market_edges").fetchone()[0]
            )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        return GraphAppendReceipt(
            attempted_nodes=len(nodes),
            inserted_nodes=after_nodes - before_nodes,
            attempted_edges=len(edges),
            inserted_edges=after_edges - before_edges,
        )

    def snapshot(
        self,
        *,
        decision_time: datetime,
        valid_at: datetime,
        allow_latest_only: bool = False,
    ) -> MarketSnapshot:
        _require_aware(decision_time, "decision_time")
        _require_aware(valid_at, "valid_at")
        node_versions = self._eligible_objects(
            table="market_nodes",
            id_column="node_id",
            decision_time=decision_time,
            valid_at=valid_at,
            allow_latest_only=allow_latest_only,
        )
        edge_versions = self._eligible_objects(
            table="market_edges",
            id_column="edge_id",
            decision_time=decision_time,
            valid_at=valid_at,
            allow_latest_only=allow_latest_only,
        )
        nodes = [MarketNode.model_validate_json(row["object_json"]) for row in node_versions]
        edges = [MarketEdge.model_validate_json(row["object_json"]) for row in edge_versions]
        excluded_nodes = 0
        excluded_edges = 0
        if not allow_latest_only:
            all_nodes = self._eligible_objects(
                table="market_nodes",
                id_column="node_id",
                decision_time=decision_time,
                valid_at=valid_at,
                allow_latest_only=True,
            )
            all_edges = self._eligible_objects(
                table="market_edges",
                id_column="edge_id",
                decision_time=decision_time,
                valid_at=valid_at,
                allow_latest_only=True,
            )
            excluded_nodes = len(all_nodes) - len(node_versions)
            excluded_edges = len(all_edges) - len(edge_versions)
        node_ids = {node.node_id for node in nodes}
        endpoint_eligible_edges = [
            edge
            for edge in edges
            if edge.source_node in node_ids and edge.target_node in node_ids
        ]
        excluded_edges += len(edges) - len(endpoint_eligible_edges)
        edges = endpoint_eligible_edges
        graph_payload = {
            "decision_time": decision_time.astimezone(UTC).isoformat(),
            "valid_at": valid_at.astimezone(UTC).isoformat(),
            "nodes": [
                node.model_dump(mode="json")
                for node in sorted(nodes, key=lambda item: item.node_id)
            ],
            "edges": [
                edge.model_dump(mode="json")
                for edge in sorted(edges, key=lambda item: item.edge_id)
            ],
            "allow_latest_only": allow_latest_only,
        }
        graph_hash = hashlib.sha256(_canonical_json(graph_payload).encode()).hexdigest()
        return MarketSnapshot(
            decision_time=decision_time,
            valid_at=valid_at,
            nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
            edges=tuple(sorted(edges, key=lambda item: item.edge_id)),
            excluded_latest_only_nodes=excluded_nodes,
            excluded_latest_only_edges=excluded_edges,
            graph_sha256=graph_hash,
        )

    def propagate(
        self,
        snapshot: MarketSnapshot,
        *,
        initial_shocks: dict[str, float],
        max_rounds: int = 1_000,
        tolerance: float = 1e-10,
    ) -> ContagionResult:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tolerance must be between zero and one")
        node_ids = {node.node_id for node in snapshot.nodes}
        unknown = set(initial_shocks) - node_ids
        if unknown:
            raise ValueError(f"initial shocks reference missing nodes: {sorted(unknown)}")
        if any(
            not math.isfinite(value) or not 0.0 <= value <= 1.0
            for value in initial_shocks.values()
        ):
            raise ValueError("initial shock fractions must be finite values in [0, 1]")
        if not snapshot.nodes:
            raise ValueError("cannot propagate an empty graph")
        lower, lower_rounds, lower_converged = _propagate_bound(
            snapshot,
            initial_shocks=initial_shocks,
            upper=False,
            max_rounds=max_rounds,
            tolerance=tolerance,
        )
        upper, upper_rounds, upper_converged = _propagate_bound(
            snapshot,
            initial_shocks=initial_shocks,
            upper=True,
            max_rounds=max_rounds,
            tolerance=tolerance,
        )
        return ContagionResult(
            snapshot_sha256=snapshot.graph_sha256,
            initial_shocks=initial_shocks,
            lower_loss_fraction=lower,
            upper_loss_fraction=upper,
            rounds=max(lower_rounds, upper_rounds),
            converged=lower_converged and upper_converged,
            tolerance=tolerance,
            limitations=(
                "Exposure bounds are propagated through a monotone linear loss channel capped "
                "at total node loss; this is a stress envelope, not a causal forecast.",
                "Behavioral responses, fire-sale price formation, netting, collateral and policy "
                "interventions require separately evidenced scenario modules.",
            ),
        )

    def to_networkx(
        self, snapshot: MarketSnapshot
    ) -> nx.MultiDiGraph[str, dict[str, Any], dict[str, Any]]:
        graph: nx.MultiDiGraph[str, dict[str, Any], dict[str, Any]] = nx.MultiDiGraph(
            graph_sha256=snapshot.graph_sha256
        )
        for node in snapshot.nodes:
            graph.add_node(node.node_id, model=node.model_dump(mode="json"))
        for edge in snapshot.edges:
            graph.add_edge(
                edge.source_node,
                edge.target_node,
                key=edge.edge_id,
                model=edge.model_dump(mode="json"),
            )
        return graph

    def manifest(self) -> MarketTwinManifest:
        node_versions, distinct_nodes = self._connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT node_id) FROM market_nodes"
        ).fetchone()
        edge_versions, distinct_edges = self._connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT edge_id) FROM market_edges"
        ).fetchone()
        hashes = [
            row[0]
            for row in self._connection.execute(
                """
                SELECT content_hash FROM market_nodes
                UNION ALL
                SELECT content_hash FROM market_edges
                ORDER BY content_hash
                """
            ).fetchall()
        ]
        return MarketTwinManifest(
            node_versions=int(node_versions),
            edge_versions=int(edge_versions),
            distinct_nodes=int(distinct_nodes),
            distinct_edges=int(distinct_edges),
            object_set_sha256=hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
        )

    def _initialize_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_nodes (
                node_id VARCHAR NOT NULL,
                version_id VARCHAR NOT NULL,
                content_hash VARCHAR NOT NULL UNIQUE,
                valid_from TIMESTAMPTZ NOT NULL,
                valid_to TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL,
                object_json JSON NOT NULL,
                PRIMARY KEY (node_id, version_id)
            );
            CREATE TABLE IF NOT EXISTS market_edges (
                edge_id VARCHAR NOT NULL,
                version_id VARCHAR NOT NULL,
                content_hash VARCHAR NOT NULL UNIQUE,
                source_node VARCHAR NOT NULL,
                target_node VARCHAR NOT NULL,
                valid_from TIMESTAMPTZ NOT NULL,
                valid_to TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL,
                object_json JSON NOT NULL,
                PRIMARY KEY (edge_id, version_id)
            );
            """
        )

    def _reject_batch_collisions(
        self,
        nodes: list[tuple[MarketNode, str]],
        edges: list[tuple[MarketEdge, str]],
    ) -> None:
        for kind, prepared in (("node", nodes), ("edge", edges)):
            identities: dict[tuple[str, str], str] = {}
            for item, content_hash in prepared:
                object_id = item.node_id if isinstance(item, MarketNode) else item.edge_id
                version_id = _version_id(item.temporal.available_at, item.source)
                key = (object_id, version_id)
                prior = identities.get(key)
                if prior is not None and prior != content_hash:
                    raise GraphMutationError(
                        f"one {kind} version produced conflicting staged content for {object_id!r}"
                    )
                identities[key] = content_hash

    def _insert_node(self, node: MarketNode, content_hash: str) -> None:
        version_id = _version_id(node.temporal.available_at, node.source)
        existing = self._connection.execute(
            "SELECT content_hash FROM market_nodes WHERE node_id = ? AND version_id = ?",
            [node.node_id, version_id],
        ).fetchone()
        if existing is not None:
            if existing[0] != content_hash:
                raise GraphMutationError(f"node {node.node_id!r} version attempted to mutate")
            return
        self._connection.execute(
            "INSERT INTO market_nodes VALUES (?, ?, ?, ?, ?, ?, ?::JSON)",
            [
                node.node_id,
                version_id,
                content_hash,
                node.temporal.valid_from,
                node.temporal.valid_to,
                node.temporal.available_at,
                node.model_dump_json(),
            ],
        )

    def _insert_edge(
        self,
        edge: MarketEdge,
        content_hash: str,
        *,
        new_nodes: set[str],
    ) -> None:
        existing_nodes = {
            row[0]
            for row in self._connection.execute(
                "SELECT DISTINCT node_id FROM market_nodes WHERE node_id IN (?, ?)",
                [edge.source_node, edge.target_node],
            ).fetchall()
        }
        missing = {edge.source_node, edge.target_node} - existing_nodes - new_nodes
        if missing:
            raise ValueError(f"edge endpoints are not stored nodes: {sorted(missing)}")
        version_id = _version_id(edge.temporal.available_at, edge.source)
        existing = self._connection.execute(
            "SELECT content_hash FROM market_edges WHERE edge_id = ? AND version_id = ?",
            [edge.edge_id, version_id],
        ).fetchone()
        if existing is not None:
            if existing[0] != content_hash:
                raise GraphMutationError(f"edge {edge.edge_id!r} version attempted to mutate")
            return
        self._connection.execute(
            "INSERT INTO market_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)",
            [
                edge.edge_id,
                version_id,
                content_hash,
                edge.source_node,
                edge.target_node,
                edge.temporal.valid_from,
                edge.temporal.valid_to,
                edge.temporal.available_at,
                edge.model_dump_json(),
            ],
        )

    def _eligible_objects(
        self,
        *,
        table: str,
        id_column: str,
        decision_time: datetime,
        valid_at: datetime,
        allow_latest_only: bool,
    ) -> list[dict[str, Any]]:
        if (table, id_column) not in {
            ("market_nodes", "node_id"),
            ("market_edges", "edge_id"),
        }:
            raise ValueError("unsupported market object table")
        temporal_predicate = ""
        if not allow_latest_only:
            temporal_predicate = (
                "AND (json_extract_string(object_json, '$.source.temporal_coverage') IS NULL "
                "OR json_extract_string(object_json, '$.source.temporal_coverage') "
                "!= 'latest_only')"
            )
        rows = self._connection.execute(
            f"""
            WITH eligible AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {id_column}
                    ORDER BY available_at DESC, content_hash DESC
                ) AS version_rank
                FROM {table}
                WHERE available_at <= ?
                  AND valid_from <= ?
                  AND (valid_to IS NULL OR valid_to > ?)
                  {temporal_predicate}
            )
            SELECT object_json FROM eligible WHERE version_rank = 1 ORDER BY {id_column}
            """,
            [decision_time, valid_at, valid_at],
        ).fetchall()
        return [
            {
                "object_json": row[0]
                if isinstance(row[0], str)
                else json.dumps(row[0])
            }
            for row in rows
        ]


def _propagate_bound(
    snapshot: MarketSnapshot,
    *,
    initial_shocks: dict[str, float],
    upper: bool,
    max_rounds: int,
    tolerance: float,
) -> tuple[dict[str, float], int, bool]:
    capacity = {node.node_id: node.loss_absorption_usd for node in snapshot.nodes}
    losses = {node_id: initial_shocks.get(node_id, 0.0) for node_id in capacity}
    exogenous = dict(losses)
    for round_number in range(1, max_rounds + 1):
        transmitted = dict.fromkeys(capacity, 0.0)
        for edge in snapshot.edges:
            exposure = edge.exposure_upper_usd if upper else edge.exposure_lower_usd
            transmitted[edge.target_node] += (
                losses[edge.source_node] * exposure / capacity[edge.target_node]
            )
        updated = {
            node_id: min(1.0, exogenous[node_id] + transmitted[node_id])
            for node_id in capacity
        }
        delta = max(abs(updated[node_id] - losses[node_id]) for node_id in capacity)
        losses = updated
        if delta <= tolerance:
            return losses, round_number, True
    return losses, max_rounds, False


def _version_id(available_at: datetime, source: SourceReference | None) -> str:
    source_hash = "unsourced" if source is None else source.sha256
    payload = f"{available_at.astimezone(UTC).isoformat()}:{source_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _object_hash(value: BaseModel) -> str:
    return hashlib.sha256(
        _canonical_json(value.model_dump(mode="json")).encode()
    ).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
