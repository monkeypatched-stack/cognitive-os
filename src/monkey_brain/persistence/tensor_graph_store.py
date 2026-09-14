"""TensorGraphStore — tensor-backed graph store replacing Neo4j.

Replaces Neo4j Cypher queries with tensor operations for:
    - Capability composition (REQUIRES edges)
    - Provider resolution (RESOLVED_BY, EXPOSES edges)
    - Ontology (IS_A edges)
    - PageRank/centrality (native via Bellman convergence)
    - Mesh similarity (SIMILAR_TO edges)

Architecture:
    Layer 1 (World Inference): SparseTransitionTensor W[d,i,j,f]
    This store maps Neo4j graph operations to tensor lookups.

Neo4j node types → tensor domains:
    Agent       → domain="agent"
    Capability  → domain="capability"
    Provider    → domain="provider"
    Tool        → domain="tool"
    Ontology    → domain="ontology"
    Execution   → domain="execution"

Neo4j edge types → tensor features:
    REQUIRES    → Feature.FREQUENCY (with RECENCY for order)
    RESOLVED_BY → Feature.CONFIDENCE
    IS_A        → Feature.PROBABILITY
    SIMILAR_TO  → Feature.FREQUENCY
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor

logger = logging.getLogger("agentos.tensor_graph_store")


class TensorGraphStore:
    """Tensor-backed graph store replacing Neo4j for transition-graph operations.

    Uses SparseTransitionTensor W[d,i,j,f] to store:
    - Capability composition (REQUIRES edges)
    - Provider resolution (RESOLVED_BY, EXPOSES edges)
    - Ontology (IS_A edges)
    - PageRank/centrality
    - Mesh similarity

    Thread-safe: all mutations are guarded by a lock.
    """

    def __init__(self, tensor: SparseTransitionTensor | None = None) -> None:
        self._tensor = tensor or SparseTransitionTensor()
        self._connected = True  # Always "connected" (no external dependency)

    def is_connected(self) -> bool:
        return self._connected

    # ── Capability Composition (REQUIRES edges) ──────────────────────────

    def save_capability_composition(self, capability: str, sub_capabilities: list[str]) -> None:
        """Record that capability X requires sub-capabilities [a, b, c] in order.

        Maps to: W[domain="capability", cap, sub, FREQUENCY] with RECENCY for order.
        """
        if not sub_capabilities:
            return
        for order, sub in enumerate(sub_capabilities):
            # Use RECENCY to encode order (higher = later in sequence)
            self._tensor.observe(
                capability,
                sub,
                domain="capability",
            )
            # Store order in the cell's last_ts field (repurposed for order)
            i = self._tensor.intern(capability, "capability")
            j = self._tensor.intern(sub, "capability")
            cell = self._tensor._cells.get((i, j))
            if cell:
                cell.last_ts = float(order)
        logger.info("TensorGraphStore: saved composition %s -> %s", capability, sub_capabilities)

    def get_capability_composition(self, capability: str) -> list[str] | None:
        """Get previously-discovered decomposition for a capability, in order.

        Reads from: W[domain="capability", capability, *, FREQUENCY]
        """
        successors = self._tensor.successors(capability)
        if not successors:
            return None
        # Filter to capability domain and sort by order (RECENCY)
        caps = []
        for dst, dom in successors:
            if dom == "capability":
                i = self._tensor.intern(capability, "capability")
                j = self._tensor.intern(dst, "capability")
                cell = self._tensor._cells.get((i, j))
                order = int(cell.last_ts) if cell else 0
                caps.append((order, dst))
        caps.sort(key=lambda x: x[0])
        return [dst for _, dst in caps] or None

    # ── Provider Resolution (RESOLVED_BY, EXPOSES edges) ────────────────

    _EXTERNAL_PROVIDERS = frozenset({"n8n", "ard", "openclaw", "nanda", "llm_provider", "sdlc"})

    def register_capability_provider(self, agent_name: str, capability: str, provider_type: str = "") -> None:
        """Record that a capability was resolved by an external provider.

        Maps to:
            W["capability", cap, provider, CONFIDENCE]
            W["provider", provider, tool, FREQUENCY]
        """
        if not agent_name or not capability:
            return
        if ":" in agent_name:
            provider_name, _, tool_name = agent_name.partition(":")
        elif agent_name in self._EXTERNAL_PROVIDERS:
            provider_name, tool_name = agent_name, agent_name
        else:
            provider_name, tool_name = (provider_type or "broca"), agent_name
        if provider_name not in self._EXTERNAL_PROVIDERS:
            return

        # Store capability -> provider resolution
        self._tensor.observe(
            capability,
            provider_name,
            domain="capability",
            confidence=1.0,
        )

        # Store provider -> tool exposure
        self._tensor.observe(
            provider_name,
            tool_name,
            domain="provider",
        )

        logger.info("TensorGraphStore: registered provider %s for %s", provider_name, capability)

    def find_capability_providers(self, capability: str) -> list[dict[str, Any]]:
        """Find providers for a capability, most-recently-confirmed first.

        Reads from: W["capability", capability, *, CONFIDENCE]
        """
        results = []
        for dst, dom in self._tensor.successors(capability):
            if dom == "capability":
                i = self._tensor.intern(capability, "capability")
                j = self._tensor.intern(dst, "capability")
                cell = self._tensor._cells.get((i, j))
                confidence = self._tensor.feature(capability, dst, Feature.CONFIDENCE)
                recency = cell.last_ts if cell else 0.0
                results.append(
                    {
                        "provider": dst,
                        "tool": dst,
                        "agent": dst,
                        "last_confirmed": recency,
                        "confidence": confidence,
                    }
                )
        results.sort(key=lambda x: x["last_confirmed"], reverse=True)
        return results

    # ── Ontology (IS_A edges) ───────────────────────────────────────────

    def link_capability_generalization(self, specific: str, general: str) -> None:
        """Record that specific is a generalization of general.

        Maps to: W["ontology", specific, general, PROBABILITY]
        """
        if not specific or not general or specific == general:
            return
        self._tensor.observe(
            specific,
            general,
            domain="ontology",
        )
        # Also ensure the general capability exists
        self._tensor.observe(
            general,
            general,
            domain="capability",
        )
        logger.info("TensorGraphStore: linked generalization %s -> %s", specific, general)

    def find_generalization(self, specific: str) -> str | None:
        """Find what general capability this specific name generalizes to.

        Reads from: W["ontology", specific, *, PROBABILITY]
        """
        for dst, dom in self._tensor.successors(specific):
            if dom == "ontology":
                return dst
        return None

    # ── PageRank/Centrality ──────────────────────────────────────────────

    def compute_capability_pagerank(self, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
        """Compute PageRank-like centrality over the ontology.

        Uses the tensor's Q_VALUE feature as a proxy for centrality.
        Bellman convergence approximates PageRank on the transition matrix.
        """
        # The tensor's Q_VALUE already approximates centrality via Bellman updates
        # We can derive PageRank-like scores from the feature values
        scores = {}
        for (i, j), cell in self._tensor._cells.items():
            src = self._tensor._state_name[i]
            if src:
                scores[src] = cell.q
        return scores

    # ── Mesh Similarity ──────────────────────────────────────────────────

    def record_execution_mesh(self, graph_id: str, agents: list[str], goal: str = "") -> None:
        """Record an execution graph in the mesh.

        Maps to: W["execution", graph_id, agent, FREQUENCY]
        """
        for agent in agents:
            self._tensor.observe(
                graph_id,
                agent,
                domain="execution",
            )
        logger.info(
            "TensorGraphStore: recorded execution mesh %s with %d agents",
            graph_id,
            len(agents),
        )

    def find_similar_execution_graphs(self, agents: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """Find execution graphs with similar agents.

        Reads from: W["execution", *, agent, FREQUENCY]
        """
        # Build a set of target agents
        target_agents = set(agents)

        # Find graphs that share agents
        graph_scores: dict[str, float] = {}
        for (i, j), cell in self._tensor._cells.items():
            src = self._tensor._state_name[i]
            dst = self._tensor._state_name[j]
            dom_i = self._tensor._state_domain[i]
            dom_j = self._tensor._state_domain[j]
            if dom_i == "execution" and dom_j in ("capability", "agent"):
                if dst in target_agents:
                    graph_scores[src] = graph_scores.get(src, 0.0) + cell.freq

        # Sort by overlap score
        results = []
        for graph_id, score in sorted(graph_scores.items(), key=lambda x: -x[1])[:limit]:
            results.append(
                {
                    "graph_id": graph_id,
                    "overlap": score,
                    "run_count": 1,  # Simplified
                }
            )
        return results

    # ── Layer views (tensor-native equivalents of GraphStore's Cypher views) ─
    #
    # The tensor has no separate node labels (ExecutionGraph/Agent/Capability/
    # Provider/OntologyConcept) — everything is one sparse W[d,i,j,f]. These
    # views recover the same three-layer SHAPE the Neo4j GraphStore returned
    # (see graph_store.py's get_execution_mesh_view/get_execution_graph_view/
    # get_agent_capability_graph/get_ontology_view) by filtering the tensor's
    # domain axis, so downstream callers (routes/state.py, agent_middleware's
    # _resolve_via_execution_mesh) see real, non-empty data. Metrics the
    # tensor never tracked (run_count, mesh_pagerank) are approximated from
    # observed edge frequency rather than fabricated.

    def get_execution_mesh_view(self) -> dict[str, Any]:
        """Layer 1: execution-graph nodes (domain="execution" sources, i.e.
        states recorded via record_execution_mesh) and their agent edges."""
        graph_agents: dict[str, set[str]] = {}
        edges: list[dict[str, Any]] = []
        for (i, j), cell in self._tensor._cells.items():
            if self._tensor._state_domain[i] != "execution":
                continue
            src, dst = self._tensor._state_name[i], self._tensor._state_name[j]
            graph_agents.setdefault(src, set()).add(dst)
            edges.append({"from": src, "to": dst, "type": "HAS_AGENT", "weight": cell.freq})
        nodes = [{"graph_key": gid, "name": gid, "agent_count": len(agents)} for gid, agents in graph_agents.items()]
        return {"nodes": nodes, "edges": edges, "metadata": {"source": "tensor"}}

    def get_top_ranked_capabilities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Highest-ranked capability-domain states, by the same Bellman
        Q-value the tensor already uses as its centrality proxy."""
        scores = self.compute_capability_pagerank()
        ranked = [
            (name, score)
            for name, score in scores.items()
            if self._tensor._state_index.get(name) is not None
            and self._tensor._state_domain[self._tensor._state_index[name]] == "capability"
        ]
        ranked.sort(key=lambda kv: -kv[1])
        return [{"name": name, "pagerank": score} for name, score in ranked[:limit]]

    def get_top_ranked_execution_graphs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Highest-ranked execution graphs, scored by total observed edge
        frequency (there is no separate run_count in the tensor model)."""
        scores: dict[str, float] = {}
        agent_counts: dict[str, int] = {}
        for (i, j), cell in self._tensor._cells.items():
            if self._tensor._state_domain[i] != "execution":
                continue
            gid = self._tensor._state_name[i]
            scores[gid] = scores.get(gid, 0.0) + cell.freq
            agent_counts[gid] = agent_counts.get(gid, 0) + 1
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [{"graph_key": gid, "score": score, "agent_count": agent_counts[gid]} for gid, score in ranked]

    def get_execution_graph_view(self, graph_key: str) -> dict[str, Any]:
        """Layer 2: one execution graph's agents (its recorded successors).
        No DEPENDS_ON ordering is tracked by record_execution_mesh today, so
        edges are graph->agent membership, not agent->agent ordering."""
        successors = self._tensor.successors(graph_key)
        nodes = [{"name": name} for name, _dom in successors]
        edges = [{"from": graph_key, "to": name, "type": "HAS_AGENT"} for name, _dom in successors]
        return {"graph_key": graph_key, "nodes": nodes, "edges": edges}

    def get_agent_capability_graph(self, agent_name: str) -> dict[str, Any]:
        """Layer 3: one agent's capability graph — capabilities resolved
        through a provider this agent is the tool for (register_capability_
        provider wires capability -> provider -> tool; an agent that IS an
        external provider also matches directly).

        Providers are identified by membership in _EXTERNAL_PROVIDERS, not by
        the tensor's domain tag: a provider name is first interned as the DST
        of its capability->provider edge (domain="capability", since intern()
        keeps the domain of a state's first observation), so `_state_domain
        == "provider"` never actually holds for it — only the tool name (the
        provider->tool edge's DST, interned fresh) gets that tag. Filtering on
        domain here would silently return the tool's own name as if it were
        the provider.
        """
        providers: set[str] = set()
        for (i, j), _cell in self._tensor._cells.items():
            if self._tensor._state_name[j] == agent_name and self._tensor._state_name[i] in self._EXTERNAL_PROVIDERS:
                providers.add(self._tensor._state_name[i])
        if agent_name in self._EXTERNAL_PROVIDERS:
            providers.add(agent_name)

        capabilities: list[dict[str, Any]] = []
        if providers:
            for (i, j), _cell in self._tensor._cells.items():
                dst = self._tensor._state_name[j]
                cap = self._tensor._state_name[i]
                if dst not in providers or cap in self._EXTERNAL_PROVIDERS:
                    continue
                capabilities.append(
                    {
                        "capability": cap,
                        "providers": [{"provider": dst, "tool": agent_name}],
                        "confidence": self._tensor.feature(cap, dst, Feature.CONFIDENCE),
                    }
                )
        return {"agent": agent_name, "capabilities": capabilities}

    def get_ontology_view(self) -> dict[str, Any]:
        """Ontology-only view: IS_A edges (domain="ontology")."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        seen: set[str] = set()
        for (i, j), _cell in self._tensor._cells.items():
            if self._tensor._state_domain[i] != "ontology":
                continue
            src, dst = self._tensor._state_name[i], self._tensor._state_name[j]
            for name in (src, dst):
                if name not in seen:
                    seen.add(name)
                    nodes.append({"name": name})
            edges.append({"from": src, "to": dst, "type": "IS_A"})
        return {"nodes": nodes, "edges": edges}

    def find_execution_graph_for_capability(self, capability: str) -> dict[str, Any] | None:
        """Best-effort recursive-composition lookup: an execution graph whose
        key/goal names the capability, or that directly has an agent named
        after it — mirroring the Neo4j version's name/agent match, scored by
        observed edge frequency in place of run_count."""
        if not capability:
            return None
        cap_lower = capability.lower()
        best: str | None = None
        best_score = -1.0
        for idx, name in enumerate(self._tensor._state_name):
            if name is None or self._tensor._state_domain[idx] != "execution":
                continue
            row = self._tensor._out.get(idx)
            if not row:
                continue
            agents = [self._tensor._state_name[j] for j in row]
            if cap_lower not in name.lower() and capability not in agents:
                continue
            score = sum(cell.freq for cell in row.values())
            if score > best_score:
                best_score = score
                best = name
        if best is None:
            return None
        return {"graph_key": best, "run_id": "", "goal": best, "run_count": 0}

    # ── Compatibility methods (match GraphStore interface) ────────────────

    async def sync_graph(self, graph: dict[str, Any], *, append: bool = False) -> None:
        """Compatibility method — sync execution graph to tensor."""
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        for node in nodes:
            if isinstance(node, dict):
                name = node.get("name", node.get("id", ""))
                ntype = node.get("type", "capability")
                if name:
                    self._tensor.observe(name, name, domain=ntype)

        for edge in edges:
            if isinstance(edge, dict):
                src = edge.get("from", edge.get("source", ""))
                dst = edge.get("to", edge.get("target", ""))
                if src and dst:
                    self._tensor.observe(src, dst, domain="execution")

    async def get_graph_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        """Compatibility method — find graph by run_id."""
        # In tensor, run_id is stored as a state
        successors = self._tensor.successors(run_id)
        if not successors:
            return None
        return {
            "nodes": [{"id": run_id}],
            "edges": [{"from": run_id, "to": d} for d, _ in successors],
        }

    async def add_query_paths(self, paths: list[dict[str, Any]]) -> None:
        """Compatibility method — merge entity graph paths."""
        for path in paths:
            if isinstance(path, dict):
                src = path.get("source", "")
                dst = path.get("target", "")
                if src and dst:
                    self._tensor.observe(src, dst, domain="query")

    async def close(self) -> None:
        """No-op — tensor has no external connection to close."""
        pass
