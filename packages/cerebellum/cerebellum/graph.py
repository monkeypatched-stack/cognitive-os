"""graph.py — CapabilityGraph: affordance space over CapabilityDescriptors.

Answers: "Given current world state, what capabilities are available?
          And after executing one, what opens and closes?"

This is the feed for L_affordance in SimulationLoss:
    ΔA = A_{t+1} − A_t   (how an action changes the future action space)

Also drives planning: path_to(target) finds the shortest capability sequence
from current world state to a target capability becoming available.

Usage:
    graph = CapabilityGraph()
    graph.add(CreateWorkOrder_descriptor)
    graph.add(AssignWorkOrder_descriptor)

    available = graph.available(world_state)
    gained, lost = graph.delta_affordance("CreateWorkOrder", current_available)
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from cerebellum.descriptor import CapabilityDescriptor


class CapabilityGraph:
    """Directed affordance graph: capability → {enables, disables}.

    Nodes: capability names
    Edges: (A, B, positive=True)  if A.enables contains B
           (A, B, positive=False) if A.disables contains B
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, CapabilityDescriptor] = {}

    # ── Population ────────────────────────────────────────────────────────────

    def add(self, desc: CapabilityDescriptor) -> "CapabilityGraph":
        self._descriptors[desc.name] = desc
        return self

    def add_many(self, descs: list[CapabilityDescriptor]) -> "CapabilityGraph":
        for d in descs:
            self.add(d)
        return self

    def get(self, name: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(name)

    # ── Precondition evaluation ───────────────────────────────────────────────

    def available(self, world_state: dict[str, Any]) -> list[str]:
        """Names of capabilities whose preconditions are satisfied in world_state."""
        return [name for name, desc in self._descriptors.items() if self._preconditions_pass(desc, world_state)]

    def _preconditions_pass(self, desc: CapabilityDescriptor, ws: dict[str, Any]) -> bool:
        return all(self._eval(pred, ws) for pred in desc.preconditions)

    def _eval(self, predicate: str, ws: dict[str, Any]) -> bool:
        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in predicate:
                lhs, rhs = predicate.split(op, 1)
                lv = self._resolve(lhs.strip(), ws)
                rv = self._coerce(rhs.strip())
                try:
                    if op == "==":
                        return lv == rv
                    if op == "!=":
                        return lv != rv
                    if op == ">":
                        return lv > rv  # type: ignore[operator]
                    if op == ">=":
                        return lv >= rv  # type: ignore[operator]
                    if op == "<":
                        return lv < rv  # type: ignore[operator]
                    if op == "<=":
                        return lv <= rv  # type: ignore[operator]
                except Exception:
                    return True
        return True

    @staticmethod
    def _resolve(path: str, state: dict) -> Any:
        cur: Any = state
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur

    @staticmethod
    def _coerce(v: str) -> Any:
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v.strip("'\"")

    # ── Reachability ──────────────────────────────────────────────────────────

    def reachable(self, from_name: str, *, depth: int = 5) -> set[str]:
        """BFS: all capabilities reachable by executing from_name and its successors."""
        visited: set[str] = set()
        q: deque[tuple[str, int]] = deque([(from_name, 0)])
        while q:
            name, d = q.popleft()
            if name in visited or d > depth:
                continue
            visited.add(name)
            desc = self._descriptors.get(name)
            if desc:
                for succ in desc.enables:
                    if succ not in visited:
                        q.append((succ, d + 1))
        visited.discard(from_name)
        return visited

    def path_to(self, target: str, *, world_state: dict[str, Any], max_depth: int = 10) -> list[str] | None:
        """BFS: shortest capability sequence that makes target available.

        Returns [] if target is already available, None if unreachable.
        """
        curr = frozenset(self.available(world_state))
        if target in curr:
            return []

        q: deque[tuple[frozenset[str], list[str]]] = deque([(curr, [])])
        seen: set[frozenset[str]] = {curr}

        while q:
            avail, path = q.popleft()
            if len(path) >= max_depth:
                continue
            for cap in avail:
                desc = self._descriptors.get(cap)
                if not desc:
                    continue
                nxt = (avail | set(desc.enables)) - set(desc.disables)
                new_path = path + [cap]
                if target in nxt:
                    return new_path
                f = frozenset(nxt)
                if f not in seen:
                    seen.add(f)
                    q.append((f, new_path))
        return None

    # ── ΔA (L_affordance feed) ────────────────────────────────────────────────

    def delta_affordance(self, capability: str, current_available: set[str]) -> tuple[set[str], set[str]]:
        """(gained, lost) if capability is executed from current_available."""
        desc = self._descriptors.get(capability)
        if not desc:
            return set(), set()
        gained = set(desc.enables) - current_available
        lost = set(desc.disables) & current_available
        return gained, lost

    def l_affordance_error(
        self,
        predicted_gained: set[str],
        predicted_lost: set[str],
        actual_gained: set[str],
        actual_lost: set[str],
    ) -> float:
        """Jaccard distance on affordance change sets. 0.0=perfect, 1.0=wrong."""

        def jac(a: set, b: set) -> float:
            u = a | b
            return 0.0 if not u else 1.0 - len(a & b) / len(u)

        return round(
            (jac(predicted_gained, actual_gained) + jac(predicted_lost, actual_lost)) / 2,
            4,
        )

    # ── Action model bridge (cortex simulation bridge) ────────────────────────

    def to_action_model(self) -> dict[str, dict[str, set[str]]]:
        """Project to _ACTION_MODEL format for cortex.adversarial_agents.

        {verb: {req: set, next: set, gain: set, lose: set}}
        """
        model: dict[str, dict[str, set[str]]] = {}
        for desc in self._descriptors.values():
            verb = self._verb(desc.name)
            if verb not in model:
                model[verb] = {
                    "req": set(),
                    "next": set(),
                    "gain": set(),
                    "lose": set(),
                }
            e = model[verb]
            e["req"].update(self._to_states(desc.requires))
            e["next"].update(self._to_states(desc.produces))
            for ev in desc.effects:
                e["next"].add(self._last_word(ev))
            e["gain"].update(self._verb(c) for c in desc.enables)
            e["lose"].update(self._verb(c) for c in desc.disables)
        return model

    @staticmethod
    def _verb(name: str) -> str:
        words = re.findall(r"[A-Z][a-z]*|[a-z]+", name)
        return words[0].lower() if words else name.lower().split("_")[0]

    @staticmethod
    def _to_states(entities: list[str]) -> set[str]:
        result = set()
        for e in entities:
            words = re.findall(r"[A-Z][a-z]*|[a-z]+", e)
            result.add("_".join(w.lower() for w in words) if words else e.lower())
        return result

    @staticmethod
    def _last_word(event: str) -> str:
        words = re.findall(r"[A-Z][a-z]*|[a-z]+", event)
        return words[-1].lower() if words else event.lower()

    # ── Introspection ─────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        total_edges = sum(len(d.enables) + len(d.disables) for d in self._descriptors.values())
        return {
            "nodes": len(self._descriptors),
            "edges": total_edges,
            "capabilities": list(self._descriptors.keys()),
        }


# ---------------------------------------------------------------------------
# Module-level singleton — populated at application startup.
# cortex.adversarial_agents reads this via _GLOBAL_GRAPH to enrich _ACTION_MODEL.
# ---------------------------------------------------------------------------

_GLOBAL_GRAPH: CapabilityGraph | None = None


def set_global_graph(graph: CapabilityGraph) -> None:
    """Register the process-wide CapabilityGraph.  Call once at startup."""
    global _GLOBAL_GRAPH
    _GLOBAL_GRAPH = graph


def get_global_graph() -> CapabilityGraph:
    """Return (or lazily create) the process-wide CapabilityGraph."""
    global _GLOBAL_GRAPH
    if _GLOBAL_GRAPH is None:
        _GLOBAL_GRAPH = CapabilityGraph()
    return _GLOBAL_GRAPH
