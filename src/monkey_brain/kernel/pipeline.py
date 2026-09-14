"""Pipeline stub — provides Pipeline and PipelineStep for cortex compatibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineStep:
    """A step in a pipeline."""

    name: str = ""
    capability: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Pipeline:
    """A pipeline of steps."""

    name: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: PipelineStep) -> None:
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": [{"name": s.name, "capability": s.capability} for s in self.steps],
            "metadata": self.metadata,
        }
