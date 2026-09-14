"""GraphManager — owns the Execution Graph inside CognitiveRuntime.

Each runtime owns its own graph:
    CognitiveRuntime  → Execution Graph (what will be executed)
    SimulationRuntime → Simulation Graph (predicted outcomes)
    ComparatorRuntime → Diff + Loss (predicted vs actual)

The Execution Graph is the persistent cognitive state.
The Q-table is the persistent execution policy.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos.graph_manager")


class QTable:
    """Bellman Q-table — PolicyStore is the source of truth.

    Phase 2: PolicyStore is authoritative. This class adds:
    - LR decay over time
    - Tensor cell mirroring for backward compat
    - Delegates all Q-storage to PolicyStore

    Learning rate decays over time: lr = lr_initial * (1 / (1 + decay_step / decay_rate))
    """

    def __init__(self, tensor: Any, lr_initial: float = 0.1, lr_decay_rate: float = 1000.0) -> None:
        self._tensor = tensor
        self._lr_initial = lr_initial
        self._lr_decay_rate = lr_decay_rate
        self._update_count: int = 0
        self._discount: float = 0.95
        # Phase 2: PolicyStore is authoritative
        from src.monkey_brain.kernel.policy.store import PolicyStore

        self._policy_store = PolicyStore(lr=lr_initial, discount=0.95)

    @property
    def _lr(self) -> float:
        """Current learning rate with decay."""
        return self._lr_initial / (1.0 + self._update_count / self._lr_decay_rate)

    def _read_q(self, agent: str) -> float:
        """Read Q from PolicyStore (authoritative)."""
        return self._policy_store.value(agent, "__self__")

    def _write_q(self, agent: str, q: float) -> None:
        """Write Q to PolicyStore (authoritative) and mirror to tensor."""
        with self._policy_store._lock:
            self._policy_store._q[(agent, "__self__")] = q
        # Mirror to tensor for backward compat with code reading cell.q
        i = self._tensor.intern(agent)
        cell = self._tensor._cells.get((i, i))
        if cell is None:
            cell = self._tensor._new_cell(i, i)
        cell.q = q

    def _self_loop_q(self, agent: str) -> float:
        return self._read_q(agent)

    def _write_self_loop(self, agent: str, q: float) -> None:
        self._write_q(agent, q)

    def _max_out_q(self, agent: str) -> float:
        """max Q of outgoing transitions (for TD target)."""
        successors = self._tensor.successors(agent)
        if not successors:
            return 0.5
        return max(self._policy_store.value(agent, dst) for dst in successors if dst != agent)

    def get(self, agent: str, default: float = 0.5) -> float:
        q = self._read_q(agent)
        return q if q != 0.5 else default

    def update(self, agent: str, reward: float) -> None:
        old = self._read_q(agent)
        new_q = old + self._lr * (reward - old)
        self._write_q(agent, new_q)
        self._update_count += 1

    def _td_target(self, reward: float, next_agent: str) -> float:
        if not next_agent:
            return reward
        return reward + self._discount * self._read_q(next_agent)

    def update_with_replay(self, agent: str, reward: float, next_agent: str = "") -> None:
        old = self._read_q(agent)
        td_target = self._td_target(reward, next_agent)
        td_error = td_target - old
        new_q = old + self._lr * td_error
        self._write_q(agent, new_q)
        self._update_count += 1

        # Record in PolicyStore's replay buffer (no second Bellman update)
        self._policy_store.append_replay(
            state=agent,
            action="__self__",
            reward=reward,
            next_state=next_agent,
            old_q=old,
            new_q=new_q,
            td_error=td_error,
        )

    def replay_sample(self, batch_size: int = 32) -> list[dict]:
        """Sample from PolicyStore's replay buffer."""
        return self._policy_store.replay_sample(batch_size)

    @property
    def replay_size(self) -> int:
        """Current replay buffer size from PolicyStore."""
        return self._policy_store.replay_size

    def replay_update(self, batch_size: int = 32) -> dict:
        """Delegate replay update to PolicyStore."""
        return self._policy_store.replay_update(batch_size)

    def apply_delta(self, delta: dict[str, float]) -> None:
        for agent, reward in delta.items():
            self.update(agent, reward)

    def snapshot(self) -> dict[str, float]:
        """Snapshot from PolicyStore (authoritative)."""
        return self._policy_store.snapshot()

    def restore(self, data: dict[str, float]) -> None:
        """Restore to PolicyStore (authoritative) and mirror to tensor."""
        self._policy_store.restore(data)
        for key, q in data.items():
            parts = key.split("|", 1)
            if len(parts) == 2:
                agent = parts[0]
                i = self._tensor.intern(agent)
                cell = self._tensor._cells.get((i, i))
                if cell is None:
                    cell = self._tensor._new_cell(i, i)
                cell.q = q

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2))

    def load(self, path: Path) -> None:
        if path.exists():
            self.restore(json.loads(path.read_text()))

    @property
    def policy_store(self) -> Any:
        """Access the PolicyStore directly."""
        return self._policy_store

    def verify_consistency(self, tolerance: float = 1e-6) -> dict:
        """Verify PolicyStore matches tensor self-loops."""
        tensor_snapshot = {}
        for (i, j), cell in self._tensor._cells.items():
            if i == j:
                agent = self._tensor._state_name[i]
                if agent:
                    tensor_snapshot[agent] = cell.q

        policy_snapshot = self._policy_store.snapshot()
        mismatches = []
        for agent, tensor_q in tensor_snapshot.items():
            policy_key = f"{agent}|__self__"
            policy_q = policy_snapshot.get(policy_key, 0.5)
            if abs(tensor_q - policy_q) > tolerance:
                mismatches.append({"agent": agent, "tensor_q": tensor_q, "policy_q": policy_q})

        return {
            "consistent": len(mismatches) == 0,
            "mismatches": mismatches,
            "tensor_size": len(tensor_snapshot),
            "policy_size": self._policy_store.size,
        }


class GraphManager:
    """Manages the persistent Execution Graph and Q-table.

    The graph is the ONLY persistent cognitive state.
    Everything else (agents, providers, observations) is transient.

    The graph is an ACTIVE SCHEDULER — it answers "what should execute next?"
    via the Q-table policy.

    Q-values live in the SparseTransitionTensor (the unified world model).
    """

    def __init__(self, tensor: Any = None, tenant_id: str = "default") -> None:
        # The tenant's WRITABLE tensor, not get_world()'s read-only TenantView: QTable
        # writes Q-values through intern()/_cells, which a TenantView does not have. Every
        # default-constructed GraphManager (here, CognitiveRuntime, /learn) raised
        # AttributeError on its first Q-table touch.
        from src.monkey_brain.kernel.compile.world_tensor import get_world_tensor

        self.graph: dict = {}
        self._tenant_id = tenant_id
        self._tensor = tensor or get_world_tensor(tenant_id)
        self.q_table = QTable(self._tensor)
        self.annotations: dict[str, dict] = {}
        self._epoch_count: int = 0
        self._last_loss: float = 1.0
        self._compiled: Any = None  # CompiledOperator from last compile_graph()
        # One compiler for this manager's lifetime. compile_graph() used to build a
        # fresh GraphCompiler on every call, so its operator cache was thrown away
        # immediately and EVERY compile was a cache miss — the cache never once served
        # a hit. Safe to hold now that the cache is LRU-bounded, and the cache key
        # covers strengths, so a changed Q-value still forces a recompile.
        self._compiler: Any = None

    def compile_graph(self) -> Any:
        """Compile the world tensor's learned transitions into a CompiledOperator.

        The tensor IS the state transition model. This extracts its cells
        (src→dst with Q-values) into a SemanticGraph, then compiles to
        row-stochastic CSR (M = D⁻¹W) for planning.
        """
        from src.monkey_brain.kernel.compile.types import SemanticGraphSnapshot
        from src.monkey_brain.kernel.compile.compiler import GraphCompiler

        nodes = []
        seen_states = set()
        for (i, j), cell in self._tensor._cells.items():
            for idx in (i, j):
                name = self._tensor._state_name[idx]
                if name and name not in seen_states:
                    nodes.append({"id": name, "name": name, "agent": name})
                    seen_states.add(name)

        edges = []
        strengths = {}
        for (i, j), cell in self._tensor._cells.items():
            if i == j:
                continue
            src = self._tensor._state_name[i]
            dst = self._tensor._state_name[j]
            if src and dst:
                edges.append({"from": src, "to": dst, "type": "depends_on"})
                strengths[(src, dst)] = cell.q

        sg = SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths=strengths)
        if self._compiler is None:
            self._compiler = GraphCompiler()
        self._compiled = self._compiler.compile(sg)
        return self._compiled

    # ── PLAN ──────────────────────────────────────────────────────────────

    def apply_plan(self, graph_delta: dict) -> None:
        """Merge a plan's graph_delta into the current graph.

        graph_delta contains: nodes to add, edges to add, nodes to remove.
        The graph accumulates changes across cycles.
        """
        new_nodes = graph_delta.get("nodes", [])
        new_edges = graph_delta.get("edges", [])
        remove_nodes = graph_delta.get("remove_nodes", [])

        # A node already in the graph used to be SKIPPED outright, so a re-plan that
        # reassigned the same node id to a different agent was silently discarded — the
        # graph kept the stale agent forever. Merge the new fields onto the existing node
        # instead; the graph still accumulates, it just stops ignoring updates.
        by_id = {n["id"]: n for n in self.graph.get("nodes", [])}
        for node in new_nodes:
            existing = by_id.get(node["id"])
            if existing is None:
                self.graph.setdefault("nodes", []).append(node)
                by_id[node["id"]] = node
            else:
                existing.update(node)

        # Dedupe on (from, to, TYPE). Keying on (from, to) alone treated a control/
        # feedback edge as a duplicate of the execution edge between the same pair and
        # dropped it — the two mean opposite things (one imposes execution order, the
        # other is explicitly excluded from it).
        existing_edges = {(e["from"], e["to"], e.get("type", "depends_on")) for e in self.graph.get("edges", [])}
        for edge in new_edges:
            key = (edge["from"], edge["to"], edge.get("type", "depends_on"))
            if key not in existing_edges:
                self.graph.setdefault("edges", []).append(edge)
                existing_edges.add(key)

        if remove_nodes:
            remove_set = set(remove_nodes)
            self.graph["nodes"] = [n for n in self.graph.get("nodes", []) if n["id"] not in remove_set]
            self.graph["edges"] = [
                e for e in self.graph.get("edges", []) if e["from"] not in remove_set and e["to"] not in remove_set
            ]

        self._epoch_count += 1

    # ── ACT (graph scheduling) ────────────────────────────────────────────

    def next_path(self, start: str, goal: str, policy: QTable | None = None) -> list[dict]:
        """Ask the graph: what path from start to goal should execute next?

        Uses the compiled operator's adjacency when available (O(V+E)),
        falls back to edge-list scanning.
        """
        policy = policy or self.q_table
        nodes = {n["id"]: n for n in self.graph.get("nodes", [])}
        edges = self.graph.get("edges", [])

        # "start" and "goal" are SENTINELS, not node ids. The planner emits agent nodes
        # (n1, n2, …) and never a node called "start" or "goal" — and the only caller
        # (CognitiveRuntime._act) passes those two literals — so this is the branch that
        # actually runs in production, every time. The greedy policy walk below is
        # therefore unreachable from the live path (see the note on it).
        #
        # This used to `return list(nodes.values())`: every node, in declaration order,
        # ignoring the DAG. _act re-derives its own ready-set from the edges so nothing
        # mis-executed, but the "path" this method promises was really an unordered bag.
        # Return the whole graph in dependency order instead. It must stay the whole
        # graph: handing back a single greedy chain would silently drop every planner
        # node that isn't on it.
        if start not in nodes or goal not in nodes:
            return [nodes[nid] for nid in self._topological_order() if nid in nodes]

        # Policy-guided greedy walk, for callers that pass REAL node ids. It works (given
        # n1→{n2,n3}→n4 and a Q-table that rates n2's agent above n3's, it picks n2) —
        # but nothing in the system calls it that way today, so the Q-table currently has
        # no influence on which nodes execute. Enabling it on the live path would mean
        # executing one chain and skipping every other planner node, which is a product
        # decision, not a bug fix. Left reachable for callers that want a single path.
        # TODO: decide whether execution should follow a policy-chosen path or the whole
        # planner DAG; today it is the whole DAG and the policy only scores agents.
        succ_map: dict[str, list[str]] = {}
        if self._compiled is not None:
            idx_of = self._compiled.index_of
            node_of = self._compiled.node_of
            indptr = self._compiled.indptr
            indices = self._compiled.indices
            for nid in nodes:
                i = idx_of.get(nid)
                if i is not None:
                    succ_map[nid] = [node_of[j] for j in indices[indptr[i] : indptr[i + 1]] if node_of[j] in nodes]
        if not succ_map:
            for e in edges:
                if e.get("type") != "feedback":
                    succ_map.setdefault(e["from"], []).append(e["to"])

        path = []
        visited = set()
        current = start

        for _ in range(len(nodes) + 1):
            if current == goal:
                path.append(nodes[current])
                break
            if current in visited:
                break
            visited.add(current)

            successors = succ_map.get(current, [])
            if not successors:
                break

            best = max(
                successors,
                key=lambda s: policy.get(nodes.get(s, {}).get("agent", ""), 0.5),
            )
            if current in nodes:
                path.append(nodes[current])
            current = best

        if not path and start in nodes:
            path = [nodes[nid] for nid in self._topological_order()]

        return path

    def _topological_order(self) -> list[str]:
        """Every node id, dependencies first. Feedback edges are control flow, not
        execution flow, and are excluded (same convention as the graph compiler).

        On a cycle this used to `break` and return a PARTIAL order — 1 of 3 nodes for a
        3-node cyclic graph — with no error. next_path() feeds that straight to the
        executor, so the nodes trapped in the cycle would simply never run and nothing
        would say so. Never lose a node: order what can be ordered, append the rest in
        declaration order, and log loudly.
        """
        nodes = self.graph.get("nodes", [])
        edges = self.graph.get("edges", [])
        declared = [n["id"] for n in nodes]
        node_ids = set(declared)
        real_edges = [
            e for e in edges if e.get("type") != "feedback" and e.get("from") in node_ids and e.get("to") in node_ids
        ]
        in_degree = {nid: 0 for nid in declared}
        for e in real_edges:
            in_degree[e["to"]] += 1

        order: list[str] = []
        remaining = set(declared)
        while remaining:
            ready = [nid for nid in declared if nid in remaining and in_degree.get(nid, 0) == 0]
            if not ready:
                break
            order.extend(ready)
            for nid in ready:
                remaining.discard(nid)
                for e in real_edges:
                    if e["from"] == nid:
                        in_degree[e["to"]] -= 1

        if remaining:
            stranded = [nid for nid in declared if nid in remaining]
            logger.error(
                "[graph_manager] execution graph has a dependency cycle — %d of %d nodes "
                "could not be ordered (%s); appending them in declaration order so they "
                "are not silently dropped",
                len(stranded),
                len(declared),
                ", ".join(stranded),
            )
            order.extend(stranded)
        return order

    # ── OBSERVE ───────────────────────────────────────────────────────────

    def annotate(self, observation: dict) -> dict:
        """Annotate a node with runtime observation data.

        observation: {node_id, status, reward, latency_ms, ...}
        Returns the annotated observation.
        """
        node_id = observation.get("node_id", "")
        self.annotations[node_id] = {
            **observation,
            "observed_at": time.time(),
        }
        return observation

    # ── LEARN ─────────────────────────────────────────────────────────────

    def apply_learning(self, policy_delta: dict[str, float], simulation_graph: dict | None = None) -> dict:
        """Apply learning with experience replay.

        Perplexity = raw_loss / (1 + avg_q_value)

        As Q-values increase (system learns), perplexity decreases.
        When Q-values converge, perplexity approaches 0 — system has learned.

        Returns a graph_delta that the Planner should apply in the next cycle,
        rather than directly mutating topology.
        """
        if simulation_graph is not None:
            sim_graph_id = simulation_graph.get("graph_id", "")
            exec_graph_id = self.graph.get("graph_id", "")
            if sim_graph_id and exec_graph_id and sim_graph_id != exec_graph_id:
                logger.warning(
                    "GraphManager.apply_learning graph_id mismatch: execution=%s simulation=%s",
                    exec_graph_id,
                    sim_graph_id,
                )
        for agent, reward in policy_delta.items():
            self.q_table.update_with_replay(agent, reward)

        replay_result = self.q_table.replay_update(batch_size=16)

        rewards = list(policy_delta.values())
        avg_reward = sum(rewards) / max(len(rewards), 1)
        epistemic_loss = 1.0 - avg_reward

        topo_loss = self._topological_loss(simulation_graph)

        q_values = list(self.q_table.snapshot().values())
        avg_q = sum(q_values) / max(len(q_values), 1) if q_values else 0.5

        raw_loss = topo_loss + epistemic_loss
        self._last_loss = raw_loss / (1 + avg_q)

        synced = False
        sync_threshold = 0.15
        graph_delta: dict[str, Any] = {"nodes": [], "edges": [], "remove_nodes": []}
        if topo_loss < sync_threshold and simulation_graph is not None:
            graph_delta = self._compute_sync_delta(simulation_graph)
            synced = True

        return {
            "avg_reward": round(avg_reward, 3),
            "epistemic_loss": round(epistemic_loss, 3),
            "topological_loss": round(topo_loss, 3),
            "q_value": round(avg_q, 4),
            "perplexity": round(self._last_loss, 3),
            "nodes_learned": len(policy_delta),
            "q_table_size": len(self.q_table.snapshot()),
            "synced": synced,
            "replay_updated": replay_result["updated"],
            "avg_td_error": round(replay_result["avg_td_error"], 4),
            "graph_delta": graph_delta,
        }

    def _topological_loss(self, simulation_graph: dict | None) -> float:
        """Compute structural diff between simulation graph and execution graph.

        Measures: missing nodes, extra nodes, different edges.
        Returns 0.0 (identical structure) to 1.0 (completely different).

        A MISSING simulation graph is not a match — it is an un-runnable comparison.
        Returning 0.0 there ("identical structure") fabricated a perfect prediction:
        it lowered raw_loss/perplexity and let a run converge, report success, and even
        mark itself `synced` on a comparison that never happened (this fires whenever
        _simulate degrades to {} — its except-path). The honest signal is maximal
        structural uncertainty (1.0): we predicted nothing, so our structural belief is
        entirely unvalidated and the run must NOT be allowed to claim convergence on it.
        """
        if not simulation_graph or not simulation_graph.get("nodes"):
            logger.warning(
                "Topological loss computed with missing simulation graph — the prediction "
                "phase produced nothing, so this comparison is unmeasurable; scoring it as "
                "maximal uncertainty (1.0), not a match."
            )
            return 1.0

        exec_nodes = {n["id"] for n in self.graph.get("nodes", [])}
        sim_nodes = {n["id"] for n in simulation_graph.get("nodes", [])}

        if not exec_nodes and not sim_nodes:
            return 0.0

        all_nodes = exec_nodes | sim_nodes
        missing_in_exec = len(sim_nodes - exec_nodes)
        extra_in_exec = len(exec_nodes - sim_nodes)
        node_loss = (missing_in_exec + extra_in_exec) / max(len(all_nodes), 1)

        exec_edges = {(e["from"], e["to"]) for e in self.graph.get("edges", []) if e.get("type") != "feedback"}
        sim_edges = {(e["from"], e["to"]) for e in simulation_graph.get("edges", []) if e.get("type") != "feedback"}

        if not exec_edges and not sim_edges:
            edge_loss = 0.0
        else:
            all_edges = exec_edges | sim_edges
            diff_edges = len(exec_edges.symmetric_difference(sim_edges))
            edge_loss = diff_edges / max(len(all_edges), 1)

        return (node_loss + edge_loss) / 2

    def _compute_sync_delta(self, simulation_graph: dict) -> dict[str, Any]:
        """Compute graph delta from simulation graph without mutating topology.

        Returns a graph_delta containing nodes/edges to add. The Planner
        should apply this delta in the next cycle to update topology.
        """
        exec_ids = {n["id"] for n in self.graph.get("nodes", [])}
        new_nodes = [node for node in simulation_graph.get("nodes", []) if node["id"] not in exec_ids]

        exec_edges = {(e["from"], e["to"]) for e in self.graph.get("edges", [])}
        new_edges = [edge for edge in simulation_graph.get("edges", []) if (edge["from"], edge["to"]) not in exec_edges]

        return {
            "nodes": new_nodes,
            "edges": new_edges,
            "remove_nodes": [],
        }

    @property
    def loss(self) -> float:
        return self._last_loss

    def converged(self, threshold: float = 0.1) -> bool:
        return self._last_loss < threshold

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        path = path or Path(os.environ.get("GRAPH_MANAGER_PATH", "/tmp/monkeybrain-graph-state"))
        path.mkdir(parents=True, exist_ok=True)
        (path / "graph.json").write_text(json.dumps(self.graph, indent=2))
        self.q_table.save(path / "q_table.json")
        (path / "annotations.json").write_text(json.dumps(self.annotations, indent=2, default=str))
        (path / "meta.json").write_text(
            json.dumps(
                {
                    "epoch_count": self._epoch_count,
                    "loss": self._last_loss,
                }
            )
        )

    def load(self, path: Path | None = None) -> None:
        path = path or Path(os.environ.get("GRAPH_MANAGER_PATH", "/tmp/monkeybrain-graph-state"))
        graph_file = path / "graph.json"
        if graph_file.exists():
            self.graph = json.loads(graph_file.read_text())
        q_file = path / "q_table.json"
        if q_file.exists():
            self.q_table.load(q_file)
        ann_file = path / "annotations.json"
        if ann_file.exists():
            self.annotations = json.loads(ann_file.read_text())
        meta_file = path / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            self._epoch_count = meta.get("epoch_count", 0)
            self._last_loss = meta.get("loss", 1.0)

    # ── Summary ───────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "nodes": len(self.graph.get("nodes", [])),
            "edges": len(self.graph.get("edges", [])),
            "epoch_count": self._epoch_count,
            "q_table_size": len(self.q_table.snapshot()),
            "loss": round(self._last_loss, 3),
            "annotations": len(self.annotations),
        }
