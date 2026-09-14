"""WorkloadDAG — explicit directed acyclic graph of workload steps.

Nodes  : step_id → WorkloadStep
Edges  : step_id → [dep_step_id, ...]   (each step points at what it depends on)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.monkey_brain.kernel.plan.workload.workload import WorkloadStep

logger = logging.getLogger("agentos.workload_dag")


class DAGValidationError(ValueError):
    """Raised when a WorkloadDAG fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"DAG validation failed ({len(errors)} error(s)):\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass
class WorkloadDAG:
    nodes: dict[str, "WorkloadStep"] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_steps(cls, steps: list["WorkloadStep"]) -> "WorkloadDAG":
        """Build a DAG from steps and validate it. Raises DAGValidationError if invalid."""
        nodes = {s.step_id: s for s in steps}
        edges = {s.step_id: list(s.dependencies) for s in steps}
        dag = cls(nodes=nodes, edges=edges)
        dag.validate_or_raise()
        return dag

    def validate(self) -> list[str]:
        """Return all structural errors. Empty list means the DAG is valid."""
        errors: list[str] = []
        errors.extend(self._check_unknown_deps())
        errors.extend(self._check_self_loops())
        errors.extend(self._check_cycles())
        errors.extend(self._check_capability_names())
        return errors

    def validate_or_raise(self) -> None:
        """Run validate() and raise DAGValidationError if any errors are found."""
        errors = self.validate()
        if errors:
            for err in errors:
                logger.error("DAG validation: %s", err)
            raise DAGValidationError(errors)

    def _check_unknown_deps(self) -> list[str]:
        errors = []
        for step_id, deps in self.edges.items():
            for dep in deps:
                if dep not in self.nodes:
                    errors.append(f"Step {step_id!r} depends on unknown step {dep!r}")
        return errors

    def _check_self_loops(self) -> list[str]:
        return [f"Step {step_id!r} depends on itself" for step_id, deps in self.edges.items() if step_id in deps]

    def _check_cycles(self) -> list[str]:
        """DFS cycle detection — returns one error per back-edge found.

        ITERATIVE (explicit stack), not recursion: the recursive form overflowed Python's
        ~1000-frame limit on a deep dependency chain — a workload with ~1500 chained steps
        raised RecursionError right here inside from_steps() validation, and the
        /workloads route surfaced that as a 500. Depth is now bounded only by memory.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in self.nodes}
        errors: list[str] = []

        for start in self.nodes:
            if color[start] != WHITE:
                continue
            color[start] = GRAY
            stack: list[tuple[str, "object"]] = [(start, iter(self.edges.get(start, [])))]
            while stack:
                node, it = stack[-1]
                descended = False
                for dep in it:  # resumes where this frame left off
                    if dep not in color:
                        continue
                    if color[dep] == GRAY:
                        errors.append(f"Cycle detected: step {node!r} → step {dep!r}")
                    elif color[dep] == WHITE:
                        color[dep] = GRAY
                        stack.append((dep, iter(self.edges.get(dep, []))))
                        descended = True
                        break
                if not descended:
                    color[node] = BLACK
                    stack.pop()

        return errors

    def _check_capability_names(self) -> list[str]:
        return [
            f"Step {step.step_id!r} has an empty capability_name"
            for step in self.nodes.values()
            if not getattr(step, "capability_name", "").strip()
        ]

    def roots(self) -> list["WorkloadStep"]:
        """Steps with no dependencies — the starting nodes."""
        return [self.nodes[sid] for sid, deps in self.edges.items() if not deps]

    def successors(self, step_id: str) -> list["WorkloadStep"]:
        """Steps that list step_id as a dependency (downstream consumers)."""
        return [self.nodes[sid] for sid, deps in self.edges.items() if step_id in deps and sid in self.nodes]

    def topo_sort(self) -> list["WorkloadStep"]:
        """Return all steps in dependency order (dependencies before dependents).

        ITERATIVE post-order DFS — the recursive form overflowed on deep chains (a ~1500-
        step chain raised RecursionError). A node is appended only after all its
        in-graph dependencies have been appended; the `on_stack` guard breaks a cycle the
        same way the recursive `in_progress` check did (from_steps validates cycles away,
        so this only matters for a directly-constructed cyclic DAG).
        """
        visited: set[str] = set()
        on_stack: set[str] = set()
        ordered: list["WorkloadStep"] = []

        for start in self.nodes:
            if start in visited:
                continue
            stack: list[tuple[str, "object"]] = [(start, iter(self.edges.get(start, [])))]
            on_stack.add(start)
            while stack:
                node, it = stack[-1]
                descended = False
                for dep_id in it:
                    if dep_id not in self.nodes or dep_id in visited or dep_id in on_stack:
                        continue  # missing / done / cycle back-edge
                    stack.append((dep_id, iter(self.edges.get(dep_id, []))))
                    on_stack.add(dep_id)
                    descended = True
                    break
                if not descended:
                    visited.add(node)
                    on_stack.discard(node)
                    ordered.append(self.nodes[node])
                    stack.pop()

        return ordered

    def summary(self) -> dict:
        errors = self.validate()
        return {
            "step_count": len(self.nodes),
            "edge_count": sum(len(d) for d in self.edges.values()),
            "roots": [s.step_id for s in self.roots()],
            "valid": not errors,
            "errors": errors,
        }
