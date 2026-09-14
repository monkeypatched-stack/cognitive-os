"""Execution Graph — single canonical runtime metadata catalog."""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class NodeState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    # A node whose capability could not be resolved to any implementation.
    # Distinct from FAILED: nothing ran, so nothing errored. Written by the
    # dict-graph path (execute/runtime/workload.py); declared here so the
    # vocabulary is single-sourced and snapshot round-trips cannot raise.
    UNIMPLEMENTED = "unimplemented"


# Explicit valid-transition table for per-node state, mirroring
# kernel/process/models.py::VALID_TRANSITIONS (the process-level state
# machine layered above this one) -- that table is enforced by
# ProcessManager._transition(); this one was not enforced at all before,
# despite every real call site below already implying exactly one set of
# legal transitions. Derived from the actual call sites, not invented:
#   - GraphScheduler._execute_node: PENDING/READY -> RUNNING -> COMPLETE/FAILED
#     (graph_scheduler.py:121,147,149,160)
#   - GraphScheduler._maybe_expand's repair-chain completion: a FAILED node
#     transitions to COMPLETE when the repair node that supersedes it
#     succeeds (graph_scheduler.py:156) -- not a literal retry of the same
#     node, but a real, existing FAILED -> COMPLETE path
#   - ProcessManager.retry_node / Checkpoint.restore_process's crash-safety
#     reset: FAILED -> PENDING and RUNNING -> PENDING respectively
#     (manager.py:254, checkpoint.py:381)
#   - DAGRepairer (fix/repair/repair.py:56): an as-yet-unreached node ->
#     SKIPPED
# Terminal states (COMPLETE, SKIPPED, UNIMPLEMENTED) have no outgoing
# transitions -- once reached, only a fresh node/expand() can add more
# work, never a re-mark of the same node.
VALID_NODE_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.PENDING: frozenset({NodeState.READY, NodeState.RUNNING, NodeState.SKIPPED, NodeState.UNIMPLEMENTED}),
    NodeState.READY: frozenset({NodeState.RUNNING, NodeState.SKIPPED, NodeState.UNIMPLEMENTED}),
    NodeState.RUNNING: frozenset({NodeState.COMPLETE, NodeState.FAILED, NodeState.PENDING}),
    NodeState.FAILED: frozenset({NodeState.PENDING, NodeState.COMPLETE}),
    NodeState.COMPLETE: frozenset(),
    NodeState.SKIPPED: frozenset(),
    NodeState.UNIMPLEMENTED: frozenset(),
}


class InvalidNodeTransitionError(Exception):
    """Mirrors kernel/process/models.py::InvalidTransitionError, one layer
    down -- raised when a NodeState mutation isn't in VALID_NODE_TRANSITIONS
    for the node's current state."""

    def __init__(self, node_id: str, current: NodeState, target: NodeState) -> None:
        super().__init__(f"node={node_id!r}: invalid transition {current.value} -> {target.value}")
        self.node_id = node_id
        self.current = current
        self.target = target


@dataclass(frozen=True)
class GraphNode:
    id: str
    type: str
    label: str
    props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "type": self.type, "label": self.label}
        if self.props:
            d.update(self.props)
        return d


@dataclass(frozen=True)
class GraphEdge:
    src: str
    dst: str
    rel: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.src, "to": self.dst, "type": self.rel}


class ExecutionGraph:
    def __init__(self, id: str = "") -> None:
        self.id = id or f"graph-{uuid4().hex[:12]}"
        self.metadata: dict[str, Any] = {}
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._adj: dict[str, list[GraphEdge]] = {}
        self._radj: dict[str, list[GraphEdge]] = {}
        self._by_type: dict[str, list[GraphNode]] = {}
        self._state: dict[str, NodeState] = {}
        self._results: dict[str, Any] = {}
        self._iteration: int = 0

    def add_node(self, node: GraphNode) -> None:
        self._nodes[node.id] = node
        self._by_type.setdefault(node.type, []).append(node)
        if node.id not in self._state:
            self._state[node.id] = NodeState.PENDING

    def add_edge(self, edge: GraphEdge) -> None:
        self._edges.append(edge)
        self._adj.setdefault(edge.src, []).append(edge)
        self._radj.setdefault(edge.dst, []).append(edge)

    def expand(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        self._iteration += 1
        for n in nodes:
            self.add_node(n)
        for e in edges:
            self.add_edge(e)

    @property
    def iteration(self) -> int:
        return self._iteration

    def get_state(self, node_id: str) -> NodeState:
        return self._state.get(node_id, NodeState.PENDING)

    def set_state(self, node_id: str, state: NodeState) -> None:
        """The single chokepoint every NodeState mutation funnels through
        (mark_running/mark_complete/mark_failed all call this) -- enforces
        VALID_NODE_TRANSITIONS so an illegal transition (e.g. double
        completion, COMPLETED -> RUNNING, or two concurrent callers racing
        on the same node) raises instead of silently overwriting state."""
        current = self._state.get(node_id, NodeState.PENDING)
        if current == state:
            # Idempotent re-mark of the SAME state is a no-op, not an
            # error -- unlike a genuine transition to a different state,
            # this can never represent a race or a double-completion.
            return
        if state not in VALID_NODE_TRANSITIONS.get(current, frozenset()):
            raise InvalidNodeTransitionError(node_id, current, state)
        self._state[node_id] = state

    def set_result(self, node_id: str, result: Any) -> None:
        self._results[node_id] = result

    def get_result(self, node_id: str) -> Any:
        return self._results.get(node_id)

    def has_runnable_nodes(self) -> bool:
        return len(self.get_runnable_nodes()) > 0

    def get_runnable_nodes(self) -> list[GraphNode]:
        runnable: list[GraphNode] = []
        for node_id, state in self._state.items():
            if state not in (NodeState.PENDING, NodeState.READY):
                continue
            node = self._nodes.get(node_id)
            if node is None:
                continue
            if self._deps_satisfied(node_id):
                runnable.append(node)
        return runnable

    def _deps_satisfied(self, node_id: str) -> bool:
        for edge in self._radj.get(node_id, []):
            if edge.rel == "depends_on" and self._state.get(edge.src, NodeState.PENDING) != NodeState.COMPLETE:
                return False
        return True

    def mark_running(self, node_id: str) -> None:
        self.set_state(node_id, NodeState.RUNNING)

    def mark_complete(self, node_id: str, result: Any = None) -> None:
        self.set_state(node_id, NodeState.COMPLETE)
        if result is not None:
            self._results[node_id] = result

    def mark_failed(self, node_id: str, error: str = "") -> None:
        self.set_state(node_id, NodeState.FAILED)
        if error:
            self._results[node_id] = {"error": error}

    def get_step_nodes(self) -> list[GraphNode]:
        return list(self._by_type.get("step", []))

    def get_execution_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for s in self._state.values():
            counts[s.value] = counts.get(s.value, 0) + 1
        return {
            "iteration": self._iteration,
            "total_nodes": len(self._nodes),
            "states": counts,
            "runnable": len(self.get_runnable_nodes()),
        }

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    def nodes_by_type(self, node_type: str) -> list[GraphNode]:
        return list(self._by_type.get(node_type, []))

    def outgoing(self, node_id: str) -> list[GraphEdge]:
        return list(self._adj.get(node_id, []))

    def incoming(self, node_id: str) -> list[GraphEdge]:
        return list(self._radj.get(node_id, []))

    def get_agent_for_capability(self, capability_name: str) -> GraphNode | None:
        cid = f"capability:{capability_name}"
        for e in self._radj.get(cid, []):
            if e.rel == "provides":
                return self._nodes.get(e.src)
        return None

    def get_capabilities_for_agent(self, agent_type: str) -> list[GraphNode]:
        aid = f"agent:{agent_type}"
        return [self._nodes[e.dst] for e in self._adj.get(aid, []) if e.rel == "provides" and e.dst in self._nodes]

    def get_preconditions(self, capability_name: str) -> list[GraphNode]:
        cid = f"capability:{capability_name}"
        return [self._nodes[e.dst] for e in self._adj.get(cid, []) if e.rel == "requires" and e.dst in self._nodes]

    def get_effects(self, capability_name: str) -> list[GraphNode]:
        cid = f"capability:{capability_name}"
        return [self._nodes[e.dst] for e in self._adj.get(cid, []) if e.rel == "produces" and e.dst in self._nodes]

    def get_all_predicates(self) -> list[str]:
        return sorted({n.label for n in self._by_type.get("precondition", [])})

    def get_all_effects(self) -> list[str]:
        return sorted({n.label for n in self._by_type.get("effect", [])})

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def to_dict(self) -> dict[str, Any]:
        nodes_out = []
        for n in self._nodes.values():
            d = n.to_dict()
            d["state"] = self._state.get(n.id, NodeState.PENDING).value
            nodes_out.append(d)
        metadata = {
            "graph_id": self.id,
            "execution_order": [n.id for n in self._nodes.values()],
            **dict(self.metadata),
        }
        return {
            "graph_id": self.id,
            "solver": self.metadata.get("solver", ""),
            "nodes": nodes_out,
            "edges": [e.to_dict() for e in self._edges],
            "execution_order": [n.id for n in self._nodes.values()],
            "metadata": metadata,
        }

    def to_text(self) -> str:
        lines: list[str] = ["Execution Graph", "=" * 55]
        for ntype in [
            "workload",
            "step",
            "provider",
            "agent",
            "capability",
            "precondition",
            "effect",
        ]:
            nodes = self._by_type.get(ntype, [])
            if not nodes:
                continue
            lines.append("")
            lines.append(f"{ntype.upper()}S ({len(nodes)})")
            lines.append("-" * 30)
            for node in nodes:
                state = self._state.get(node.id, NodeState.PENDING)
                m = {
                    "pending": "○",
                    "ready": "●",
                    "running": "◎",
                    "complete": "✓",
                    "failed": "✗",
                    "skipped": "—",
                    "unimplemented": "⚠",
                }.get(state.value, "?")
                lines.append(f"  {m} {node.label}")
                for e in self._adj.get(node.id, []):
                    target = self._nodes.get(e.dst)
                    if target:
                        lines.append(f"    → [{e.rel}] {target.label}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        lines = ["graph LR"]
        for e in self._edges:
            a = e.src.replace(":", "_").replace(" ", "_")
            b = e.dst.replace(":", "_").replace(" ", "_")
            lines.append(f"    {a} -->|{e.rel}| {b}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        """Full internal-state dump for checkpointing — unlike to_dict(), this
        preserves enough to reconstruct an equivalent ExecutionGraph exactly
        (per-node state and results, not just a display-oriented projection).
        """
        return {
            "id": self.id,
            "nodes": [
                {"id": n.id, "type": n.type, "label": n.label, "props": dict(n.props)} for n in self._nodes.values()
            ],
            "edges": [{"src": e.src, "dst": e.dst, "rel": e.rel} for e in self._edges],
            "states": {node_id: state.value for node_id, state in self._state.items()},
            "results": dict(self._results),
            "iteration": self._iteration,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "ExecutionGraph":
        """Reconstruct an ExecutionGraph from snapshot() output — used by
        checkpoint/resume. Rebuilds nodes/edges via the normal add_node/
        add_edge path, then restores exact per-node state/results/iteration
        (add_node alone would leave every node PENDING).
        """
        graph = cls(id=snapshot.get("id", ""))
        for n in snapshot["nodes"]:
            graph.add_node(GraphNode(id=n["id"], type=n["type"], label=n["label"], props=dict(n["props"])))
        for e in snapshot["edges"]:
            graph.add_edge(GraphEdge(src=e["src"], dst=e["dst"], rel=e["rel"]))
        for node_id, state_value in snapshot["states"].items():
            graph._state[node_id] = NodeState(state_value)
        graph._results = dict(snapshot["results"])
        graph._iteration = int(snapshot["iteration"])
        return graph

    def _signing_payload(self) -> str:
        """Canonical payload for signing: graph id + sorted nodes + sorted edges."""
        nodes = sorted(
            [{"id": n.id, "type": n.type, "label": n.label} for n in self._nodes.values()],
            key=lambda n: n["id"],
        )
        edges = sorted(
            [{"src": e.src, "dst": e.dst, "rel": e.rel} for e in self._edges],
            key=lambda e: (e["src"], e["dst"]),
        )
        return json.dumps({"id": self.id, "nodes": nodes, "edges": edges}, sort_keys=True)

    def sign(self) -> str:
        """Sign the graph's topology using identity module."""
        payload = self._signing_payload().encode()
        try:
            from src.monkey_brain.kernel.identity import (
                get_identity,
                get_key_manager,
                sign_bytes,
            )

            identity = get_identity()
            km = get_key_manager()
            key = km.get_or_create(identity.runtime_id)
            sig = sign_bytes(payload, key)
            self.metadata["graph_signer"] = identity.runtime_id
        except Exception:
            import hashlib

            sig = hashlib.sha256(payload).hexdigest()
            self.metadata["graph_signer"] = "sha256-fallback"
        self.metadata["graph_signature"] = sig
        return sig

    def verify(self) -> bool:
        """Verify the graph's signature using identity module."""
        expected = self.metadata.get("graph_signature", "")
        if not expected:
            return False
        payload = self._signing_payload().encode()
        signer_id = self.metadata.get("graph_signer", "")
        if signer_id == "sha256-fallback":
            import hashlib

            return hashlib.sha256(payload).hexdigest() == expected
        try:
            from src.monkey_brain.kernel.identity import (
                get_identity,
                get_key_manager,
                verify_bytes,
            )

            identity = get_identity()
            km = get_key_manager()
            resolved_signer = signer_id or identity.runtime_id
            km.get_or_create(resolved_signer)
            pub_pem = km.get_public_key_pem(resolved_signer)
            return verify_bytes(payload, expected, pub_pem)
        except Exception:
            return False


def sign_graph_dict(graph: dict[str, Any]) -> str:
    """Sign a dict-format execution graph. Returns the HMAC-SHA256 signature."""
    nodes = sorted(
        [
            {
                "id": n.get("id", ""),
                "type": n.get("type", ""),
                "agent": n.get("agent", ""),
            }
            for n in graph.get("nodes", [])
        ],
        key=lambda n: n["id"],
    )
    edges = sorted(
        [
            {
                "from": e.get("from", ""),
                "to": e.get("to", ""),
                "type": e.get("type", ""),
            }
            for e in graph.get("edges", [])
        ],
        key=lambda e: (e["from"], e["to"]),
    )
    payload = json.dumps(
        {"id": graph.get("graph_id", ""), "nodes": nodes, "edges": edges},
        sort_keys=True,
    ).encode()

    # Sign with identity module (Ed25519) — no shared secrets
    signer_id = "unknown"
    try:
        from src.monkey_brain.kernel.identity import (
            get_identity,
            get_key_manager,
            sign_bytes,
        )

        identity = get_identity()
        km = get_key_manager()
        key = km.get_or_create(identity.runtime_id)
        sig = sign_bytes(payload, key)
        signer_id = identity.runtime_id
    except Exception:
        # Fallback: should not happen if identity module is properly initialized
        import hashlib

        sig = hashlib.sha256(payload).hexdigest()

    graph.setdefault("metadata", {})["graph_signature"] = sig
    graph.setdefault("metadata", {})["graph_signer"] = signer_id
    return sig


def verify_graph_dict(graph: dict[str, Any]) -> bool:
    """Verify a dict-format execution graph's signature using identity module."""
    expected = graph.get("metadata", {}).get("graph_signature", "")
    signer_id = graph.get("metadata", {}).get("graph_signer", "")
    if not expected:
        return False
    nodes = sorted(
        [
            {
                "id": n.get("id", ""),
                "type": n.get("type", ""),
                "agent": n.get("agent", ""),
            }
            for n in graph.get("nodes", [])
        ],
        key=lambda n: n["id"],
    )
    edges = sorted(
        [
            {
                "from": e.get("from", ""),
                "to": e.get("to", ""),
                "type": e.get("type", ""),
            }
            for e in graph.get("edges", [])
        ],
        key=lambda e: (e["from"], e["to"]),
    )
    payload = json.dumps(
        {"id": graph.get("graph_id", ""), "nodes": nodes, "edges": edges},
        sort_keys=True,
    ).encode()

    # Verify with identity module (Ed25519) — no shared secrets
    try:
        from src.monkey_brain.kernel.identity import get_key_manager, verify_bytes

        km = get_key_manager()
        km.get_or_create(signer_id)
        pub_pem = km.get_public_key_pem(signer_id)
        return verify_bytes(payload, expected, pub_pem)
    except Exception:
        return False
