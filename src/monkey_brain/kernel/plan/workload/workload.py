"""Workload — executable capability graph.

A Workload is a sequence of capability steps that the Runtime executes.
The Kernel produces Workloads. The Runtime executes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class WorkloadStep:
    """A single step in a capability workload."""

    step_id: str = field(default_factory=lambda: f"step-{uuid4().hex[:8]}")
    capability_name: str = ""
    agent_name: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Workload:
    """An executable graph of capability steps.

    The Kernel generates candidate Workloads.
    The Policy selects which Workload to execute.
    The Runtime executes the selected Workload.
    """

    workload_id: str = field(default_factory=lambda: f"pipe-{uuid4().hex[:8]}")
    steps: list[WorkloadStep] = field(default_factory=list)
    estimated_cost: float = 1.0
    estimated_latency: float = 1.0
    metadata: dict = field(default_factory=dict)
    dag: Any = field(default=None, repr=False)
    edges: list[dict] = field(default_factory=list)  # For graph validation

    def build_dag(self) -> "Any":
        """Build, validate, and cache a WorkloadDAG from self.steps.

        Raises DAGValidationError if the graph has cycles, unknown deps,
        self-loops, or steps with empty capability names.
        """
        from src.monkey_brain.kernel.plan.workload.dag import WorkloadDAG

        self.dag = WorkloadDAG.from_steps(self.steps)
        return self.dag

    def step_names(self) -> list[str]:
        return [s.capability_name for s in self.steps]

    def is_empty(self) -> bool:
        return len(self.steps) == 0
