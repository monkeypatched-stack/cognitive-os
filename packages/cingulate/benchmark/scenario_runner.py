"""Scenario Runner — runs complete end-to-end scenarios.

Validates the complete execution pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


@dataclass
class ScenarioStep:
    """A single step in a scenario."""

    step_id: str = ""
    name: str = ""
    action: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    expected_output: dict[str, Any] = field(default_factory=dict)
    actual_output: dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class ScenarioResult:
    """Result of a complete scenario execution."""

    scenario_id: str = field(default_factory=lambda: f"scenario-{uuid4().hex[:8]}")
    name: str = ""
    status: str = "pending"
    steps: list[ScenarioStep] = field(default_factory=list)
    total_latency_ms: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "status": self.status,
            "steps": [
                {
                    "name": s.name,
                    "passed": s.passed,
                    "latency_ms": round(s.latency_ms, 2),
                    "error": s.error,
                }
                for s in self.steps
            ],
            "total_latency_ms": round(self.total_latency_ms, 2),
            "metrics": self.metrics,
        }


class ScenarioRunner:
    """Runs complete end-to-end scenarios.

    Validates the complete execution pipeline.
    """

    def __init__(self):
        self._scenarios: list[dict[str, Any]] = []
        self._results: list[ScenarioResult] = []

    def register(self, name: str, steps: list[dict[str, Any]]) -> str:
        """Register a scenario."""
        scenario_id = f"scenario-{uuid4().hex[:8]}"
        self._scenarios.append(
            {
                "id": scenario_id,
                "name": name,
                "steps": steps,
            }
        )
        return scenario_id

    async def run(
        self,
        name: str,
        steps: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> ScenarioResult:
        """Run a complete scenario."""
        start = time.monotonic()
        context = context or {}

        result = ScenarioResult(name=name)
        step_results = []

        for step_def in steps:
            step = ScenarioStep(
                step_id=step_def.get("id", f"step-{len(step_results)}"),
                name=step_def.get("name", ""),
                action=step_def.get("action", ""),
                input_data=step_def.get("input", {}),
                expected_output=step_def.get("expected", {}),
            )

            step_start = time.monotonic()

            try:
                fn = step_def.get("function")
                if fn:
                    actual = await fn(step.input_data, context)
                    step.actual_output = actual
                    step.passed = self._validate_step(step.expected_output, actual)
                else:
                    step.passed = True  # No validation needed
            except Exception as e:
                step.error = str(e)
                step.passed = False

            step.latency_ms = (time.monotonic() - step_start) * 1000
            step_results.append(step)

        result.steps = step_results
        result.total_latency_ms = (time.monotonic() - start) * 1000
        result.status = "passed" if all(s.passed for s in step_results) else "failed"

        result.metrics = {
            "total_steps": len(step_results),
            "passed_steps": sum(1 for s in step_results if s.passed),
            "failed_steps": sum(1 for s in step_results if not s.passed),
            "avg_step_latency_ms": (
                round(sum(s.latency_ms for s in step_results) / len(step_results), 2)
                if step_results
                else 0
            ),
        }

        self._results.append(result)
        return result

    def _validate_step(self, expected: dict[str, Any], actual: dict[str, Any]) -> bool:
        """Validate a single step."""
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            if expected_value is not None and actual_value != expected_value:
                return False
        return True

    def get_results(self, status: str | None = None) -> list[ScenarioResult]:
        if status:
            return [r for r in self._results if r.status == status]
        return list(self._results)

    def summary(self) -> dict:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == "passed")
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 3) if total else 0,
        }
