"""Graph Store — Neo4j persistence for the agent mesh graph.

Stores the complete execution engine graph:
    Agent → Capability → Precondition/Effect

Nodes:
    (:Agent {name, role})
    (:Capability {name, modality, confidence})
    (:Precondition {name})
    (:Effect {name})

Edges:
    (:Agent)-[:PROVIDES]->(:Capability)
    (:Capability)-[:REQUIRES]->(:Precondition)
    (:Capability)-[:PRODUCES]->(:Effect)
    (:Capability)-[:ENABLES]->(:Capability)
    (:Capability)-[:DISABLES]->(:Capability)
    (:Agent)-[:DEPENDS_ON]->(:Agent)  # agent-to-agent dependencies
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Neo4j cannot parameterize labels or relationship types — they must be
# interpolated into the query string. Everything interpolated this way is
# forced through this allowlist so planner/LLM/query-path-derived names can
# never inject Cypher. Anything not matching is rejected by the callers.
_SAFE_CYPHER_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_cypher_ident(name: str) -> str | None:
    """Return name if it is a valid, injection-safe Cypher identifier, else None."""
    return name if isinstance(name, str) and _SAFE_CYPHER_IDENT.match(name) else None


class GraphStore:
    """Neo4j-backed graph store for the agent mesh."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self._uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self._user = user or os.getenv("NEO4J_USER", "neo4j")
        # os.getenv(var, default) only falls back when the var is fully
        # unset — .env files that set NEO4J_PASSWORD= (present, empty) make
        # it return "" instead, silently authenticating with a blank
        # password. Treat "" as unset, but never fall back to a baked-in
        # credential: an unset password must fail loudly, not authenticate.
        self._password = password or os.getenv("NEO4J_PASSWORD") or ""
        self._driver: Any = None

    async def connect(self) -> None:
        if not self._password:
            logger.error(
                "GraphStore disabled: NEO4J_PASSWORD is unset. Export it rather than relying on a default credential.",
            )
            self._driver = None
            return
        try:
            from neo4j import AsyncGraphDatabase

            driver = AsyncGraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
            )
            # Only cache the driver on successful creation
            # If exception occurs below, driver stays None and next call retries
            self._driver = driver
            logger.info("GraphStore connected to %s", self._uri)
        except ImportError:
            logger.warning("neo4j driver not installed — GraphStore disabled")
            # Leave _driver as None (don't cache failure)
        except Exception as exc:
            logger.warning("GraphStore connect failed: %s (will retry on next operation)", exc)
            # Leave _driver as None (don't cache failure)
            # Next operation will see _driver=None and automatically retry

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    def is_connected(self) -> bool:
        return self._driver is not None

    # ── Write ────────────────────────────────────────────────────────────────

    async def sync_graph(self, graph: dict[str, Any], *, append: bool = False) -> None:
        """Replace or append to the execution graph in Neo4j.

        append=True: MERGE nodes/edges (accumulates plans)
        append=False: DELETE + CREATE (replaces graph)
        """
        if not self.is_connected():
            logger.debug("GraphStore not connected, attempting to reconnect...")
            await self.connect()
            if not self.is_connected():
                logger.warning("GraphStore reconnection failed, skipping sync")
                return

        async with self._driver.session() as session:
            if not append:
                await session.run("MATCH (n) WHERE n:_PlanStep DETACH DELETE n")

            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            node_aliases: dict[str, str] = {}
            graph_metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
            graph_id = graph.get("graph_id") or graph_metadata.get("graph_id", "")
            run_id = graph_metadata.get("run_id", "")

            for node in nodes:
                label = self._node_label(node.get("type", ""))
                props = self._node_props(node)
                props["graph_id"] = graph_id
                props["run_id"] = run_id
                node_id = props.get("id", "")
                for alias in self._node_aliases(node):
                    node_aliases.setdefault(alias, node_id)
                if append and node_id:
                    # MERGE: preserve graph topology identity while `name`
                    # remains the human-facing display value.
                    await session.run(
                        f"""
                        MERGE (n:{label}:_PlanStep {{graph_id: $graph_id, id: $node_id}})
                        SET n += $props
                        SET n.agent_type = $agent_type
                        SET n.node_type = $node_type
                        """,
                        graph_id=graph_id,
                        node_id=node_id,
                        props=props,
                        agent_type=props.get("agent", label),
                        node_type=props.get("node_type", props.get("type", "agent")),
                    )
                else:
                    # CREATE: new node
                    await session.run(
                        f"CREATE (n:{label} $props) SET n:_PlanStep",
                        props=props,
                    )

            created_edges = 0
            skipped_edges = 0

            for edge in edges:
                rel = self._edge_type(edge.get("type") or edge.get("rel") or "depends_on")
                raw_src = str(edge.get("from") or edge.get("src") or "")
                raw_dst = str(edge.get("to") or edge.get("dst") or "")
                src_key = node_aliases.get(raw_src, raw_src)
                dst_key = node_aliases.get(raw_dst, raw_dst)
                if src_key and dst_key:
                    result = await session.run(
                        f"""
                        MATCH (a:_PlanStep)
                        WHERE ($graph_id = '' OR a.graph_id = $graph_id)
                          AND (
                              a.id = $src_key
                              OR a.node_id = $src_key
                              OR a.name = $src_key
                              OR a.agent = $src_key
                              OR a.label = $src_key
                          )
                        MATCH (b:_PlanStep)
                        WHERE ($graph_id = '' OR b.graph_id = $graph_id)
                          AND (
                              b.id = $dst_key
                              OR b.node_id = $dst_key
                              OR b.name = $dst_key
                              OR b.agent = $dst_key
                              OR b.label = $dst_key
                          )
                        MERGE (a)-[r:{rel}]->(b)
                        SET r += $props
                        RETURN count(r) AS edge_count
                        """,
                        graph_id=graph_id,
                        src_key=src_key,
                        dst_key=dst_key,
                        props={
                            "graph_id": graph_id,
                            "run_id": run_id,
                            "source": raw_src,
                            "target": raw_dst,
                            "source_id": src_key,
                            "target_id": dst_key,
                        },
                    )
                    record = await result.single()
                    edge_count = int(record["edge_count"] or 0) if record else 0
                    if edge_count:
                        created_edges += 1
                    else:
                        skipped_edges += 1
                        logger.warning(
                            "GraphStore skipped edge with unmatched endpoints: %s -> %s",
                            raw_src,
                            raw_dst,
                        )
                else:
                    skipped_edges += 1
                    logger.warning("GraphStore skipped malformed edge: %s", edge)

        logger.info(
            "GraphStore synced: %d nodes, %d/%d edges created, %d skipped",
            len(nodes),
            created_edges,
            len(edges),
            skipped_edges,
        )

    async def sync_node_state(self, node_id: str, state: str, **props: Any) -> None:
        """Update a single execution node's state in Neo4j (live sync).

        Called on every ProcessManager tick so the graph is never stale.
        """
        if not self.is_connected():
            return

        async with self._driver.session() as session:
            await session.run(
                "MATCH (n:_PlanStep) "
                "WHERE n.id = $name "
                "   OR n.node_id = $name "
                "   OR n.name = $name "
                "   OR n.agent = $name "
                "   OR n.label = $name "
                "SET n.state = $state, n.updated_at = timestamp()",
                name=node_id,
                state=state,
            )
            for key, value in props.items():
                await session.run(
                    "MATCH (n:_PlanStep) "
                    "WHERE n.id = $name "
                    "   OR n.node_id = $name "
                    "   OR n.name = $name "
                    "   OR n.agent = $name "
                    "   OR n.label = $name "
                    f"SET n.{key} = $value",
                    name=node_id,
                    value=value,
                )

    async def upsert_agent_dependencies(self, dependencies: list[dict[str, str]]) -> None:
        """Store agent-to-agent dependency edges.

        Each dict: {"from": agent_type, "to": agent_type, "reason": str}
        """
        if not self.is_connected():
            return

        async with self._driver.session() as session:
            for dep in dependencies:
                await session.run(
                    "MATCH (a:Agent {_PlanStep: true, name: $src}), "
                    "      (b:Agent {_PlanStep: true, name: $dst}) "
                    "CREATE (a)-[:DEPENDS_ON {reason: $reason}]->(b)",
                    src=dep["from"],
                    dst=dep["to"],
                    reason=dep.get("reason", ""),
                )

    # ── Capability mesh ──────────────────────────────────────────────────────
    # Persistent record of "capability X is satisfied by composing sub-
    # capabilities [a, b, c] in this order" — discovered once (by an LLM
    # decomposing a capability nothing could satisfy directly) and reused by
    # every future request for that capability, across process restarts,
    # rather than re-deriving the same decomposition from scratch each time.
    # Distinct from the :_PlanStep nodes elsewhere in this file (those
    # are one specific run's planner output); :Capability nodes here are the
    # durable, growing ontology multiple runs' compositions accumulate into.

    async def save_capability_composition(self, capability: str, sub_capabilities: list[str]) -> None:
        if not self.is_connected() or not sub_capabilities:
            return
        async with self._driver.session() as session:
            await session.run("MERGE (c:Capability {name: $capability})", capability=capability)
            for order, sub in enumerate(sub_capabilities):
                await session.run(
                    """
                    MERGE (c:Capability {name: $capability})
                    MERGE (s:Capability {name: $sub})
                    MERGE (c)-[r:REQUIRES]->(s)
                    SET r.order = $order
                    """,
                    capability=capability,
                    sub=sub,
                    order=order,
                )
        logger.info(
            "GraphStore: persisted capability composition %s -> %s",
            capability,
            sub_capabilities,
        )

    async def get_capability_composition(self, capability: str) -> list[str] | None:
        """Previously-discovered decomposition for this capability, in
        order, or None if the mesh has no record of one (capability is
        either atomic or has never been decomposed before)."""
        if not self.is_connected():
            return None
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Capability {name: $capability})-[r:REQUIRES]->(s:Capability)
                RETURN s.name AS sub ORDER BY r.order
                """,
                capability=capability,
            )
            subs = [record["sub"] async for record in result]
        return subs or None

    @staticmethod
    def _slug(name: str) -> str:
        """Normalized token for canonical ids — lowercase, dot-separated."""
        import re

        return re.sub(r"[^a-z0-9]+", ".", (name or "").lower()).strip(".")[:60]

    # External provider tiers that legitimately appear as (:Provider) nodes.
    # Broca is deliberately NOT here: Broca is the agent runtime/interface
    # layer, not a provider — a locally-resolved capability is already fully
    # represented by the Agent -> HAS_CAPABILITY -> Capability structure,
    # and inserting "broca" created a meaningless hub node every local
    # resolution pointed at.
    _EXTERNAL_PROVIDERS = frozenset({"n8n", "ard", "openclaw", "nanda", "llm_provider", "sdlc"})

    async def register_capability_provider(self, agent_name: str, capability: str, provider_type: str = "") -> None:
        """Record that a capability was resolved by an EXTERNAL provider —
        Layer 3 (Capability Graph) schema:

            (:Capability)-[:RESOLVED_BY]->(:Provider)
            (:Provider)-[:EXPOSES]->(:Tool)

        `agent_name` is the implementation identifier the resolver reports:
        "provider:tool" (e.g. "n8n:InvoiceAgent") or a bare tier name
        ("llm_provider"). Local (Broca) resolutions are NOT recorded here —
        Broca is runtime infrastructure, never a semantic-graph citizen; a
        locally-resolved capability's provenance is already carried by the
        Agent -> HAS_CAPABILITY -> Capability structure. All nodes get
        namespaced canonical ids (cap.* / provider.* / tool.*) so no id can
        ever collide across layers.
        """
        if not self.is_connected() or not agent_name or not capability:
            return
        if ":" in agent_name:
            provider_name, _, tool_name = agent_name.partition(":")
        elif agent_name in self._EXTERNAL_PROVIDERS:
            provider_name, tool_name = agent_name, agent_name
        else:
            provider_name, tool_name = (provider_type or "broca"), agent_name
        if provider_name not in self._EXTERNAL_PROVIDERS:
            return
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (c:Capability {name: $capability})
                ON CREATE SET c.id = $cap_id
                SET c.rank = coalesce(c.rank, 0) + 1
                MERGE (p:Provider {name: $provider_name})
                ON CREATE SET p.id = $provider_id
                MERGE (t:Tool {name: $tool_name})
                ON CREATE SET t.id = $tool_id
                MERGE (c)-[r:RESOLVED_BY]->(p)
                SET r.last_confirmed = timestamp(), r.tool = $tool_name
                MERGE (p)-[:EXPOSES]->(t)
                """,
                capability=capability,
                provider_name=provider_name,
                tool_name=tool_name,
                cap_id=f"cap.{self._slug(capability)}",
                provider_id=f"provider.{self._slug(provider_name)}",
                tool_id=f"tool.{self._slug(tool_name)}",
            )

    async def find_capability_providers(self, capability: str) -> list[dict[str, Any]]:
        """Providers previously confirmed (via register_capability_provider)
        to resolve this capability, most-recently-confirmed first. The
        "agent" key carries the tool identifier for callers written against
        the old (:Agent)-[:PROVIDES]-> schema."""
        if not self.is_connected():
            return []
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Capability {name: $capability})-[r:RESOLVED_BY]->(p:Provider)
                RETURN p.name AS provider, r.tool AS tool, r.tool AS agent, r.last_confirmed AS last_confirmed
                ORDER BY r.last_confirmed DESC
                """,
                capability=capability,
            )
            return [dict(record) async for record in result]

    async def link_capability_generalization(self, specific: str, general: str) -> None:
        """Record that a specific, planner-invented capability name (e.g.
        "DoctorAvailabilityAgent") is actually a special case of an
        existing, already-real capability/agent (e.g. "appointment") —
        (:Capability {name: specific})-[:GENERALIZES]->(:Capability {name:
        general}). Without this, semantically identical requests that
        happen to get differently-worded planner node names each build
        their own composition/generation from scratch forever, even though
        a real implementation already covers the general case.
        """
        if not self.is_connected() or not specific or not general or specific == general:
            return
        async with self._driver.session() as session:
            # The generalization fact itself is ONTOLOGY knowledge — it lives
            # on (:OntologyConcept)-[:IS_A]-> nodes, never as a Capability-to-
            # Capability edge. The earlier schema MERGEd a Capability named
            # after the specific (planner-invented agent) name, which is
            # exactly the agent/capability conflation this model prohibits:
            # "AppointmentSlotAgent" is an Agent, and no Capability of that
            # name may exist. Only the GENERAL side becomes a Capability —
            # it is a real, resolvable thing ("appointment").
            await session.run(
                """
                MERGE (s:OntologyConcept {name: $specific})
                ON CREATE SET s.id = $specific_id
                MERGE (g:OntologyConcept {name: $general})
                ON CREATE SET g.id = $general_id
                MERGE (s)-[:IS_A]->(g)
                """,
                specific=specific,
                general=general,
                specific_id=f"concept.{self._slug(specific)}",
                general_id=f"concept.{self._slug(general)}",
            )
            await session.run(
                """
                MERGE (c:Capability {name: $general})
                ON CREATE SET c.id = $cap_id
                SET c.rank = coalesce(c.rank, 0) + 1
                """,
                general=general,
                cap_id=f"cap.{self._slug(general)}",
            )
        logger.info(
            "GraphStore: linked capability generalization %s -> %s (ontology IS_A)",
            specific,
            general,
        )

    async def find_generalization(self, specific: str) -> str | None:
        """The existing, real capability/agent this specific name was
        previously linked to, if any — read from the ontology (IS_A), which
        is where link_capability_generalization records it."""
        if not self.is_connected():
            return None
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (s:OntologyConcept {name: $specific})-[:IS_A]->(g:OntologyConcept) RETURN g.name AS general LIMIT 1",
                specific=specific,
            )
            record = await result.single()
            return record["general"] if record else None

    async def compute_capability_pagerank(self, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
        """Standard iterative PageRank over the ontology's IS_A edges (a
        specific concept "votes" for the general one it reduces to) —
        computed in Python and persisted onto each OntologyConcept AND onto
        the same-named Capability where one exists, since this Neo4j
        instance has no GDS plugin for a native graph-algorithm call.

        Generalization edges live in the ontology now (see
        link_capability_generalization) — Capability nodes carry no
        GENERALIZES edges of their own, so ranking runs over concepts and
        the scores are projected onto the real capabilities they name.

        The result identifies "computational roots": real capabilities
        (like "appointment") that many differently-worded, planner-invented
        names reduce to — the nodes actually worth having a fast, direct
        implementation for, versus one-off names nothing else ever refers
        back to.
        """
        if not self.is_connected():
            return {}
        async with self._driver.session() as session:
            result = await session.run("MATCH (o:OntologyConcept) RETURN o.name AS name")
            nodes = [record["name"] async for record in result]
            result = await session.run(
                "MATCH (s:OntologyConcept)-[:IS_A]->(g:OntologyConcept) RETURN s.name AS src, g.name AS dst"
            )
            edges = [(record["src"], record["dst"]) async for record in result]

        if not nodes:
            return {}

        n = len(nodes)
        rank = {name: 1.0 / n for name in nodes}
        outgoing: dict[str, list[str]] = {name: [] for name in nodes}
        for src, dst in edges:
            outgoing.setdefault(src, []).append(dst)

        for _ in range(iterations):
            new_rank = {name: (1 - damping) / n for name in nodes}
            for name in nodes:
                targets = outgoing.get(name, [])
                if not targets:
                    # Dangling node (no outgoing edge — often a "root", like
                    # "appointment" itself) redistributes its rank evenly
                    # rather than losing it, the standard PageRank fix.
                    share = damping * rank[name] / n
                    for other in nodes:
                        new_rank[other] += share
                    continue
                share = damping * rank[name] / len(targets)
                for dst in targets:
                    new_rank[dst] = new_rank.get(dst, (1 - damping) / n) + share
            rank = new_rank

        async with self._driver.session() as session:
            for name, score in rank.items():
                await session.run(
                    "MATCH (c:Capability {name: $name}) SET c.pagerank = $score",
                    name=name,
                    score=score,
                )

        return rank

    async def get_top_ranked_capabilities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Highest-pagerank capabilities — the mesh's "computational roots."
        Call compute_capability_pagerank() first for a fresh ranking; falls
        back to the simpler incremental `rank` counter (bumped on every new
        generalization link — see link_capability_generalization) if
        pagerank has never been computed."""
        if not self.is_connected():
            return []
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Capability)
                RETURN c.name AS name, coalesce(c.pagerank, 0.0) AS pagerank, coalesce(c.rank, 0) AS rank
                ORDER BY pagerank DESC, rank DESC
                LIMIT $limit
                """,
                limit=limit,
            )
            return [dict(record) async for record in result]

    # ── Three-Layer Graph Architecture ──────────────────────────────────────
    #
    # Layer 1 — Execution Mesh: (:ExecutionGraph) nodes, one per REUSABLE
    #   workflow (identified by graph_key, a hash of its agent set — two
    #   runs of "schedule an appointment" that planned the same agents are
    #   the SAME mesh node, with run_count/last_run_id tracking instances).
    #   Edges: COMPOSES / REFERENCES / GENERALIZES / SIMILAR_TO — mesh nodes
    #   never link directly to Capabilities; similarity is derived through
    #   Layer 2/3 traversal and materialized as SIMILAR_TO {weight}.
    #   This layer is learned from completed executions and never executed
    #   directly.
    #
    # Layer 2 — Execution Graph: (:ExecutionGraph)-[:HAS_AGENT]->(:Agent),
    #   with [:DEPENDS_ON {graph_key}] edges between Agents carrying
    #   execution order. Agent nodes are canonical (one per agent name,
    #   shared across graphs); the graph_key property on DEPENDS_ON scopes
    #   each workflow's ordering. Nothing else belongs in this layer.
    #   (The per-run plan-step instances written by sync_graph() carry the
    #   internal `_PlanStep` marker label and run_id — they are the run LOG
    #   that /execute recovers and the comparator clones, not part of the
    #   three-layer model.)
    #
    # Layer 3 — Capability Graph, private to each Agent:
    #   (:Agent)-[:HAS_CAPABILITY]->(:Capability)
    #   (:Capability)-[:RESOLVED_BY]->(:Provider)
    #   (:Provider)-[:EXPOSES]->(:Tool)
    #   (:Capability)-[:REQUIRES]->(:Capability)      (composition)
    #   NOTE: generalization is NOT a Capability edge — it is ontology
    #   knowledge, (:OntologyConcept)-[:IS_A]->, and only its GENERAL side
    #   ever becomes a Capability. Broca never appears anywhere in this
    #   graph: it is the runtime, not a provider.
    #
    # Ontology — (:OntologyConcept)-[:IS_A]->(:OntologyConcept), mirrored
    #   from capability generalizations; stored on its own label so meaning
    #   never mixes with execution structure.

    @staticmethod
    def _graph_key(agent_names: list[str]) -> str:
        """Stable identity for a reusable workflow: hash of its sorted agent
        set. Two runs that planned the same agents ARE the same workflow."""
        import hashlib

        return hashlib.sha1("|".join(sorted(set(agent_names))).encode()).hexdigest()[:16]

    @staticmethod
    def _goal_key(goal: str) -> str:
        """Workflow identity = the GOAL, normalized — not the agent set.
        The planner is non-deterministic and names slightly different agent
        sets for the same goal on every run; keying by agent-set hash minted
        a fresh "workflow" per run (4 mesh nodes all named "schedule a
        patient appointment with the doctor" — pure duplication). Keying by
        goal joins them: one workflow node, whose structure is the latest
        successful run's, with run_count tracking repetition.
        """
        import hashlib

        normalized = " ".join((goal or "").lower().split())
        return hashlib.sha1(normalized.encode()).hexdigest()[:16]

    async def record_execution_graph(
        self,
        run_id: str,
        goal: str,
        agent_names: list[str],
        agent_edges: list[tuple[str, str]] | None = None,
    ) -> str | None:
        """Record a completed run into all three layers at once:

        Layer 1: MERGE the (:ExecutionGraph {graph_key}) mesh node, keyed by
        the normalized GOAL — repeat runs of the same goal update the one
        workflow node (structure = latest run, run_count += 1).
        Layer 2: canonical (:Agent) nodes joined on name, HAS_AGENT edges
        from the mesh node, DEPENDS_ON {graph_key} ordering — the previous
        run's structure for this workflow is replaced, not accumulated.
        Layer 3: HAS_CAPABILITY ONLY where the ontology actually knows the
        agent's capability (an IS_A generalization to a real capability).
        An agent with no known capability gets NO edge — a Capability is a
        distinct concept, never a mirror of an agent's name; fabricating
        one per agent is what filled the graph with same-named
        Agent/Capability pairs.

        Then rematerializes SIMILAR_TO edges (weight = shared agents).

        Best-effort by contract: called after a run completes, must never
        fail the response it records. Returns the graph_key, or None.
        """
        if not self.is_connected() or not run_id or not agent_names:
            return None
        graph_key = self._goal_key(goal)
        async with self._driver.session() as session:
            # Replace, don't accumulate: this workflow's structure is the
            # latest run's. Old HAS_AGENT/DEPENDS_ON for this key go first.
            await session.run(
                "MATCH (e:ExecutionGraph {graph_key: $graph_key})-[r:HAS_AGENT]->() DELETE r",
                graph_key=graph_key,
            )
            await session.run(
                "MATCH (:Agent)-[r:DEPENDS_ON {graph_key: $graph_key}]->(:Agent) DELETE r",
                graph_key=graph_key,
            )
            await session.run(
                """
                MERGE (e:ExecutionGraph {graph_key: $graph_key})
                ON CREATE SET e.name = $goal, e.created_at = timestamp(), e.run_count = 0, e.id = $graph_id
                SET e.last_run_id = $run_id, e.last_run = timestamp(),
                    e.run_count = coalesce(e.run_count, 0) + 1,
                    e.agent_count = $agent_count
                WITH e
                UNWIND $agents AS agent_name
                MERGE (a:Agent {name: agent_name})
                ON CREATE SET a.id = 'agent.' + $agent_id_prefix + agent_name
                MERGE (e)-[:HAS_AGENT]->(a)
                """,
                graph_key=graph_key,
                goal=(goal or "")[:500],
                run_id=run_id,
                agent_count=len(set(agent_names)),
                agents=sorted(set(agent_names)),
                graph_id=f"graph.{self._slug(goal)}",
                agent_id_prefix="",
            )
            # Namespaced canonical ids on agents (slugified after the merge —
            # ON CREATE above can't call a Python helper, so normalize here).
            await session.run(
                """
                UNWIND $agents AS agent_name
                MATCH (a:Agent {name: agent_name})
                WHERE a.id IS NULL OR NOT a.id STARTS WITH 'agent.'
                SET a.id = 'agent.' + toLower(agent_name)
                """,
                agents=sorted(set(agent_names)),
            )
            for src, dst in agent_edges or []:
                if src and dst:
                    await session.run(
                        """
                        MATCH (a:Agent {name: $src}), (b:Agent {name: $dst})
                        MERGE (a)-[r:DEPENDS_ON {graph_key: $graph_key}]->(b)
                        """,
                        src=src,
                        dst=dst,
                        graph_key=graph_key,
                    )
            # Layer 3: link agent -> capability only through real ontology
            # knowledge. No IS_A known => no edge, and no Capability node.
            await session.run(
                """
                UNWIND $agents AS agent_name
                MATCH (a:Agent {name: agent_name})
                MATCH (:OntologyConcept {name: agent_name})-[:IS_A]->(g:OntologyConcept)
                MERGE (c:Capability {name: g.name})
                MERGE (a)-[:HAS_CAPABILITY]->(c)
                """,
                agents=sorted(set(agent_names)),
            )
            # Mesh similarity from shared agents (capabilities are sparse by
            # design now — most agents legitimately have no known capability
            # yet, so shared agents are the denser, more honest signal).
            await session.run(
                """
                MATCH (e1:ExecutionGraph {graph_key: $graph_key})-[:HAS_AGENT]->(a:Agent)<-[:HAS_AGENT]-(e2:ExecutionGraph)
                WITH e1, e2, count(DISTINCT a) AS weight
                MERGE (e1)-[s:SIMILAR_TO]->(e2)
                SET s.weight = weight
                MERGE (e2)-[s2:SIMILAR_TO]->(e1)
                SET s2.weight = weight
                """,
                graph_key=graph_key,
            )
        logger.info(
            "GraphStore: recorded ExecutionGraph %s (run=%s, %d agents)",
            graph_key,
            run_id,
            len(set(agent_names)),
        )
        return graph_key

    async def find_similar_execution_graphs(
        self,
        capability_names: list[str],
        min_overlap: int = 2,
        exclude_run_id: str = "",
    ) -> list[dict[str, Any]]:
        """Mesh nodes whose agents/capabilities overlap the given names —
        "has a goal like this already been solved." Matches either the
        Agent name or the Capability it resolves through, so both planner
        agent names and generalized capability names find prior work.
        run_id in the result is the workflow's last run (recoverable via
        get_graph_by_run_id)."""
        if not self.is_connected() or not capability_names:
            return []
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:ExecutionGraph)-[:HAS_AGENT]->(a:Agent)
                OPTIONAL MATCH (a)-[:HAS_CAPABILITY]->(c:Capability)
                WITH e, a, c
                WHERE a.name IN $names OR c.name IN $names
                WITH e, count(DISTINCT a) AS overlap
                WHERE overlap >= $min_overlap AND e.last_run_id <> $exclude_run_id
                RETURN e.graph_key AS graph_key, e.last_run_id AS run_id, e.name AS goal,
                       overlap, coalesce(e.mesh_pagerank, 0.0) AS mesh_pagerank,
                       e.run_count AS run_count
                ORDER BY overlap DESC, mesh_pagerank DESC
                LIMIT 10
                """,
                names=capability_names,
                min_overlap=min_overlap,
                exclude_run_id=exclude_run_id,
            )
            return [dict(record) async for record in result]

    async def find_execution_graph_for_capability(self, capability: str) -> dict[str, Any] | None:
        """Recursive-composition lookup: is there an existing reusable
        ExecutionGraph that fulfills this capability? Matches a mesh node
        whose name/goal contains the capability tokens, or that HAS_AGENT an
        agent resolving through the capability. Returns {graph_key, run_id,
        goal} for the best match (most-run first), or None."""
        if not self.is_connected() or not capability:
            return None
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:ExecutionGraph)
                OPTIONAL MATCH (e)-[:HAS_AGENT]->(a:Agent)-[:HAS_CAPABILITY]->(c:Capability)
                WITH e, collect(DISTINCT c.name) AS caps, collect(DISTINCT a.name) AS agents
                WHERE toLower(e.name) CONTAINS toLower($capability)
                   OR $capability IN caps OR $capability IN agents
                RETURN e.graph_key AS graph_key, e.last_run_id AS run_id, e.name AS goal,
                       coalesce(e.run_count, 0) AS run_count
                ORDER BY run_count DESC
                LIMIT 1
                """,
                capability=capability,
            )
            record = await result.single()
            return dict(record) if record else None

    async def compute_execution_mesh_pagerank(self, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
        """PageRank over Layer 1's materialized SIMILAR_TO {weight} edges.
        High-pagerank mesh nodes are the workflows other workflows most
        resemble — the reuse backbone, mirroring compute_capability_pagerank
        one layer down."""
        if not self.is_connected():
            return {}
        async with self._driver.session() as session:
            result = await session.run("MATCH (e:ExecutionGraph) RETURN e.graph_key AS k")
            nodes = [record["k"] async for record in result]
            result = await session.run("""
                MATCH (e1:ExecutionGraph)-[s:SIMILAR_TO]->(e2:ExecutionGraph)
                RETURN e1.graph_key AS a, e2.graph_key AS b, coalesce(s.weight, 1) AS weight
                """)
            edges = [(record["a"], record["b"], record["weight"]) async for record in result]

        if not nodes:
            return {}

        n = len(nodes)
        rank = {name: 1.0 / n for name in nodes}
        outgoing: dict[str, list[tuple[str, int]]] = {name: [] for name in nodes}
        for a, b, w in edges:
            outgoing.setdefault(a, []).append((b, w))

        for _ in range(iterations):
            new_rank = {name: (1 - damping) / n for name in nodes}
            for name in nodes:
                targets = outgoing.get(name, [])
                total_w = sum(w for _, w in targets)
                if not total_w:
                    share = damping * rank[name] / n
                    for other in nodes:
                        new_rank[other] += share
                    continue
                for dst, w in targets:
                    new_rank[dst] = new_rank.get(dst, (1 - damping) / n) + damping * rank[name] * (w / total_w)
            rank = new_rank

        async with self._driver.session() as session:
            for k, score in rank.items():
                await session.run(
                    "MATCH (e:ExecutionGraph {graph_key: $k}) SET e.mesh_pagerank = $score",
                    k=k,
                    score=score,
                )

        return rank

    async def get_top_ranked_execution_graphs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Highest-mesh-pagerank reusable workflows — the Execution Mesh's
        computational roots."""
        if not self.is_connected():
            return []
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:ExecutionGraph)
                RETURN e.graph_key AS graph_key, e.name AS goal, e.last_run_id AS run_id,
                       coalesce(e.mesh_pagerank, 0.0) AS mesh_pagerank,
                       coalesce(e.run_count, 0) AS run_count, e.agent_count AS agent_count
                ORDER BY mesh_pagerank DESC, run_count DESC
                LIMIT $limit
                """,
                limit=limit,
            )
            return [dict(record) async for record in result]

    # ── Layer views (one layer at a time, per the visualization contract) ──

    async def get_execution_mesh_view(self) -> dict[str, Any]:
        """Layer 1 only: ExecutionGraph nodes + mesh-level edges."""
        if not self.is_connected():
            return {"nodes": [], "edges": []}
        async with self._driver.session() as session:
            result = await session.run("""
                MATCH (e:ExecutionGraph)
                OPTIONAL MATCH (e)-[r:COMPOSES|REFERENCES|GENERALIZES|SIMILAR_TO]->(e2:ExecutionGraph)
                RETURN e, r, e2
                """)
            nodes, edges, seen = [], [], set()
            async for record in result:
                for key in ("e", "e2"):
                    n = record[key]
                    if n and n.get("graph_key") not in seen:
                        seen.add(n.get("graph_key"))
                        nodes.append(
                            {
                                "graph_key": n.get("graph_key"),
                                "name": n.get("name"),
                                "run_count": n.get("run_count", 0),
                                "mesh_pagerank": n.get("mesh_pagerank", 0.0),
                            }
                        )
                r = record["r"]
                if r:
                    edges.append(
                        {
                            "from": r.start_node.get("graph_key"),
                            "to": r.end_node.get("graph_key"),
                            "type": r.type,
                            "weight": r.get("weight", 1),
                        }
                    )
            return {"nodes": nodes, "edges": edges}

    async def get_execution_graph_view(self, graph_key: str) -> dict[str, Any]:
        """Layer 2 only: the Agents of one ExecutionGraph + its DEPENDS_ON
        ordering."""
        if not self.is_connected():
            return {"nodes": [], "edges": []}
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e:ExecutionGraph {graph_key: $graph_key})-[:HAS_AGENT]->(a:Agent)
                OPTIONAL MATCH (a)-[r:DEPENDS_ON {graph_key: $graph_key}]->(b:Agent)
                RETURN e.name AS goal, a, r, b
                """,
                graph_key=graph_key,
            )
            nodes, edges, seen, goal = [], [], set(), ""
            async for record in result:
                goal = record["goal"] or goal
                for key in ("a", "b"):
                    n = record[key]
                    if n and n.get("name") not in seen:
                        seen.add(n.get("name"))
                        nodes.append({"name": n.get("name")})
                if record["r"]:
                    edges.append(
                        {
                            "from": record["r"].start_node.get("name"),
                            "to": record["r"].end_node.get("name"),
                            "type": "DEPENDS_ON",
                        }
                    )
            return {
                "graph_key": graph_key,
                "goal": goal,
                "nodes": nodes,
                "edges": edges,
            }

    async def get_agent_capability_graph(self, agent_name: str) -> dict[str, Any]:
        """Layer 3 only: one Agent's private capability graph — its
        Capabilities, the Providers that resolve them, and the Tools those
        providers expose."""
        if not self.is_connected():
            return {"agent": agent_name, "capabilities": []}
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Agent {name: $agent_name})-[:HAS_CAPABILITY]->(c:Capability)
                OPTIONAL MATCH (c)-[rb:RESOLVED_BY]->(p:Provider)
                OPTIONAL MATCH (c)-[:REQUIRES]->(req:Capability)
                RETURN c.name AS capability, coalesce(c.pagerank, 0.0) AS pagerank,
                       collect(DISTINCT {provider: p.name, tool: rb.tool}) AS providers,
                       collect(DISTINCT req.name) AS requires
                """,
                agent_name=agent_name,
            )
            capabilities = []
            async for record in result:
                capabilities.append(
                    {
                        "capability": record["capability"],
                        "pagerank": record["pagerank"],
                        "providers": [p for p in record["providers"] if p.get("provider")],
                        "requires": [r for r in record["requires"] if r],
                    }
                )
            return {"agent": agent_name, "capabilities": capabilities}

    async def get_ontology_view(self) -> dict[str, Any]:
        """Ontology only: OntologyConcept nodes + IS_A/PART_OF/SPECIALIZES/
        GENERALIZES edges."""
        if not self.is_connected():
            return {"nodes": [], "edges": []}
        async with self._driver.session() as session:
            result = await session.run("""
                MATCH (o:OntologyConcept)
                OPTIONAL MATCH (o)-[r:IS_A|PART_OF|SPECIALIZES|GENERALIZES]->(o2:OntologyConcept)
                RETURN o.name AS name, type(r) AS rel, o2.name AS target
                """)
            nodes, edges, seen = [], [], set()
            async for record in result:
                for name in (record["name"], record["target"]):
                    if name and name not in seen:
                        seen.add(name)
                        nodes.append({"name": name})
                if record["rel"] and record["target"]:
                    edges.append(
                        {
                            "from": record["name"],
                            "to": record["target"],
                            "type": record["rel"],
                        }
                    )
            return {"nodes": nodes, "edges": edges}

    async def migrate_enforce_canonical_semantics(self) -> dict[str, int]:
        """Architecture-correction migration (idempotent):

        1. Capability nodes that mirror an Agent name are the agent/
           capability conflation — deleted. A node is an Agent or a
           Capability, never both identities under two labels.
        2. Capability-to-Capability GENERALIZES edges — deleted; that fact
           is ontology knowledge and already lives on OntologyConcept IS_A.
        3. The 'broca' Provider hub and any Tool only it exposed — deleted;
           Broca is the runtime/interface layer, not a semantic-graph
           citizen.
        4. Orphaned Capabilities (no remaining relationships) — deleted.
        5. All ExecutionGraph nodes — deleted (duplicate per-agent-set
           workflows); the caller rebuilds them goal-keyed from PlanStore.
        6. Namespaced canonical ids stamped on every surviving node.
        """
        if not self.is_connected():
            return {}
        counts: dict[str, int] = {}
        async with self._driver.session() as session:
            r = await session.run(
                "MATCH (c:Capability) WHERE EXISTS { MATCH (:Agent {name: c.name}) } DETACH DELETE c RETURN count(c) AS n"
            )
            counts["mirror_capabilities_deleted"] = int((await r.single())["n"] or 0)

            r = await session.run("MATCH (:Capability)-[g:GENERALIZES]->(:Capability) DELETE g RETURN count(g) AS n")
            counts["capability_generalizes_deleted"] = int((await r.single())["n"] or 0)

            r = await session.run("""
                MATCH (p:Provider {name: 'broca'})
                OPTIONAL MATCH (p)-[:EXPOSES]->(t:Tool)
                WHERE NOT EXISTS { MATCH (other:Provider)-[:EXPOSES]->(t) WHERE other.name <> 'broca' }
                DETACH DELETE t
                WITH DISTINCT p
                DETACH DELETE p
                RETURN 1 AS n
                """)
            rec = await r.single()
            counts["broca_removed"] = 1 if rec else 0

            r = await session.run("MATCH (c:Capability) WHERE NOT (c)--() DETACH DELETE c RETURN count(c) AS n")
            counts["orphan_capabilities_deleted"] = int((await r.single())["n"] or 0)

            r = await session.run("MATCH (e:ExecutionGraph) DETACH DELETE e RETURN count(e) AS n")
            counts["execution_graphs_reset"] = int((await r.single())["n"] or 0)

            for label, prefix in [
                ("Agent", "agent"),
                ("Capability", "cap"),
                ("Provider", "provider"),
                ("Tool", "tool"),
                ("OntologyConcept", "concept"),
            ]:
                await session.run(f"MATCH (n:{label}) WHERE n.id IS NULL SET n.id = '{prefix}.' + toLower(n.name)")
        logger.info("GraphStore: canonical-semantics migration: %s", counts)
        return counts

    async def migrate_to_three_layer_schema(self) -> dict[str, int]:
        """One-time migration from the interim schema (MeshRun / USES / STEP
        / RESOLVES / (:Agent)-[:PROVIDES]->) to the three-layer model.
        Idempotent — safe to run on an already-migrated database."""
        if not self.is_connected():
            return {}
        counts: dict[str, int] = {}
        async with self._driver.session() as session:
            # Old provider-Agents -> Provider/Tool + RESOLVED_BY/EXPOSES.
            result = await session.run(
                "MATCH (a:Agent)-[r:PROVIDES]->(c:Capability) RETURN a.name AS impl, a.provider_type AS ptype, c.name AS cap"
            )
            provides = [(rec["impl"], rec["ptype"] or "", rec["cap"]) async for rec in result]
        for impl, ptype, cap in provides:
            await self.register_capability_provider(impl, cap, ptype)
        counts["provides_migrated"] = len(provides)
        async with self._driver.session() as session:
            r = await session.run(
                "MATCH (a:Agent)-[r:PROVIDES]->() DELETE r WITH DISTINCT a "
                "WHERE NOT (a)<-[:HAS_AGENT]-() AND NOT (a)-[:HAS_CAPABILITY]->() AND NOT (a)-[:DEPENDS_ON]-() "
                "DETACH DELETE a RETURN count(a) AS n"
            )
            rec = await r.single()
            counts["provider_agents_removed"] = int(rec["n"] or 0) if rec else 0

            # MeshRun instances -> ExecutionGraph workflows, rebuilt from the
            # run-log plan steps each STEP edge points at.
            result = await session.run("""
                MATCH (m:MeshRun)
                OPTIONAL MATCH (m)-[:STEP]->(s:_PlanStep)
                RETURN m.run_id AS run_id, m.goal AS goal, collect(DISTINCT s.agent) AS agents
                """)
            meshruns = [(rec["run_id"], rec["goal"] or "", [a for a in rec["agents"] if a]) async for rec in result]
        for run_id, goal, agents in meshruns:
            edges: list[tuple[str, str]] = []
            if self.is_connected():
                async with self._driver.session() as session:
                    result = await session.run(
                        """
                        MATCH (a:_PlanStep {run_id: $run_id})-[:DEPENDS_ON]->(b:_PlanStep {run_id: $run_id})
                        RETURN a.agent AS src, b.agent AS dst
                        """,
                        run_id=run_id,
                    )
                    edges = [(rec["src"], rec["dst"]) async for rec in result if rec["src"] and rec["dst"]]
            if agents:
                await self.record_execution_graph(run_id, goal, agents, edges)
        counts["meshruns_migrated"] = len(meshruns)
        async with self._driver.session() as session:
            await session.run("MATCH (m:MeshRun) DETACH DELETE m")
            await session.run("MATCH (:_PlanStep)-[r:RESOLVES]->() DELETE r")
            # Ontology mirror for pre-existing generalizations.
            r = await session.run("""
                MATCH (s:Capability)-[:GENERALIZES]->(g:Capability)
                MERGE (so:OntologyConcept {name: s.name})
                MERGE (go:OntologyConcept {name: g.name})
                MERGE (so)-[:IS_A]->(go)
                RETURN count(*) AS n
                """)
            rec = await r.single()
            counts["ontology_mirrored"] = int(rec["n"] or 0) if rec else 0
        logger.info("GraphStore: three-layer migration complete: %s", counts)
        return counts

    # ── Read ─────────────────────────────────────────────────────────────────

    async def get_graph(self) -> dict[str, Any]:
        """Read the full graph from Neo4j."""
        if not self.is_connected():
            return {"nodes": [], "edges": []}

        async with self._driver.session() as session:
            result = await session.run("MATCH (n:_PlanStep) OPTIONAL MATCH (n)-[r]->(m:_PlanStep) RETURN n, r, m")
            nodes = []
            edges = []
            seen_nodes: set[str] = set()
            async for record in result:
                n = record["n"]
                if n:
                    nid = n.get("id", n.get("node_id", str(n.element_id)))
                    if nid not in seen_nodes:
                        nodes.append(
                            {
                                "id": nid,
                                "type": self._label_to_type(list(n.labels)),
                                "label": n.get("display_name", n.get("label", n.get("name", ""))),
                            }
                        )
                        seen_nodes.add(nid)
                r = record["r"]
                m = record["m"]
                if r and m:
                    edges.append(
                        {
                            "from": r.start_node.get("id", r.start_node.get("node_id", "")),
                            "to": r.end_node.get("id", r.end_node.get("node_id", "")),
                            "type": r.type.lower(),
                        }
                    )
            return {"nodes": nodes, "edges": edges}

    async def get_graph_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        """Read back the execution graph planned for a specific run_id.

        Recovery path for /execute when RunStore (process-local, wiped on
        restart) has lost the run: sync_graph() tags every node/edge with
        run_id at write time, so the planner's graph can be reconstructed
        from Neo4j alone. Returns None if not connected or nothing matches —
        callers should treat that the same as "no plan for this run_id".
        """
        if not self.is_connected():
            return None

        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n:_PlanStep {run_id: $run_id}) "
                "OPTIONAL MATCH (n)-[r]->(m:_PlanStep {run_id: $run_id}) "
                "RETURN n, r, m",
                run_id=run_id,
            )
            nodes = []
            edges = []
            seen_nodes: set[str] = set()
            async for record in result:
                n = record["n"]
                if n:
                    nid = n.get("id", n.get("node_id", str(n.element_id)))
                    if nid not in seen_nodes:
                        node: dict[str, Any] = {
                            "id": nid,
                            "type": n.get("node_type", self._label_to_type(list(n.labels))),
                            "name": n.get("name", ""),
                            "label": n.get("display_name", n.get("label", "")),
                        }
                        if n.get("agent"):
                            node["agent"] = n.get("agent")
                        if n.get("state"):
                            node["state"] = n.get("state")
                        nodes.append(node)
                        seen_nodes.add(nid)
                r = record["r"]
                m = record["m"]
                if r and m:
                    edges.append(
                        {
                            "from": r.start_node.get("id", r.start_node.get("node_id", "")),
                            "to": r.end_node.get("id", r.end_node.get("node_id", "")),
                            "type": r.type.lower(),
                        }
                    )

        if not nodes:
            return None
        return {
            "nodes": nodes,
            "edges": edges,
            "metadata": {"run_id": run_id, "source": "neo4j_recovery"},
        }

    async def get_agent_dependencies(self) -> list[dict[str, str]]:
        """Read agent-to-agent dependencies."""
        if not self.is_connected():
            return []

        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (a:Agent:_PlanStep)-[r:DEPENDS_ON]->(b:Agent:_PlanStep) "
                "RETURN a.name AS src, b.name AS dst, r.reason AS reason"
            )
            return [{"from": record["src"], "to": record["dst"], "reason": record["reason"]} async for record in result]

    async def add_query_paths(self, paths: list) -> None:
        """Persist query-result graph paths returned by an execution.

        Merges nodes and relationships into Neo4j so discovered entities
        accumulate across executions rather than being discarded.
        """
        if not self.is_connected() or not paths:
            return

        async with self._driver.session() as session:
            for path in paths:
                nodes = self._get_attr(path, "nodes", [])
                rels = self._get_attr(path, "relationships", [])

                for node in nodes:
                    node_id = self._get_attr(node, "node_id", "")
                    labels = self._get_attr(node, "labels", [])
                    props = self._get_attr(node, "properties", {})
                    if not node_id or not labels:
                        continue
                    # Labels are interpolated into Cypher and cannot be
                    # parameterized — drop any that aren't safe identifiers
                    # rather than letting query-path data inject Cypher.
                    safe_labels = [s for s in (_safe_cypher_ident(str(l)) for l in labels if l) if s]
                    if len(safe_labels) != len([l for l in labels if l]):
                        logger.warning(
                            "add_query_paths dropped unsafe node label(s) on node %r",
                            node_id,
                        )
                    label_str = ":".join(safe_labels)
                    if label_str:
                        await session.run(
                            f"MERGE (n:{label_str} {{node_id: $node_id}}) SET n += $props",
                            node_id=node_id,
                            props=props,
                        )

                for rel in rels:
                    src = self._get_attr(rel, "source_node_id", "")
                    dst = self._get_attr(rel, "target_node_id", "")
                    # Relationship type is interpolated into Cypher — validate
                    # it, falling back to a fixed safe type on anything unsafe.
                    rel_type = _safe_cypher_ident(str(self._get_attr(rel, "rel_type", "RELATED"))) or "RELATED"
                    if src and dst:
                        await session.run(
                            f"MATCH (a {{node_id: $src}}), (b {{node_id: $dst}}) MERGE (a)-[:{rel_type}]->(b)",
                            src=src,
                            dst=dst,
                        )

        logger.debug("GraphStore stored %d query paths", len(paths))

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_attr(obj: Any, key: str, default: Any) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _node_label(node_type: str) -> str:
        return {
            "workload": "Workload",
            "step": "Step",
            "provider": "Provider",
            "agent": "Agent",
            "execution": "Agent",
            "capability": "Capability",
            "precondition": "Precondition",
            "effect": "Effect",
        }.get(node_type, "Unknown")

    @staticmethod
    def _node_props(node: dict) -> dict:
        node_id = str(node.get("id", node.get("label", node.get("name", ""))) or "")
        display_name = str(node.get("agent") or node.get("label") or node.get("name") or node_id)
        props = {
            "id": node_id,
            "node_id": node_id,
            "name": display_name,
            "display_name": display_name,
            "label": display_name,
        }
        if node.get("agent"):
            props["agent"] = node["agent"]
        if node.get("type"):
            props["node_type"] = node["type"]
            props["type"] = node["type"]
        if node.get("modality"):
            props["modality"] = node["modality"]
        if node.get("confidence") is not None:
            props["confidence"] = node["confidence"]
        if node.get("state"):
            props["state"] = node["state"]
        return props

    @staticmethod
    def _node_aliases(node: dict) -> set[str]:
        return {
            str(value)
            for value in (
                node.get("id"),
                node.get("name"),
                node.get("label"),
                node.get("agent"),
            )
            if value
        }

    @staticmethod
    def _edge_type(rel_type: str) -> str:
        known = {
            "provides": "PROVIDES",
            "requires": "REQUIRES",
            "produces": "PRODUCES",
            "enables": "ENABLES",
            "disables": "DISABLES",
            "depends_on": "DEPENDS_ON",
        }.get(rel_type)
        if known is not None:
            return known
        # Unknown types come from planner/LLM output and get interpolated into
        # MERGE (a)-[r:{rel}]->(b) — validate before trusting them as Cypher.
        # Anything unsafe collapses to a fixed, harmless relationship type.
        candidate = str(rel_type).upper()
        return candidate if _safe_cypher_ident(candidate) else "RELATED"

    @staticmethod
    def _label_to_type(labels: list[str]) -> str:
        for label in labels:
            if label != "_PlanStep":
                return label.lower()
        return "unknown"

    # ── Trust persistence ────────────────────────────────────────────────

    async def save_trust_edge(
        self,
        src: str,
        dst: str,
        relationship: str,
        permissions: list[str],
        trust_score: float,
        knowledge_scope: list[str] | None = None,
        policy_scope: list[str] | None = None,
        expiration: float = 0.0,
        reputation: float = 0.5,
        delegation_chain: list[str] | None = None,
    ) -> None:
        """Persist a trust edge to Neo4j."""
        if not self.is_connected():
            return
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (a:Runtime {id: $src})
                MERGE (b:Runtime {id: $dst})
                MERGE (a)-[r:TRUSTS]->(b)
                SET r.relationship = $relationship,
                    r.permissions = $permissions,
                    r.trust_score = $trust_score,
                    r.knowledge_scope = $knowledge_scope,
                    r.policy_scope = $policy_scope,
                    r.expiration = $expiration,
                    r.reputation = $reputation,
                    r.delegation_chain = $delegation_chain,
                    r.updated_at = timestamp()
            """,
                src=src,
                dst=dst,
                relationship=relationship,
                permissions=permissions,
                trust_score=trust_score,
                knowledge_scope=knowledge_scope or [],
                policy_scope=policy_scope or [],
                expiration=expiration,
                reputation=reputation,
                delegation_chain=delegation_chain or [],
            )

    async def revoke_trust_edge(self, src: str, dst: str, revoked_by: str = "") -> None:
        """Mark a trust edge as revoked in Neo4j."""
        if not self.is_connected():
            return
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (a:Runtime {id: $src})-[r:TRUSTS]->(b:Runtime {id: $dst})
                SET r.revoked = true,
                    r.revoked_at = timestamp(),
                    r.revoked_by = $revoked_by
            """,
                src=src,
                dst=dst,
                revoked_by=revoked_by,
            )

    async def load_trust_edges(self) -> list[dict[str, Any]]:
        """Load all non-revoked trust edges from Neo4j."""
        if not self.is_connected():
            return []
        async with self._driver.session() as session:
            result = await session.run("""
                MATCH (a:Runtime)-[r:TRUSTS]->(b:Runtime)
                WHERE r.revoked IS NULL OR r.revoked = false
                RETURN a.id as src, b.id as dst, r.relationship as relationship,
                       r.permissions as permissions, r.trust_score as trust_score,
                       r.knowledge_scope as knowledge_scope, r.policy_scope as policy_scope,
                       r.expiration as expiration, r.reputation as reputation,
                       r.delegation_chain as delegation_chain
            """)
            edges = []
            async for record in result:
                edges.append(dict(record))
            return edges


_booted_instance: Any = None  # GraphStore or CompositeGraphStore


def get_graph_store_instance() -> Any:
    """The Kernel-booted, already-connected singleton, for callers with no
    FastAPI Request to pull app.state._graph_store from — e.g. the
    middleware's capability-mesh composition, several frames below any
    request handler. None until init_graph_store() has actually run.

    Returns either GraphStore (Neo4j) or CompositeGraphStore (tensor + KV).
    """
    return _booted_instance
