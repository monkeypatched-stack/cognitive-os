"""Types for the execution-graph compiler (ADR 0021).

The CSR arrays are plain Python lists here — correct, dependency-free, and matching
the repo's pure-Python graph-algorithm style (cf. graph_store PageRank). They map
one-to-one onto numpy/scipy CSR; swapping in numpy is a later performance step, not a
correctness change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Mapping, Sequence

NodeId = str  # a graph node instance id (semantic node "id")
AgentKey = str  # agent / capability identity — the learning key


class DanglingPolicy(Enum):
    """How rows with no out-edges are resolved when building M."""

    DROP = auto()  # leave the row empty — a DAG leaf / absorbing sink (default; correct for execution)
    UNIFORM = auto()  # redistribute row mass evenly (PageRank convention; for centrality/stationary use)
    ABSORB = auto()  # self-loop identity row


# Default prior for an edge whose coordination strength has never been learned.
# Matches QTable's default Q (graph_manager.py) so an un-learned edge is neutral.
DEFAULT_STRENGTH_PRIOR = 0.5


@dataclass(frozen=True)
class SemanticGraphSnapshot:
    """Input: the learned execution graph — topology + coordination memory.

    A read-only snapshot. The compiler never mutates it (compiler outside the loop).
    `strengths` maps a coordination link (src_agent, dst_agent) to its learned weight
    W(i→j); missing links fall back to DEFAULT_STRENGTH_PRIOR at compile time.
    """

    nodes: Sequence[Mapping[str, Any]]
    edges: Sequence[Mapping[str, Any]]
    strengths: Mapping[tuple[AgentKey, AgentKey], float] = field(default_factory=dict)
    graph_id: str = ""
    revision: int = 0

    @staticmethod
    def from_graph_dict(
        graph: Mapping[str, Any],
        strengths: Mapping[tuple[AgentKey, AgentKey], float] | None = None,
        revision: int = 0,
    ) -> "SemanticGraphSnapshot":
        """Adapt a GraphManager-style {nodes, edges, ...} dict into a SemanticGraphSnapshot."""
        return SemanticGraphSnapshot(
            nodes=list(graph.get("nodes", [])),
            edges=list(graph.get("edges", [])),
            strengths=dict(strengths or {}),
            graph_id=str(graph.get("graph_id", "")),
            revision=revision,
        )


@dataclass(frozen=True)
class CompiledOperator:
    """Output: M = D⁻¹W as a row-stochastic sparse transition operator (CSR).

    Immutable, reproducible-from-source, holds no ground truth. CSR is row-major:
    row i's nonzeros are indices[indptr[i]:indptr[i+1]] with values data[...].
    """

    indptr: Sequence[int]  # len n+1
    indices: Sequence[int]  # len nnz — column indices
    data: Sequence[float]  # len nnz — M[i,j], row-normalized
    index_of: Mapping[NodeId, int]  # semantic id → matrix index
    node_of: Sequence[NodeId]  # inverse
    agent_of: Sequence[AgentKey]  # index → agent (spectral / anchor reporting)
    dangling: frozenset[int]  # rows with no out-edges
    source_graph_id: str
    source_revision: int
    compile_hash: str  # determinism proof + cache key

    @property
    def n(self) -> int:
        return len(self.node_of)

    @property
    def nnz(self) -> int:
        return len(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "indptr": list(self.indptr),
            "indices": list(self.indices),
            "data": list(self.data),
            "node_of": list(self.node_of),
            "agent_of": list(self.agent_of),
            "dangling": sorted(self.dangling),
            "source_graph_id": self.source_graph_id,
            "source_revision": self.source_revision,
            "compile_hash": self.compile_hash,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CompiledOperator":
        node_of = list(d["node_of"])
        return CompiledOperator(
            indptr=list(d["indptr"]),
            indices=list(d["indices"]),
            data=list(d["data"]),
            index_of={nid: i for i, nid in enumerate(node_of)},
            node_of=node_of,
            agent_of=list(d["agent_of"]),
            dangling=frozenset(d.get("dangling", [])),
            source_graph_id=d.get("source_graph_id", ""),
            source_revision=int(d.get("source_revision", 0)),
            compile_hash=d.get("compile_hash", ""),
        )


@dataclass(frozen=True)
class DomainOperatorSet:
    """Per-domain sparse operators — the block-diagonal decomposition of the mesh.

    Domains coordinate internally but rarely across, so the global operator is
    block-diagonal: `domains[d]` is domain d's own intra-domain operator (its block),
    and `mesh` is a small domain-level operator built from cross-domain edges (the
    off-diagonal, aggregated to domain granularity). Each block compiles, verifies,
    recompiles, and federates independently.
    """

    domains: Mapping[str, "CompiledOperator"]  # domain name → its own operator (block)
    mesh: "CompiledOperator | None"  # domain-level operator from cross-domain edges
    node_domain: Mapping[NodeId, str]  # each node's domain
    cross_edges: int  # count of edges that crossed a domain boundary

    def domain_of(self, node_id: NodeId) -> str:
        return self.node_domain.get(node_id, "default")


@dataclass(frozen=True)
class VerificationReport:
    """Result of Compile.verify — the semantics-preserving test."""

    regime: str  # "dag" | "cyclic" | "empty"
    row_stochastic: bool
    spectral_radius: float
    max_path_sum_error: float  # dag only
    policy_agreement: float  # dag only; must be 1.0 to pass
    converged: bool  # cyclic only
    iterations: int  # cyclic only
    ok: bool
    failures: Sequence[str] = field(default_factory=tuple)
