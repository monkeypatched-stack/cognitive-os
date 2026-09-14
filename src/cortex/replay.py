"""Replay — replay historical executions.

Used for:
- Debugging failed executions
- Training Policy on past outcomes
- Comparing predicted vs actual
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from monkeypatched_sdk.models import Pipeline
from src.monkey_brain.kernel.fix.loss.loss import Loss, compute_loss
from src.monkey_brain.runtime.runtime import ExecutionResult


@dataclass
class Recording:
    """A recorded execution for replay."""

    recording_id: str = field(default_factory=lambda: f"rec-{uuid4().hex[:8]}")
    pipeline: Pipeline = field(default_factory=Pipeline)
    initial_state: dict[str, Any] = field(default_factory=dict)
    result: ExecutionResult | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Comparison:
    """Comparison of predicted vs actual outcome."""

    comparison_id: str = field(default_factory=lambda: f"comp-{uuid4().hex[:8]}")
    prediction_id: str = ""
    actual_pipeline_id: str = ""
    predicted_state: dict[str, Any] = field(default_factory=dict)
    actual_state: dict[str, Any] = field(default_factory=dict)
    loss: Loss = field(default_factory=Loss)
    component_losses: dict[str, float] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class ReplayEngine:
    """Replays historical executions.

    Responsibilities:
    - Record executions for later replay
    - Replay recorded executions
    - Compare predicted vs actual outcomes

    The ReplayEngine never:
    - Executes real capabilities
    - Modifies production state
    - Makes policy decisions
    """

    def __init__(self):
        self._recordings: list[Recording] = []
        self._comparisons: list[Comparison] = []

    def record(
        self,
        execution: ExecutionResult,
        pipeline: Pipeline,
        initial_state: dict[str, Any] | None = None,
    ) -> Recording:
        """Record an execution for later replay."""
        recording = Recording(
            pipeline=pipeline,
            initial_state=initial_state or {},
            result=execution,
            metadata={
                "success": execution.success,
                "latency_ms": execution.total_latency_ms,
                "step_count": execution.step_count(),
            },
        )
        self._recordings.append(recording)
        return recording

    def get_recording(self, recording_id: str) -> Recording | None:
        for rec in self._recordings:
            if rec.recording_id == recording_id:
                return rec
        return None

    def get_recordings(self) -> list[Recording]:
        return list(self._recordings)

    def compare(
        self,
        prediction_id: str,
        predicted_state: dict[str, Any],
        actual: ExecutionResult,
        actual_state: dict[str, Any] | None = None,
    ) -> Comparison:
        """Compare predicted vs actual outcome."""
        actual_state = actual_state or actual.final_state

        loss = compute_loss(predicted_state, actual_state)

        comparison = Comparison(
            prediction_id=prediction_id,
            actual_pipeline_id=actual.pipeline_id,
            predicted_state=predicted_state,
            actual_state=actual_state,
            loss=loss,
            component_losses=loss.components,
            metadata={
                "actual_success": actual.success,
                "actual_latency_ms": actual.total_latency_ms,
            },
        )
        self._comparisons.append(comparison)
        return comparison

    def get_comparison(self, comparison_id: str) -> Comparison | None:
        for comp in self._comparisons:
            if comp.comparison_id == comparison_id:
                return comp
        return None

    def get_comparisons(self) -> list[Comparison]:
        return list(self._comparisons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordings": len(self._recordings),
            "comparisons": len(self._comparisons),
        }
