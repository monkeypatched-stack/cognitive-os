"""ComparatorRuntime — Layer 4: Learning (loss computation).

Compares predicted vs observed outcomes and emits hierarchical losses.

Layer 4 Equations:
    L_world = L_topology + L_epistemic
    L_actor = L_world + L_policy

The Comparator is the single authority responsible for producing these losses.
Every runtime, learner, benchmark, dashboard, metric, and unit test uses these
definitions.

Key principle:
    Comparator MEASURES.
    Learner OPTIMIZES.
    These are different responsibilities.
"""

from __future__ import annotations

import json
import logging
import threading
import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.models.graph import canonical_graph_envelope
from src.monkey_brain.persistence.events import EventType, PersistenceEvent

logger = logging.getLogger("agentos.comparator_runtime")


class ComparatorOutcome(str, Enum):
    """Categorical classification of a comparison, layered on top of the
    numeric diff/loss model below -- nothing like this existed anywhere
    in the codebase before (confirmed by repo-wide search), so this is a
    new canonical type, not a duplicate of an existing one.

    Derived purely from _compare_node_outcomes()' per-node
    expected/actual success pairs -- see _classify_outcome()'s own
    docstring for the exact, documented precedence rules. Missing
    evidence never reads as SUCCESS (INCONCLUSIVE is the only outcome
    when actual state can't be established)."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    UNEXPECTED_SUCCESS = "unexpected_success"
    UNEXPECTED_FAILURE = "unexpected_failure"
    NO_CHANGE = "no_change"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ComparisonResult:
    graph_diff: dict[str, Any] = field(default_factory=dict)
    execution_order_diff: dict[str, Any] = field(default_factory=dict)
    state_diff: dict[str, Any] = field(default_factory=dict)
    operation_diff: dict[str, Any] = field(default_factory=dict)
    event_diff: dict[str, Any] = field(default_factory=dict)
    artifact_diff: dict[str, Any] = field(default_factory=dict)
    latency_diff: float = 0.0
    reward_diff: float = 0.0
    confidence_diff: float = 0.0
    comparison_score: float = 0.0
    # Hierarchical loss structure (explicit loss calculation contract)
    topology_loss: float = 0.0  # structural correctness of world representation
    epistemic_loss: float = 0.0  # correctness of knowledge about the world
    world_loss: float = 0.0  # topology_loss + epistemic_loss (computed)
    policy_loss: float = 0.0  # action quality (Bellman TD, reward prediction)
    actor_loss: float = 0.0  # world_loss + policy_loss (computed)
    # Comparator-hardening pass additions -- all additive, no existing
    # field's meaning or value changes.
    node_diffs: dict[str, Any] = field(default_factory=dict)
    """Per-node id -> {"expected_success": bool|None, "actual_success":
    bool|None, "match": bool}. None on either side means that node id
    wasn't present there (predicted but never executed, or executed but
    never predicted). _compare_graphs (above) only diffs node/edge ID
    MEMBERSHIP -- this is the per-step success/failure signal that was
    previously invisible to graph_diff/topology_loss, letting a multi-step
    plan's individual failing step collapse into an aggregate scalar."""
    outcome: str = "inconclusive"
    """One of ComparatorOutcome's values -- see _classify_outcome()."""
    execution_id: str = ""
    """Provenance: the tick's execution_id, when the caller supplied one
    on either graph envelope's graph_id. Empty (not fabricated) when
    neither side carried one."""
    timestamp: float = 0.0
    """Provenance: when this comparison's underlying observation was
    taken, from the graphs' own timestamp field when present. 0.0 (not
    fabricated) when neither side carried one."""

    def __post_init__(self) -> None:
        """Compute derived losses from independent components."""
        if self.world_loss == 0.0 and (self.topology_loss > 0 or self.epistemic_loss > 0):
            self.world_loss = round(min(1.0, self.topology_loss + self.epistemic_loss), 4)
        if self.actor_loss == 0.0 and (self.world_loss > 0 or self.policy_loss > 0):
            self.actor_loss = round(min(1.0, self.world_loss * 0.7 + self.policy_loss * 0.3), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_diff": self.graph_diff,
            "execution_order_diff": self.execution_order_diff,
            "state_diff": self.state_diff,
            "operation_diff": self.operation_diff,
            "event_diff": self.event_diff,
            "artifact_diff": self.artifact_diff,
            "latency_diff": self.latency_diff,
            "reward_diff": self.reward_diff,
            "confidence_diff": self.confidence_diff,
            "comparison_score": self.comparison_score,
            "topology_loss": self.topology_loss,
            "epistemic_loss": self.epistemic_loss,
            "world_loss": self.world_loss,
            "policy_loss": self.policy_loss,
            "actor_loss": self.actor_loss,
            "node_diffs": self.node_diffs,
            "outcome": self.outcome,
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
        }


class ComparatorRuntime:
    """Pure comparison engine.

    Inputs:
        simulation_graph — canonical predicted artifact
        execution_result  — observed execution artifact
    Output:
        ComparisonResult dict with diffs and scores
    """

    def __init__(self) -> None:
        self._event_bus: Any = None
        self.lemon: Any = None
        self.persistence: Any = None
        # Latest comparison, kept in-process so /learn can train on the real
        # measured epistemic loss (graph/order/state/confidence diffs from
        # an actual sim-vs-execution comparison) instead of its own
        # synthetic all-nodes-complete estimate. A persisted snapshot also
        # goes to Mongo via _persist_snapshot; this is just the hot handle.
        self._last_comparison_lock = threading.Lock()
        self.last_comparison: dict[str, Any] | None = None
        self.last_comparison_at: float = 0.0
        # Per-Actor CognitiveOS Isolation refactor: the bare fields above
        # are process-global -- fine for compare_history's own legitimate
        # "most recent comparison system-wide" admin view (the only real
        # external reader, api/routes/comparator_gateway.py), but actor-
        # facing code (CognitiveOS.comparator) must never read another
        # actor's result just because it happened to run more recently.
        # Scoped by execution_id (ComparisonResult.execution_id, already
        # derived from graph_id/run_id below) so a caller who knows its
        # OWN execution_id gets ONLY its own result. Capped to bound
        # memory -- this is a hot in-process cache, not a durable store
        # (Mongo persistence via _persist_snapshot is unaffected).
        self._comparisons: dict[str, dict[str, Any]] = {}
        self._comparisons_order: list[str] = []
        self._MAX_SCOPED_COMPARISONS = 2000

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    @classmethod
    async def boot(
        cls,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
    ) -> "ComparatorRuntime":
        """Attach Kernel-injected dependencies and register on app.state.

        No heavy subsystem wiring needed — compare's flow takes db/mongo_client
        per call, so there's nothing else to stand up at boot time.
        """
        rt = cls()
        rt.lemon = lemon
        rt.persistence = persistence
        rt._event_bus = event_bus
        app.state.comparator_runtime = rt
        if lemon is not None:
            lemon.info("ComparatorRuntime boot complete", component="bootstrap")
        logger.info("ComparatorRuntime boot complete")
        await rt._publish("comparator_runtime.booted", {})
        return rt

    async def shutdown(self, app: Any) -> None:
        """No owned subsystems to tear down today — see SimulationRuntime.
        shutdown()'s docstring for the rationale."""
        await self._publish("comparator_runtime.shutdown", {})
        logger.info("ComparatorRuntime shutdown complete")

    async def compare(self, simulation_graph: dict[str, Any], execution_result: dict[str, Any]) -> ComparisonResult:
        """Compare two completed artifacts without side effects."""
        simulation_graph = self._normalize_graph(simulation_graph)
        execution_result = self._normalize_graph(execution_result)
        if (
            simulation_graph.get("graph_id")
            and execution_result.get("graph_id")
            and simulation_graph["graph_id"] != execution_result["graph_id"]
        ):
            logger.warning(
                "ComparatorRuntime received mismatched graph_ids: %s != %s",
                simulation_graph["graph_id"],
                execution_result["graph_id"],
            )
        graph_diff = self._compare_graphs(simulation_graph, execution_result)
        execution_order_diff = self._compare_lists(
            simulation_graph.get("execution_order", []),
            execution_result.get("execution_order", []),
        )
        state_diff = self._compare_states(simulation_graph, execution_result)
        operation_diff = self._compare_lists(
            simulation_graph.get("metadata", {}).get("summary", {}).get("operations", []),
            execution_result.get("operations", []),
        )
        event_diff = self._compare_lists(
            simulation_graph.get("metadata", {}).get("summary", {}).get("events", []),
            execution_result.get("events", []),
        )
        artifact_diff = self._compare_lists(
            simulation_graph.get("metadata", {}).get("summary", {}).get("artifacts", []),
            execution_result.get("artifacts", []),
        )
        latency_diff = self._numeric_diff(
            float(simulation_graph.get("metadata", {}).get("summary", {}).get("latency_ms", 0.0)),
            float(execution_result.get("latency_ms", 0.0)),
        )
        reward_diff = self._numeric_diff(
            float(simulation_graph.get("metadata", {}).get("summary", {}).get("predicted_reward", 0.0)),
            float(execution_result.get("reward", 0.0)),
        )
        confidence_diff = self._numeric_diff(
            float(simulation_graph.get("metadata", {}).get("summary", {}).get("grounding_score", 0.0)),
            float(execution_result.get("confidence", 0.0)),
        )
        # The set/map diff `score`s are SIMILARITIES (1.0 = identical); the
        # numeric diffs are PENALTIES (0.0 = identical). Convert similarities
        # to penalties (1 - s) before weighting — the previous formula summed
        # similarities as if they were penalties, so total disagreement
        # (similarity 0.0 across the board) produced comparison_score 1.0 /
        # epistemic_loss 0.0: a perfectly wrong signal for the learner.
        comparison_score = round(
            max(
                0.0,
                1.0
                - (
                    ((1.0 - graph_diff["score"]) * 0.227)
                    + ((1.0 - execution_order_diff["score"]) * 0.136)
                    + ((1.0 - state_diff["score"]) * 0.182)
                    + ((1.0 - operation_diff["score"]) * 0.091)
                    + ((1.0 - event_diff["score"]) * 0.091)
                    + ((1.0 - artifact_diff["score"]) * 0.091)
                    + (latency_diff * 0.0)  # TODO: zero-weighted until sim predicts execution latency
                    + (reward_diff * 0.091)
                    + (confidence_diff * 0.091)
                ),
            ),
            4,
        )

        # ── Hierarchical Loss Structure (explicit loss calculation contract) ──
        #
        # topology_loss: structural correctness of world representation
        #   - missing/invalid transitions
        #   - incorrect graph topology
        #   - missing nodes/edges
        #   - incorrect reachability
        topology_loss = round(
            1.0 - ((graph_diff["score"] * 0.5) + (execution_order_diff["score"] * 0.5)),
            4,
        )

        # epistemic_loss: correctness of knowledge about the world
        #   - belief error (predicted vs observed state)
        #   - simulation error (predicted vs actual operations)
        #   - prediction error (predicted vs actual events)
        #   - confidence error (grounding vs reality)
        # state/operation/event `score`s are SIMILARITIES (1.0 = identical); confidence_diff
        # is a PENALTY (0.0 = identical). Convert the penalty to a similarity before summing —
        # adding it raw meant a PERFECT prediction scored epistemic_loss 0.2 (the confidence
        # term contributed 0 instead of its 0.2 weight), fabricating loss on flawless runs and
        # flooring world_loss at 0.2 (which then depressed /learn's reward). Weights sum to 1.0,
        # so a perfect match is now exactly 0.0.
        epistemic_loss = round(
            1.0
            - (
                (state_diff["score"] * 0.4)
                + (operation_diff["score"] * 0.2)
                + (event_diff["score"] * 0.2)
                + ((1.0 - confidence_diff) * 0.2)
            ),
            4,
        )

        # world_loss = topology_loss + epistemic_loss
        # The world model is responsible for representing reality correctly.
        world_loss = round(min(1.0, topology_loss + epistemic_loss), 4)

        # policy_loss: action quality (Bellman TD, reward prediction)
        #   - reward prediction error
        #   - advantage loss
        policy_loss = round(reward_diff, 4)

        # actor_loss = world_loss * 0.7 + policy_loss * 0.3
        # World loss is weighted MORE heavily than policy loss because:
        # - Bad world model → bad decisions, even with good policy
        # - Policy can only be as good as the world it operates on
        # - 70/30 split ensures world quality dominates
        actor_loss = round(min(1.0, world_loss * 0.7 + policy_loss * 0.3), 4)

        # Extract provenance information
        execution_id = (
            simulation_graph.get("graph_id")
            or execution_result.get("graph_id")
            or simulation_graph.get("metadata", {}).get("run_id")
            or execution_result.get("metadata", {}).get("run_id")
            or ""
        )
        timestamp = (
            simulation_graph.get("timestamp")
            or execution_result.get("timestamp")
            or simulation_graph.get("metadata", {}).get("timestamp")
            or execution_result.get("metadata", {}).get("timestamp")
            or 0.0
        )

        # Compute node-level diffs and outcome classification
        node_diffs = self._compare_node_outcomes(simulation_graph, execution_result)
        outcome = self._classify_outcome(node_diffs, simulation_graph, execution_result)

        result = ComparisonResult(
            graph_diff=graph_diff,
            execution_order_diff=execution_order_diff,
            state_diff=state_diff,
            operation_diff=operation_diff,
            event_diff=event_diff,
            artifact_diff=artifact_diff,
            latency_diff=latency_diff,
            reward_diff=reward_diff,
            confidence_diff=confidence_diff,
            comparison_score=comparison_score,
            topology_loss=topology_loss,
            epistemic_loss=epistemic_loss,
            world_loss=world_loss,
            policy_loss=policy_loss,
            actor_loss=actor_loss,
            node_diffs=node_diffs,
            outcome=outcome,
            execution_id=execution_id,
            timestamp=timestamp,
        )
        result_dict = result.to_dict()
        with self._last_comparison_lock:
            self.last_comparison = result_dict
            self.last_comparison_at = _time.time()
            if execution_id:
                if execution_id not in self._comparisons:
                    self._comparisons_order.append(execution_id)
                self._comparisons[execution_id] = {
                    "result": result_dict,
                    "at": self.last_comparison_at,
                }
                while len(self._comparisons_order) > self._MAX_SCOPED_COMPARISONS:
                    evict = self._comparisons_order.pop(0)
                    self._comparisons.pop(evict, None)
        await self._persist_snapshot(simulation_graph, execution_result, result)
        return result

    async def _persist_snapshot(
        self,
        simulation_graph: dict[str, Any],
        execution_result: dict[str, Any],
        result: ComparisonResult,
    ) -> None:
        if self.persistence is None:
            return
        try:
            run_id = str(
                simulation_graph.get("metadata", {}).get("run_id")
                or execution_result.get("metadata", {}).get("run_id")
                or simulation_graph.get("graph_id")
                or execution_result.get("graph_id")
                or "",
            )
            event = PersistenceEvent(
                event_type=EventType.METRIC_RECORDED,
                entity_type="comparison_snapshot",
                entity_id=run_id,
                data={
                    "run_id": run_id,
                    "graph_id": simulation_graph.get("graph_id", execution_result.get("graph_id", "")),
                    "simulation_graph": simulation_graph,
                    "execution_result": execution_result,
                    "comparison": result.to_dict(),
                    "source": "comparator_runtime",
                },
                source="comparator_runtime",
            )
            await self.persistence.persist(event)
        except Exception as exc:
            logger.warning("Failed to persist comparison snapshot: %s", exc)

    def _normalize_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        normalized = canonical_graph_envelope(graph)
        # canonical_graph_envelope deliberately returns only the graph shape
        # ("no extra metadata") — metadata must be carried over from the raw
        # input, not read back off the envelope. Reading it off the envelope
        # silently discarded the simulator's metadata.summary, which is where
        # every "expected" value this comparator diffs against lives — with
        # it gone, all expectations read as empty and the comparison was
        # vacuous.
        source = graph.to_dict() if hasattr(graph, "to_dict") else (graph if isinstance(graph, dict) else {})
        metadata = dict(source.get("metadata", {}))
        normalized["nodes"] = [self._normalize_node(node) for node in normalized.get("nodes", [])]
        normalized["edges"] = [self._normalize_edge(edge) for edge in normalized.get("edges", [])]
        execution_order = self._normalize_execution_order(
            normalized.get("execution_order", metadata.get("execution_order", []))
        )
        normalized["execution_order"] = execution_order
        metadata["execution_order"] = execution_order

        # The OBSERVED side is not a graph — it is an execution result that happens to
        # carry a graph inside it (operations, events, artifacts, latency_ms, confidence,
        # reward, plus nodes/edges/order). Returning the bare envelope threw every one of
        # those observation fields away, so compare() read them back as [] and 0.0 and
        # diffed the simulator's real predictions against nothing. A PERFECT prediction
        # scored 0.8 with epistemic_loss 0.2 — the executed agents came back as
        # `operations.missing`, and confidence_diff was pinned at 1.0 — and that invented
        # loss is exactly what /learn trains on. Keep the source's fields; let the
        # canonical graph shape win where the two overlap.
        merged = dict(source)
        merged.update(normalized)
        merged["metadata"] = metadata
        return merged

    def _compare_graphs(self, simulation_graph: dict[str, Any], execution_result: dict[str, Any]) -> dict[str, Any]:
        sim_nodes = self._node_ids(simulation_graph.get("nodes", []))
        exec_nodes = self._node_ids(execution_result.get("nodes", []))
        sim_edges = self._edge_ids(simulation_graph.get("edges", []))
        exec_edges = self._edge_ids(execution_result.get("edges", []))
        return {
            "missing_nodes": sorted(exec_nodes - sim_nodes),
            "extra_nodes": sorted(sim_nodes - exec_nodes),
            "missing_edges": sorted(exec_edges - sim_edges),
            "extra_edges": sorted(sim_edges - exec_edges),
            "score": self._set_similarity_score(sim_nodes, exec_nodes, sim_edges, exec_edges),
        }

    def _compare_states(self, simulation_graph: dict[str, Any], execution_result: dict[str, Any]) -> dict[str, Any]:
        expected = simulation_graph.get("metadata", {}).get("summary", {}).get("predicted_state", {})
        observed = execution_result.get("state", execution_result.get("observed_state", {}))
        return {
            "expected": expected,
            "observed": observed,
            "score": self._map_similarity_score(expected, observed),
        }

    def _compare_lists(self, expected: list[Any], observed: list[Any]) -> dict[str, Any]:
        expected_set = {self._stable_token(v) for v in expected}
        observed_set = {self._stable_token(v) for v in observed}
        return {
            "missing": sorted(expected_set - observed_set),
            "extra": sorted(observed_set - expected_set),
            "score": self._set_similarity_score(expected_set, observed_set),
        }

    def _numeric_diff(self, expected: float, observed: float) -> float:
        denom = max(abs(expected), abs(observed), 1.0)
        return round(min(abs(expected - observed) / denom, 1.0), 4)

    def _set_similarity_score(
        self,
        left: set[Any],
        right: set[Any],
        left_edges: set[Any] | None = None,
        right_edges: set[Any] | None = None,
    ) -> float:
        if left_edges is not None and right_edges is not None:
            left = set(left) | set(left_edges)
            right = set(right) | set(right_edges)
        universe = left | right
        if not universe:
            # Both sides empty is vacuous agreement (nothing predicted,
            # nothing observed), not zero similarity — returning 0.0 here
            # read as maximal disagreement once the aggregate converts
            # similarity to penalty.
            return 1.0
        return round(1.0 - (len(left ^ right) / max(len(universe), 1)), 4)

    def _map_similarity_score(self, expected: dict[str, Any], observed: dict[str, Any]) -> float:
        if not expected and not observed:
            return 1.0
        keys = set(expected.keys()) | set(observed.keys())
        if not keys:
            return 1.0
        matches = 0
        for key in keys:
            if self._stable_token(expected.get(key)) == self._stable_token(observed.get(key)):
                matches += 1
        return round(matches / len(keys), 4)

    def _node_ids(self, nodes: list[Any]) -> set[str]:
        ids = set()
        for node in nodes:
            node = self._normalize_node(node)
            if isinstance(node, dict):
                nid = node.get("id") or node.get("name") or node.get("label")
            else:
                nid = getattr(node, "id", None) or getattr(node, "name", None) or getattr(node, "label", None)
            if nid:
                ids.add(str(nid))
        return ids

    def _normalize_execution_order(self, order: Any) -> list[list[str]]:
        """Preserve nested list structure for execution_order."""
        if not isinstance(order, list):
            return [[str(order)] if order is not None else []]
        result: list[list[str]] = []
        for item in order:
            if isinstance(item, list):
                result.append([str(n) for n in item])
            elif isinstance(item, str):
                result.append([item])
            elif item is not None:
                result.append([str(item)])
        return result

    def _edge_ids(self, edges: list[Any]) -> set[tuple[str, str]]:
        ids: set[tuple[str, str]] = set()
        for edge in edges:
            edge = self._normalize_edge(edge)
            if isinstance(edge, dict):
                src = edge.get("from") or edge.get("src")
                dst = edge.get("to") or edge.get("dst")
            else:
                src = getattr(edge, "src", None) or getattr(edge, "from", None)
                dst = getattr(edge, "dst", None) or getattr(edge, "to", None)
            if src and dst:
                ids.add((str(src), str(dst)))
        return ids

    def _normalize_node(self, node: Any) -> dict[str, Any]:
        if isinstance(node, dict):
            normalized = dict(node)
        else:
            normalized = {
                "id": getattr(node, "id", ""),
                "type": getattr(node, "type", "step"),
                "label": getattr(node, "label", getattr(node, "id", "")),
            }
        if "name" in normalized and not normalized.get("label"):
            normalized["label"] = normalized["name"]
        if "name" in normalized and not normalized.get("id"):
            normalized["id"] = normalized["name"]
        normalized.setdefault("id", "")
        normalized.setdefault("label", normalized["id"])
        normalized.setdefault("type", "step")
        return normalized

    def _normalize_edge(self, edge: Any) -> dict[str, Any]:
        if isinstance(edge, dict):
            normalized = dict(edge)
        else:
            normalized = {
                "from": getattr(edge, "src", getattr(edge, "from", "")),
                "to": getattr(edge, "dst", getattr(edge, "to", "")),
                "type": getattr(edge, "rel", getattr(edge, "type", "depends_on")),
            }
        normalized["from"] = normalized.get("from") or normalized.get("src", "")
        normalized["to"] = normalized.get("to") or normalized.get("dst", "")
        normalized["type"] = normalized.get("type") or normalized.get("rel", "depends_on")
        if normalized["type"] == "dependency":
            normalized["type"] = "depends_on"
        return normalized

    def _stable_token(self, value: Any) -> str:
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True, default=str)
        if isinstance(value, list):
            return json.dumps(value, sort_keys=True, default=str)
        return str(value)

    def _compare_node_outcomes(
        self, simulation_graph: dict[str, Any], execution_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Compare per-node expected vs actual success.

        Returns a dict mapping node_id -> {
            "expected_success": bool|None,
            "actual_success": bool|None,
            "match": bool
        }

        None on either side means the node wasn't present in that graph
        (predicted but never executed, or executed but never predicted).

        A world-state cross-check (gating `actual_success` on the
        aggregate `state_diff` score) was attempted and reverted during
        the Learning-hardening follow-up pass: `_execution_to_graph`
        (kernel/pipeline/comparison/integration.py) hardcodes the
        observed "state" to `{success_count, failure_count}` -- it never
        carries real world facts comparable to `predicted_state`'s actual
        keys (e.g. "has_milk"). Gating on that comparison made
        `state_diff.score` read as near-total divergence for nearly EVERY
        real actor-tick comparison (confirmed live: it broke a genuine,
        correctly-matching success), not just the intended HTTP-200 case.
        A correct fix needs real per-node predicted/observed world deltas,
        which don't exist yet in this codebase -- documented as a
        remaining gap (see REMAINING LEARNING GAPS), not invented here
        with data that doesn't support it.
        """
        node_diffs: dict[str, Any] = {}

        sim_nodes = simulation_graph.get("nodes", [])
        exec_nodes = execution_result.get("nodes", [])

        # Build node success maps
        sim_node_map = {}
        for node in sim_nodes:
            node_id = node.get("id") or node.get("name") or node.get("label")
            if node_id:
                # Expected success is derived from the simulation's
                # predicted_success field, checked first -- the real
                # adapter (_prediction_to_graph, kernel/pipeline/
                # comparison/integration.py) only ever sets
                # "predicted_success" on a sim node, never bare "success".
                # Falls back to "success" for callers/tests that build a
                # sim graph with the more generic key -- same permissive,
                # multiple-synonym convention _node_ids/_normalize_node
                # already use elsewhere in this file (id/name/label).
                expected_success = node.get("predicted_success")
                if expected_success is None:
                    expected_success = node.get("success")
                if expected_success is None:
                    # If no explicit success field, infer from status
                    status = node.get("status", "").lower()
                    expected_success = status in ("completed", "success", "succeeded")
                sim_node_map[str(node_id)] = expected_success

        exec_node_map = {}
        for node in exec_nodes:
            node_id = node.get("id") or node.get("name") or node.get("label")
            if node_id:
                # Actual success is derived from execution result
                actual_success = node.get("success")
                if actual_success is None:
                    # If no explicit success field, infer from status
                    status = node.get("status", "").lower()
                    actual_success = status in ("completed", "success", "succeeded")
                exec_node_map[str(node_id)] = actual_success

        # Build comparison for all nodes present in either graph
        all_node_ids = set(sim_node_map.keys()) | set(exec_node_map.keys())

        for node_id in all_node_ids:
            expected_success = sim_node_map.get(node_id, None)
            actual_success = exec_node_map.get(node_id, None)

            # Match is True only when both sides have the same boolean value
            # If either side is None (node missing), match is False
            if expected_success is None or actual_success is None:
                match = False
            else:
                match = expected_success == actual_success

            node_diffs[node_id] = {
                "expected_success": expected_success,
                "actual_success": actual_success,
                "match": match,
            }

        return node_diffs

    def _classify_outcome(
        self,
        node_diffs: dict[str, Any],
        simulation_graph: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> str:
        """Classify the overall comparison outcome based on node-level diffs.

        Precedence rules (first match wins):
        1. NO_CHANGE: nothing was ever predicted AND nothing executed.
        2. INCONCLUSIVE: something was predicted, but there's no
           observation of what actually happened (actual state can't be
           established) -- or nodes exist but carry no usable
           success/failure signal at all.
        3. FAILURE: every node was expected to fail and did.
        4. UNEXPECTED_FAILURE: every node was expected to succeed but all
           failed.
        5. UNEXPECTED_SUCCESS: every node was expected to fail but all
           succeeded.
        6. PARTIAL_SUCCESS: a real mix of success and failure among the
           executed nodes.
        7. SUCCESS: every node succeeded AND matched what was predicted,
           same node identities on both sides (perfect_match) --
           otherwise (everything succeeded, but under different node
           identities than predicted, e.g. a different provider was
           actually used) UNEXPECTED_SUCCESS instead.

        Missing evidence never reads as SUCCESS.
        """
        if not execution_result or not execution_result.get("nodes"):
            if not simulation_graph or not simulation_graph.get("nodes"):
                # Nothing was ever predicted OR executed -- a genuinely
                # empty comparison, not missing evidence.
                return ComparatorOutcome.NO_CHANGE.value
            # Something was predicted, but there is no observation of what
            # actually happened -- actual state cannot be established.
            return ComparatorOutcome.INCONCLUSIVE.value

        if not node_diffs:
            return ComparatorOutcome.NO_CHANGE.value

        # Extract actual success values from node_diffs
        actual_successes = [diff.get("actual_success") for diff in node_diffs.values()]
        expected_successes = [diff.get("expected_success") for diff in node_diffs.values()]

        # Filter out None values (nodes that weren't present in one graph)
        actual_successes_filtered = [s for s in actual_successes if s is not None]
        expected_successes_filtered = [s for s in expected_successes if s is not None]

        if not actual_successes_filtered:
            # No actual execution evidence
            return ComparatorOutcome.INCONCLUSIVE.value

        if not expected_successes_filtered:
            # Something executed, but nothing was predicted to compare it
            # against -- we can't say whether it matched an expectation
            # (there wasn't one), which is INCONCLUSIVE, not "no change":
            # real execution genuinely happened.
            return ComparatorOutcome.INCONCLUSIVE.value

        # Whether every node's identity AND outcome lined up on both sides
        # -- a node present on only one side (predicted-but-never-ran, or
        # ran-but-unpredicted, e.g. "Provider A predicted, Provider B
        # actually used") is match=False even when everything that DID
        # run succeeded. Without this, a plan deviation that happens to
        # succeed reads as indistinguishable from a clean, fully-matched
        # SUCCESS -- this is what separates SUCCESS from UNEXPECTED_SUCCESS
        # below.
        perfect_match = all(diff.get("match") for diff in node_diffs.values())

        # Check for any actual failures
        any_actual_failure = any(s is False for s in actual_successes_filtered)
        all_actual_success = all(s is True for s in actual_successes_filtered)
        all_actual_failure = all(s is False for s in actual_successes_filtered)

        # Check for any expected failures
        all_expected_success = all(s is True for s in expected_successes_filtered)
        all_expected_failure = all(s is False for s in expected_successes_filtered)

        # Check for complete failure (all expected nodes failed and were expected to fail)
        if all_expected_failure and all_actual_failure:
            return ComparatorOutcome.FAILURE.value

        # Check for unexpected failure (expected success but got failure)
        if all_expected_success and all_actual_failure:
            return ComparatorOutcome.UNEXPECTED_FAILURE.value

        # Check for unexpected success (expected failure but got success)
        if all_expected_failure and all_actual_success:
            return ComparatorOutcome.UNEXPECTED_SUCCESS.value

        # Check for partial success (mixed outcomes - some succeeded, some failed)
        # This handles the case where there are multiple nodes with mixed outcomes
        if any_actual_failure and not all_actual_failure and len(actual_successes_filtered) > 1:
            return ComparatorOutcome.PARTIAL_SUCCESS.value

        # Check for complete success -- only a CLEAN success (every
        # predicted node ran, under the same id, with the same result) is
        # SUCCESS; everything actually succeeded but the node identities
        # diverged (e.g. Provider A predicted, Provider B actually used)
        # is a real plan deviation, reported as UNEXPECTED_SUCCESS instead
        # of silently reading identical to a clean match.
        if all_actual_success and all_expected_success:
            return ComparatorOutcome.SUCCESS.value if perfect_match else ComparatorOutcome.UNEXPECTED_SUCCESS.value

        # Default to inconclusive if we can't classify
        return ComparatorOutcome.INCONCLUSIVE.value

    def has_stored_run(self, run_id: str) -> bool:
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

        return get_run_store().has(run_id)

    def get_last_comparison(self, execution_id: str | None = None) -> dict[str, Any] | None:
        """Thread-safe accessor for a comparison result.

        execution_id=None (the default, unchanged behavior for the one
        real caller today -- api/routes/comparator_gateway.py's
        GET /compare/history, a legitimate operator-facing "most recent
        comparison system-wide" view): returns the single most recent
        comparison across every execution, same as before this method
        gained scoping.

        execution_id=<real id>: returns ONLY that execution's own
        comparison (or None if it has none yet) -- actor-facing callers
        (CognitiveOS.comparator) must always pass their own execution_id
        here, never rely on the unscoped default, so a concurrently-
        running actor's more-recent compare() call can never be read as
        if it were this actor's own result."""
        with self._last_comparison_lock:
            if execution_id is None:
                return self.last_comparison
            entry = self._comparisons.get(execution_id)
            return entry["result"] if entry is not None else None


def get_comparator_runtime() -> ComparatorRuntime:
    """Return a ComparatorRuntime instance.

    NOTE: The preferred way to get the runtime in API routes is via the
    FastAPI dependency in run_helpers.get_comparator_runtime(), which returns
    the booted singleton from app.state. This factory is a fallback for
    non-request contexts (e.g. tests, background tasks).
    """
    from src.monkey_brain.kernel.kernel import Kernel

    kernel = Kernel._instance
    try:
        runtime = kernel.runtime_selector.select("comparator") if kernel is not None else None
    except LookupError:
        runtime = None
    if runtime is None:
        raise RuntimeError("ComparatorRuntime is not booted; resolve it through Kernel boot")
    return runtime
