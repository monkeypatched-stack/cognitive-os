"""Feedback Engine — evaluates executions and drives learning.

Every completed execution produces a Feedback Record.
Learning is asynchronous and never blocks user responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class FeedbackRecord:
    """A complete feedback record for an execution."""

    feedback_id: str = field(default_factory=lambda: f"fb-{uuid4().hex[:12]}")

    # Execution Context
    question: str = ""
    intent: str = ""
    workflow: str = ""
    pipeline_id: str = ""
    capabilities: list[str] = field(default_factory=list)

    # Results
    answer: str = ""
    success: bool = False
    latency_ms: float = 0.0

    # Ground Truth
    ground_truth: dict[str, Any] = field(default_factory=dict)

    # User Feedback
    user_feedback: dict[str, Any] = field(default_factory=dict)

    # Metrics
    metrics: dict[str, Any] = field(default_factory=dict)

    # Learning
    reward: float = 0.0
    loss: float = 0.0
    reward_components: dict[str, float] = field(default_factory=dict)
    loss_components: dict[str, float] = field(default_factory=dict)

    # Timestamp
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "question": self.question,
            "intent": self.intent,
            "pipeline_id": self.pipeline_id,
            "capabilities": self.capabilities,
            "answer": self.answer,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "reward": self.reward,
            "loss": self.loss,
            "timestamp": self.timestamp,
        }


class FeedbackEngine:
    """Evaluates executions and drives learning.

    Responsibilities:
    - Collect execution feedback
    - Compute rewards and losses
    - Update policies
    - Store experiences
    - Emit telemetry

    Learning is asynchronous and never blocks user responses.
    """

    def __init__(self):
        self._records: list[FeedbackRecord] = []
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def record(
        self,
        question: str,
        intent: str,
        pipeline_id: str,
        capabilities: list[str],
        answer: str,
        success: bool,
        latency_ms: float,
        ground_truth: dict[str, Any] | None = None,
        user_feedback: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        """Record feedback for an execution."""
        record = FeedbackRecord(
            question=question,
            intent=intent,
            pipeline_id=pipeline_id,
            capabilities=capabilities,
            answer=answer,
            success=success,
            latency_ms=latency_ms,
            ground_truth=ground_truth or {},
            user_feedback=user_feedback or {},
            metrics=metrics or {},
        )

        self._records.append(record)

        if self._lemon:
            self._lemon.counter("xavier.executions_evaluated")
            self._lemon.histogram("xavier.latency_ms", latency_ms)

        return record

    def get_records(self, limit: int = 100) -> list[FeedbackRecord]:
        return self._records[-limit:]

    def get_record(self, feedback_id: str) -> FeedbackRecord | None:
        for r in self._records:
            if r.feedback_id == feedback_id:
                return r
        return None

    def summary(self) -> dict:
        if not self._records:
            return {"total": 0}

        rewards = [r.reward for r in self._records]
        losses = [r.loss for r in self._records]

        return {
            "total": len(self._records),
            "avg_reward": sum(rewards) / len(rewards),
            "avg_loss": sum(losses) / len(losses),
            "success_rate": sum(1 for r in self._records if r.success) / len(self._records),
        }
