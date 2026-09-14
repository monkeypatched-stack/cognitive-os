"""Workload result dataclasses — healing, stability, and combined outcome."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HealingResult:
    outcome: str  # "success" | "failure" | "skipped"
    passes: int = 0
    reason: str = ""


@dataclass
class StabilityResult:
    outcome: str  # "complete" | "spec_review" | "skipped"
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkloadOutcome:
    run_type: str
    healing: HealingResult | None = None
    stability: StabilityResult | None = None

    @property
    def final_status(self) -> str:
        if self.stability:
            return self.stability.outcome  # "complete" | "spec_review"
        if self.healing:
            return "healing_" + self.healing.outcome
        return "codegen"

    def to_dict(self) -> dict:
        return {
            "run_type": self.run_type,
            "final_status": self.final_status,
            "healing": (
                {
                    "outcome": self.healing.outcome,
                    "passes": self.healing.passes,
                    "reason": self.healing.reason,
                }
                if self.healing
                else None
            ),
            "stability": (
                {
                    "outcome": self.stability.outcome,
                    "conditions": self.stability.conditions,
                }
                if self.stability
                else None
            ),
        }
