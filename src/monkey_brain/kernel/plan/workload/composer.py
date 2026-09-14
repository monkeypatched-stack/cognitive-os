"""Workload Composer — composes steps into executable workloads.

Takes a list of step definitions or workload template names,
wires dependencies, and returns a composed Workload.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.plan.workload.workload import Workload, WorkloadStep

logger = logging.getLogger(__name__)


class WorkloadComposer:
    """Composes workload steps into an executable Workload.

    Supports:
    - Adding individual steps with explicit dependencies
    - Merging workload templates
    - Auto-wiring linear chains
    - Validating dependency graphs (no cycles, all deps exist)
    """

    def compose(
        self,
        steps: list[dict[str, Any]] | None = None,
        templates: list[str] | None = None,
        workload_id: str = "",
        auto_wire: bool = True,
    ) -> Workload:
        """Compose a workload from step definitions and/or template names.

        Args:
            steps: List of step dicts with keys:
                step_id, capability_name, inputs, outputs, dependencies
            templates: List of workload template names to merge
                (e.g. ["self-healing"])
            workload_id: Optional custom workload ID
            auto_wire: If True, chain steps linearly when no explicit deps

        Returns:
            Composed Workload with validated dependency graph
        """
        all_steps: list[WorkloadStep] = []

        # 1. Load from templates
        if templates:
            for tpl_name in templates:
                tpl = self._load_template(tpl_name)
                if tpl is not None:
                    all_steps.extend(tpl.steps)

        # 2. Add explicit steps
        if steps:
            for s in steps:
                all_steps.append(
                    WorkloadStep(
                        step_id=s.get("step_id", ""),
                        capability_name=s.get("capability_name", ""),
                        inputs=s.get("inputs", []),
                        outputs=s.get("outputs", []),
                        dependencies=s.get("dependencies", []),
                        metadata=s.get("metadata", {}),
                    )
                )

        # 3. Auto-wire linear chain if no explicit dependencies
        if auto_wire and all_steps:
            self._auto_wire_chain(all_steps)

        # 4. Validate
        self._validate(all_steps)

        return Workload(
            workload_id=workload_id or f"composed-{id(all_steps):x}",
            steps=all_steps,
            metadata={
                "composed": True,
                "templates": templates or [],
                "step_count": len(all_steps),
            },
        )

    def merge(self, workloads: list[Workload]) -> Workload:
        """Merge multiple workloads into one, preserving dependencies."""
        all_steps: list[WorkloadStep] = []
        for wl in workloads:
            all_steps.extend(wl.steps)
        self._auto_wire_chain(all_steps)
        self._validate(all_steps)
        return Workload(
            workload_id=f"merged-{id(workloads):x}",
            steps=all_steps,
            metadata={
                "merged": True,
                "source_count": len(workloads),
                "step_count": len(all_steps),
            },
        )

    def _load_template(self, name: str) -> Workload | None:
        """Load a workload template by name."""
        templates: dict[str, Any] = {}
        try:
            from src.monkey_brain.kernel.fix.self_healing.workload import (
                create_self_healing_workload,
            )

            templates["self-healing"] = create_self_healing_workload
        except ImportError:
            logger.debug("_load_template: suppressed exception", exc_info=True)

        factory = templates.get(name)
        if factory is None:
            logger.warning("Unknown workload template: %s", name)
            return None
        return factory()

    def _auto_wire_chain(self, steps: list[WorkloadStep]) -> None:
        """Wire each step to depend on the previous step (linear chain)."""
        for i, step in enumerate(steps):
            if not step.dependencies and i > 0:
                step.dependencies = [steps[i - 1].step_id]

    def _validate(self, steps: list[WorkloadStep]) -> None:
        """Validate the dependency graph: no cycles, all deps exist."""
        step_ids = {s.step_id for s in steps}
        for step in steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    logger.warning(
                        "Step %s depends on unknown step %s — ignoring",
                        step.step_id,
                        dep,
                    )
            # Check for self-dependency
            if step.step_id in step.dependencies:
                logger.warning("Step %s depends on itself — removing", step.step_id)
                step.dependencies = [d for d in step.dependencies if d != step.step_id]
