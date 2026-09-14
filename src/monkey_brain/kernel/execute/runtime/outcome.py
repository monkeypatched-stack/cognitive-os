"""Typed execution contracts — AgentResult, ExecutionOutcome, OutcomeSubscriber.

Flow:
  agent.handle() → AgentResult
  Runtime assembles → ExecutionOutcome
  Runtime.publish() → OutcomeSubscriber.on_outcome()

No raw dict promotion. No string-key conventions. Subscribers read typed fields.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Artifact:
    """A discrete output produced by an agent step (file, PR, branch, report)."""

    kind: str  # "file", "pull_request", "branch", "report", "commit"
    name: str  # path, PR number, branch name, etc.
    uri: str = ""  # URL or filesystem path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Strongly-typed result returned by every agent.handle() call.

    Agents never return raw dicts — they construct an AgentResult so the
    runtime can build a typed ExecutionOutcome without key-promotion tricks.
    """

    reward: float = 0.5  # RL signal — consumed by BellmanPolicy
    payload: dict[str, Any] = field(default_factory=dict)  # next-step visible state updates
    artifacts: list[Artifact] = field(default_factory=list)  # files, PRs, commits, reports
    metrics: dict[str, float] = field(default_factory=dict)  # latency, coverage, score, etc.
    observations: list[str] = field(default_factory=list)  # human-readable for Observer/Learning
    evidence: list[dict[str, Any]] = field(default_factory=list)  # for chart evolution
    success: bool = True
    trace_id: str = ""  # propagated from ExecutionContext.trace_id — see ExecutionOutcome below

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "AgentResult":
        """Coerce a legacy raw dict into a typed AgentResult (best-effort)."""
        reward = float(raw.get("reward", 0.5))
        success = raw.get("success", True)
        payload = {
            k: v
            for k, v in raw.items()
            if k
            not in (
                "reward",
                "agent",
                "artifacts",
                "metrics",
                "observations",
                "evidence",
                "success",
                "trace_id",
            )
        }
        artifacts = [Artifact(**a) if isinstance(a, dict) else a for a in raw.get("artifacts", [])]
        return AgentResult(
            reward=reward,
            payload=payload,
            artifacts=artifacts,
            metrics={k: float(v) for k, v in raw.get("metrics", {}).items()},
            observations=raw.get("observations", []),
            evidence=raw.get("evidence", []),
            success=bool(success),
            trace_id=str(raw.get("trace_id", "")),
        )


@dataclass
class ExecutionOutcome:
    """First-class event published by Runtime after each step.

    Subscribers consume typed fields — no raw dict parsing.
    """

    goal: str  # original question / intent goal
    workflow_id: str  # workload_id
    agent_type: str  # "governance", "codegen", "" for pure capability steps
    capability_name: str  # capability that executed
    result: AgentResult  # typed result from the step
    latency_ms: float
    state_before: dict[str, Any]
    state_after: dict[str, Any]  # state_before + result.payload (via reducer)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""  # ExecutionContext.trace_id — was previously untraceable past this
    # point: every OutcomeSubscriber (Lemon metrics, BellmanPolicy's
    # Q-updates, Observer, Learning) received this event with no way
    # to correlate it back to the request that produced it.

    def __post_init__(self) -> None:
        # A step's outcome inherits its result's trace_id if the caller only
        # set one of the two — keeps them from silently disagreeing.
        if not self.trace_id and self.result.trace_id:
            self.trace_id = self.result.trace_id

    @property
    def success(self) -> bool:
        return self.result.success

    @property
    def reward(self) -> float:
        return self.result.reward


@runtime_checkable
class OutcomeSubscriber(Protocol):
    """Anything that wants to consume ExecutionOutcome events."""

    async def on_outcome(self, outcome: ExecutionOutcome) -> None: ...
