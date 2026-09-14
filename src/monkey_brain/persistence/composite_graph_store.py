"""CompositeGraphStore — replaces Neo4j GraphStore with tensor + KV storage.

Combines:
    - TensorGraphStore: transition-graph operations (capability, ontology, mesh)
    - TrustKVStore: trust edge persistence

This replaces the Neo4j-backed GraphStore while maintaining the same interface.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.persistence.tensor_graph_store import TensorGraphStore
from src.monkey_brain.persistence.trust_kv_store import TrustKVStore

logger = logging.getLogger("agentos.composite_graph_store")


class CompositeGraphStore:
    """Drop-in replacement for Neo4j GraphStore.

    Combines TensorGraphStore (transition-graph ops) with TrustKVStore (trust persistence).
    Maintains the same interface as the original GraphStore for backward compatibility.
    """

    def __init__(
        self,
        tensor: SparseTransitionTensor | None = None,
        trust_path: str | None = None,
    ) -> None:
        self._tensor_store = TensorGraphStore(tensor)
        self._trust_store = TrustKVStore(trust_path)
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    # ── Capability composition (REQUIRES edges) ──────────────────────────

    async def save_capability_composition(self, capability: str, sub_capabilities: list[str]) -> None:
        self._tensor_store.save_capability_composition(capability, sub_capabilities)

    async def get_capability_composition(self, capability: str) -> list[str] | None:
        return self._tensor_store.get_capability_composition(capability)

    # ── Provider resolution (RESOLVED_BY, EXPOSES edges) ────────────────

    async def register_capability_provider(self, agent_name: str, capability: str, provider_type: str = "") -> None:
        self._tensor_store.register_capability_provider(agent_name, capability, provider_type)

    async def find_capability_providers(self, capability: str) -> list[dict[str, Any]]:
        return self._tensor_store.find_capability_providers(capability)

    # ── Ontology (IS_A edges) ───────────────────────────────────────────

    async def link_capability_generalization(self, specific: str, general: str) -> None:
        self._tensor_store.link_capability_generalization(specific, general)

    async def find_generalization(self, specific: str) -> str | None:
        return self._tensor_store.find_generalization(specific)

    # ── PageRank/Centrality ──────────────────────────────────────────────

    async def compute_capability_pagerank(self, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
        return self._tensor_store.compute_capability_pagerank(iterations, damping)

    async def compute_execution_mesh_pagerank(self, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
        return self._tensor_store.compute_capability_pagerank(iterations, damping)

    # ── Mesh similarity ──────────────────────────────────────────────────

    async def record_execution_mesh(self, graph_id: str, agents: list[str], goal: str = "") -> None:
        self._tensor_store.record_execution_mesh(graph_id, agents, goal)

    async def find_similar_execution_graphs(self, agents: list[str], limit: int = 5) -> list[dict[str, Any]]:
        return self._tensor_store.find_similar_execution_graphs(agents, limit)

    # ── Trust persistence (delegates to TrustKVStore) ────────────────────

    async def save_trust_edge(self, **kwargs) -> None:
        await self._trust_store.save_trust_edge(**kwargs)

    async def revoke_trust_edge(self, src: str, dst: str, revoked_by: str = "") -> None:
        await self._trust_store.revoke_trust_edge(src, dst, revoked_by)

    async def load_trust_edges(self) -> list[dict[str, Any]]:
        return await self._trust_store.load_trust_edges()

    # ── Graph sync (delegates to TensorGraphStore) ───────────────────────

    async def sync_graph(self, graph: dict[str, Any], *, append: bool = False) -> None:
        await self._tensor_store.sync_graph(graph, append=append)

    async def get_graph_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        return await self._tensor_store.get_graph_by_run_id(run_id)

    async def add_query_paths(self, paths: list[dict[str, Any]]) -> None:
        await self._tensor_store.add_query_paths(paths)

    # ── View methods (real tensor-backed views — see TensorGraphStore's "Layer views") ──

    async def get_execution_mesh_view(self) -> dict[str, Any]:
        return self._tensor_store.get_execution_mesh_view()

    async def get_top_ranked_capabilities(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._tensor_store.get_top_ranked_capabilities(limit)

    async def get_top_ranked_execution_graphs(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._tensor_store.get_top_ranked_execution_graphs(limit)

    async def get_execution_graph_view(self, graph_key: str) -> dict[str, Any]:
        return self._tensor_store.get_execution_graph_view(graph_key)

    async def get_agent_capability_graph(self, agent_name: str) -> dict[str, Any]:
        return self._tensor_store.get_agent_capability_graph(agent_name)

    async def get_ontology_view(self) -> dict[str, Any]:
        return self._tensor_store.get_ontology_view()

    async def find_execution_graph_for_capability(self, capability: str) -> dict[str, Any] | None:
        return self._tensor_store.find_execution_graph_for_capability(capability)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def close(self) -> None:
        pass  # No external connections to close

    @property
    def tensor(self) -> SparseTransitionTensor:
        """Access the underlying tensor for direct operations."""
        return self._tensor_store._tensor
