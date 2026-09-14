"""mesh.py — AgentMesh: the self-organizing agent society.

Agents are ephemeral. Knowledge is permanent.

    OS process analogy:
        process     → MeshNode
        binary      → BrocaAgent (agent_type from registry)
        memory      → knowledge_pack (written back to pool on termination)
        exit()      → merge_and_terminate()

The mesh is generated, not hardcoded. Instead of:
    Planner → Executor → Worker  (fixed hierarchy)

You get:
    Problem → Capability Analysis → Mesh Synthesis → Execution → Dissolution

Replication is loss-driven. An agent is added to the mesh only when
simulation predicts it will reduce SimulationLoss by at least
the agent's min_loss_reduction threshold.

This file adds MESH-LEVEL concepts to broca.  It does NOT duplicate:
    BrocaAgent       — the existing step-handler protocol
    BrocaAgentRegistry — the existing ETASS step discovery registry
    BaseETASSAgent   — the existing pipeline step base class

MeshNode wraps an agent_type (from the existing registry) with mesh metadata.
AgentMesh tracks which agent_types are running and what they know.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

# ---------------------------------------------------------------------------
# MeshNodeState — lifecycle of a slot in the mesh
# ---------------------------------------------------------------------------


class MeshNodeState(Enum):
    IDLE = auto()  # spawned, awaiting task
    RUNNING = auto()  # executing
    MERGING = auto()  # writing knowledge back before exit
    TERMINATED = auto()  # done; knowledge merged


# ---------------------------------------------------------------------------
# MeshNode — a running slot (wraps agent_type from BrocaAgentRegistry)
# ---------------------------------------------------------------------------


@dataclass
class MeshNode:
    """A live slot in the mesh.

    agent_type: the type string used by BrocaAgentRegistry.discover()
    All behaviour comes from the BrocaAgent resolved at runtime.
    MeshNode only adds mesh-level metadata: lifecycle, knowledge, loss contribution.
    """

    agent_type: str
    node_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    state: MeshNodeState = MeshNodeState.IDLE
    spawned_by: str = ""  # trigger type ("load" | "knowledge" | "solver" | ...)
    spawned_for: str = ""  # capability gap or domain
    spawned_at: float = field(default_factory=time.monotonic)
    knowledge_pack: dict[str, Any] = field(default_factory=dict)
    simulation_loss: dict[str, float] = field(default_factory=dict)
    tasks_completed: int = 0
    ephemeral: bool = True  # if True → terminate after task completes

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.spawned_at

    @property
    def alive(self) -> bool:
        return self.state != MeshNodeState.TERMINATED

    def transition(self, new_state: MeshNodeState) -> "MeshNode":
        self.state = new_state
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "agent_type": self.agent_type,
            "state": self.state.name,
            "age_s": round(self.age_s, 2),
            "spawned_by": self.spawned_by,
            "spawned_for": self.spawned_for,
            "tasks_completed": self.tasks_completed,
            "simulation_loss": self.simulation_loss,
            "ephemeral": self.ephemeral,
        }


# ---------------------------------------------------------------------------
# ReplicationTrigger variants
# ---------------------------------------------------------------------------


@dataclass
class LoadTrigger:
    """Horizontal scaling — too much work for the current executor count."""

    type: str = "load"
    metric: str = "queue_length"
    threshold: float = 100.0
    spawn_agent_type: str = "executor"  # BrocaAgentRegistry agent_type to spawn

    def fires(self, metrics: dict[str, float]) -> bool:
        return metrics.get(self.metric, 0.0) > self.threshold


@dataclass
class KnowledgeTrigger:
    """Missing knowledge domain — spawn a specialist that loads the right packs."""

    type: str = "knowledge"
    missing_domain: str = ""
    spawn_agent_type: str = ""  # e.g. "fda_compliance", "robotics_planning"

    def fires(self, knowledge_domains: set[str]) -> bool:
        return self.missing_domain not in knowledge_domains


@dataclass
class SolverTrigger:
    """Missing solver capability — spawn an agent that owns it."""

    type: str = "solver"
    required_solver: str = ""  # e.g. "sat_smt", "monte_carlo"
    spawn_agent_type: str = ""

    def fires(self, active_solvers: set[str]) -> bool:
        return self.required_solver not in active_solvers


@dataclass
class ExplorationTrigger:
    """Simulation loss too high — spawn a temporary adversarial swarm."""

    type: str = "exploration"
    loss_threshold: float = 0.30
    adversarial_count: int = 20
    spawn_agent_type: str = "adversarial"
    ephemeral: bool = True  # always terminated after search

    def fires(self, simulation_loss: float) -> bool:
        return simulation_loss > self.loss_threshold


@dataclass
class SpecializationTrigger:
    """Planner identifies a capability gap — spawn a domain specialist."""

    type: str = "specialization"
    capability_gap: str = ""
    spawn_agent_type: str = ""

    def fires(self, available_capabilities: set[str]) -> bool:
        return self.capability_gap not in available_capabilities


ReplicationTrigger = LoadTrigger | KnowledgeTrigger | SolverTrigger | ExplorationTrigger | SpecializationTrigger


# ---------------------------------------------------------------------------
# AgentMesh — the current topology
# ---------------------------------------------------------------------------


class AgentMesh:
    """The running agent topology — generated, not hardcoded.

    The mesh holds:
        nodes        — active MeshNodes (by node_id)
        knowledge_pool — shared KnowledgePack dict; survives individual nodes
        capability_map — which agent_type owns each capability

    On termination a node merges its knowledge_pack into the pool.
    The process ends; the knowledge lives on.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, MeshNode] = {}  # node_id → MeshNode
        self._knowledge_pool: dict[str, Any] = {}  # shared persistent store
        self._capability_map: dict[str, str] = {}  # capability → node_id

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def spawn(
        self,
        agent_type: str,
        *,
        spawned_by: str = "",
        spawned_for: str = "",
        capabilities: list[str] | None = None,
        ephemeral: bool = True,
    ) -> MeshNode:
        """Add a new MeshNode to the mesh."""
        node = MeshNode(
            agent_type=agent_type,
            spawned_by=spawned_by,
            spawned_for=spawned_for,
            ephemeral=ephemeral,
        )
        self._nodes[node.node_id] = node
        for cap in capabilities or []:
            self._capability_map[cap] = node.node_id
        return node

    def terminate(self, node_id: str) -> dict[str, Any]:
        """Merge knowledge and remove node. Returns the merged knowledge pack."""
        node = self._nodes.get(node_id)
        if node is None:
            return {}
        node.transition(MeshNodeState.MERGING)
        self._merge_knowledge(node.knowledge_pack)
        node.transition(MeshNodeState.TERMINATED)
        self._capability_map = {cap: nid for cap, nid in self._capability_map.items() if nid != node_id}
        del self._nodes[node_id]
        return dict(node.knowledge_pack)

    def terminate_ephemeral(self) -> list[str]:
        """Terminate all ephemeral nodes. Returns list of terminated node_ids."""
        targets = [nid for nid, n in self._nodes.items() if n.ephemeral]
        for nid in targets:
            self.terminate(nid)
        return targets

    # ── Capability coverage ──────────────────────────────────────────────────

    def active_capabilities(self) -> set[str]:
        """Set of capability names currently covered by live nodes."""
        return set(self._capability_map.keys())

    def capability_gaps(self, required: set[str]) -> set[str]:
        """Capabilities in `required` not covered by any live node."""
        return required - self.active_capabilities()

    def active_agent_types(self) -> set[str]:
        return {n.agent_type for n in self._nodes.values()}

    def nodes_for_agent_type(self, agent_type: str) -> list[MeshNode]:
        return [n for n in self._nodes.values() if n.agent_type == agent_type]

    # ── Loss contribution ────────────────────────────────────────────────────

    def simulation_loss_total(self) -> float:
        """Max total loss across all live nodes (worst-case mesh loss)."""
        totals = [n.simulation_loss.get("total", 0.0) for n in self._nodes.values()]
        return max(totals) if totals else 0.0

    # ── Knowledge pool ────────────────────────────────────────────────────────

    def _merge_knowledge(self, pack: dict[str, Any]) -> None:
        for key, val in pack.items():
            if key not in self._knowledge_pool:
                self._knowledge_pool[key] = val
            elif isinstance(val, dict) and isinstance(self._knowledge_pool[key], dict):
                self._knowledge_pool[key].update(val)
            elif isinstance(val, list) and isinstance(self._knowledge_pool[key], list):
                seen = {str(v) for v in self._knowledge_pool[key]}
                self._knowledge_pool[key].extend(v for v in val if str(v) not in seen)

    @property
    def knowledge_pool(self) -> dict[str, Any]:
        return dict(self._knowledge_pool)

    # ── Serialisation ────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": len(self._nodes),
            "agent_types": sorted(self.active_agent_types()),
            "capabilities": sorted(self.active_capabilities()),
            "knowledge_pool_keys": sorted(self._knowledge_pool.keys()),
            "simulation_loss": round(self.simulation_loss_total(), 4),
        }


# ---------------------------------------------------------------------------
# MeshSynthesizer — problem → mesh topology
# ---------------------------------------------------------------------------


class MeshSynthesizer:
    """Generate an AgentMesh from a problem description.

    Instead of a fixed hierarchy, the synthesizer:
    1. Identifies which capabilities the problem requires
    2. Identifies what's currently in the mesh (gaps)
    3. Uses the BrocaAgentRegistry to find agents that cover each gap
    4. Spawns only the agents needed (greedy set cover)

    Loss-driven refinement: after synthesis, MeshOptimizer evaluates whether
    adding additional agents actually reduces SimulationLoss.
    """

    def __init__(self, registry=None) -> None:
        self._registry = registry

    def _get_registry(self):
        if self._registry is not None:
            return self._registry
        try:
            from broca.registry import get_registry

            return get_registry()
        except ImportError:
            return None

    def synthesize(
        self,
        required_capabilities: set[str],
        *,
        world_state: dict[str, Any] | None = None,
        existing_mesh: AgentMesh | None = None,
    ) -> AgentMesh:
        """Build (or extend) a mesh to cover required_capabilities.

        Returns a new or extended AgentMesh with the minimum agents needed.
        """
        mesh = existing_mesh or AgentMesh()
        if world_state:
            mesh._knowledge_pool.update(world_state.get("knowledge", {}))

        gaps = mesh.capability_gaps(required_capabilities)
        if not gaps:
            return mesh

        registry = self._get_registry()
        if registry is None:
            return mesh

        # Greedy set cover: pick the agent_type that covers the most gaps
        remaining = set(gaps)
        covered_by: dict[str, list[str]] = self._build_coverage_index(registry)

        while remaining:
            best_type = self._best_covering(remaining, covered_by)
            if best_type is None:
                break
            covers = set(covered_by.get(best_type, [])) & remaining
            mesh.spawn(
                best_type,
                spawned_by="synthesizer",
                spawned_for=", ".join(sorted(covers)),
                capabilities=list(covers),
            )
            remaining -= covers

        return mesh

    def _build_coverage_index(self, registry) -> dict[str, list[str]]:
        """agent_type → list[capability_names] it covers."""
        index: dict[str, list[str]] = {}
        try:
            for agent_type in registry.agent_types():
                agent = registry.discover(agent_type)
                if agent and hasattr(agent, "capabilities"):
                    index[agent_type] = list(agent.capabilities)
        except Exception:
            pass
        return index

    @staticmethod
    def _best_covering(remaining: set[str], covered_by: dict[str, list[str]]) -> str | None:
        best_type = None
        best_count = 0
        for agent_type, caps in covered_by.items():
            count = len(set(caps) & remaining)
            if count > best_count:
                best_count = count
                best_type = agent_type
        return best_type


# ---------------------------------------------------------------------------
# MeshOptimizer — loss-driven mesh optimization
# ---------------------------------------------------------------------------


class MeshOptimizer:
    """Optimize the agent mesh by minimizing SimulationLoss.

    Replication is justified only when it reduces L_sim by at least
    the candidate agent's min_loss_reduction.

    Algorithm (greedy hill climbing):
        1. Measure current mesh L_sim
        2. For each candidate agent_type not in mesh:
           a. Simulate adding it (update action model)
           b. Estimate new L_sim via _jepa_check
           c. If delta_loss > threshold → spawn
        3. Repeat until no further improvement or max_iterations

    The same SimulationLoss infrastructure that evaluates individual actions
    now evaluates mesh configurations.  Replication becomes planning.
    """

    def __init__(
        self,
        convergence_threshold: float = 0.10,
        max_iterations: int = 5,
        registry=None,
    ) -> None:
        self.convergence_threshold = convergence_threshold
        self.max_iterations = max_iterations
        self._registry = registry

    def _get_registry(self):
        if self._registry:
            return self._registry
        try:
            from broca.registry import get_registry

            return get_registry()
        except ImportError:
            return None

    def optimize(
        self,
        mesh: AgentMesh,
        world_state: dict[str, Any],
        *,
        prompt: str = "",
        graph=None,  # CapabilityGraph for action model
    ) -> dict[str, Any]:
        """Greedy mesh optimization against SimulationLoss.

        Returns:
            {
                "iterations": int,
                "agents_added": [agent_type, ...],
                "loss_initial": float,
                "loss_final": float,
                "converged": bool,
            }
        """

        initial_loss = self._estimate_loss(world_state, prompt, graph)
        current_loss = initial_loss
        agents_added: list[str] = []

        for _ in range(self.max_iterations):
            if current_loss < self.convergence_threshold:
                break
            best_agent, best_delta = self._find_best_addition(mesh, world_state, prompt, current_loss, graph)
            if best_agent is None or best_delta < 0.01:
                break
            registry = self._get_registry()
            agent_caps = []
            if registry:
                try:
                    agent_info = registry.get_capabilities_for(best_agent)
                    agent_caps = list(agent_info) if agent_info else []
                except Exception:
                    pass
            mesh.spawn(
                best_agent,
                spawned_by="optimizer",
                spawned_for=f"loss_reduction:{best_delta:.3f}",
                capabilities=agent_caps,
                ephemeral=True,
            )
            agents_added.append(best_agent)
            current_loss = max(0.0, current_loss - best_delta)

        return {
            "iterations": len(agents_added),
            "agents_added": agents_added,
            "loss_initial": round(initial_loss, 4),
            "loss_final": round(current_loss, 4),
            "converged": current_loss < self.convergence_threshold,
        }

    def _estimate_loss(self, world_state: dict, prompt: str, graph) -> float:
        try:
            from cortex.adversarial_agents import _jepa_check

            _, sim_loss = _jepa_check(world_state, prompt)
            sim_loss.compute_total()
            return sim_loss.total
        except Exception:
            return 0.5

    def _find_best_addition(
        self,
        mesh: AgentMesh,
        world_state: dict,
        prompt: str,
        current_loss: float,
        graph,
    ) -> tuple[str | None, float]:
        registry = self._get_registry()
        if registry is None:
            return None, 0.0

        active = mesh.active_agent_types()
        best_type: str | None = None
        best_delta = 0.0

        for agent_type in registry.agent_types():
            if agent_type in active:
                continue
            # Estimate loss with this agent added (inject its capabilities into action model)
            delta = self._estimate_delta(agent_type, world_state, prompt, graph)
            if delta > best_delta:
                best_delta = delta
                best_type = agent_type

        return best_type, best_delta

    def _estimate_delta(self, agent_type: str, world_state: dict, prompt: str, graph) -> float:
        """Estimate how much L_sim would drop if agent_type were added to the mesh."""
        # Use graph to enrich action model if available
        if graph is not None:
            try:
                action_model_contribution = graph.to_action_model()
                enriched_ws = {
                    **world_state,
                    "_candidate_agent": agent_type,
                    "_action_model_hint": action_model_contribution,
                }
            except Exception:
                enriched_ws = world_state
        else:
            enriched_ws = {**world_state, "_candidate_agent": agent_type}

        try:
            from cortex.adversarial_agents import _jepa_check

            _, candidate_loss = _jepa_check(enriched_ws, prompt)
            candidate_loss.compute_total()
            # Heuristic: each new agent reduces epistemic + goal loss by its min threshold
            # This is refined by actual simulation; for planning it's a lower bound
            return max(0.0, 0.5 - candidate_loss.total)
        except Exception:
            return 0.0
