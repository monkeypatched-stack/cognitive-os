"""Observer — passive execution recorder.

The Observer watches execution but never controls it.
It records what happened, not what should happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Observation:
    """A single observation of execution."""

    observation_id: str = field(default_factory=lambda: f"obs-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    capability: str = ""
    action: str = ""
    input_state: dict = field(default_factory=dict)
    output_state: dict = field(default_factory=dict)
    reward: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    metadata: dict = field(default_factory=dict)


class Observer:
    """Passive execution observer.

    Responsibilities:
    - Record every capability execution
    - Compute execution metrics
    - Feed observations to Learning

    The Observer never:
    - Reorders execution
    - Schedules work
    - Modifies Policy
    - Chooses capabilities
    """

    def __init__(self):
        self._observations: list[Observation] = []

    def observe(
        self,
        capability: str,
        action: str,
        input_state: dict,
        output_state: dict,
        reward: float = 0.0,
        latency_ms: float = 0.0,
        success: bool = True,
        **metadata,
    ) -> Observation:
        """Record an execution observation."""
        obs = Observation(
            capability=capability,
            action=action,
            input_state=input_state,
            output_state=output_state,
            reward=reward,
            latency_ms=latency_ms,
            success=success,
            metadata=metadata,
        )
        self._observations.append(obs)
        return obs

    def get_observations(self) -> list[Observation]:
        return list(self._observations)

    def get_last_observation(self) -> Observation | None:
        return self._observations[-1] if self._observations else None

    def clear(self):
        self._observations.clear()

    async def on_outcome(self, outcome) -> None:
        """OutcomeSubscriber — record typed ExecutionOutcome as an Observation."""
        self.observe(
            capability=outcome.capability_name,
            action=outcome.agent_type or "execute",
            input_state=outcome.state_before,
            output_state=outcome.state_after,
            reward=outcome.result.reward,  # typed
            latency_ms=outcome.latency_ms,
            success=outcome.result.success,  # typed
            workflow_id=outcome.workflow_id,
            goal=outcome.goal,
            observations=outcome.result.observations,  # typed — human-readable notes
            artifacts_count=len(outcome.result.artifacts),
        )

    def summary(self) -> dict:
        if not self._observations:
            return {"count": 0}

        rewards = [o.reward for o in self._observations]
        latencies = [o.latency_ms for o in self._observations]
        successes = [o.success for o in self._observations]

        return {
            "count": len(self._observations),
            "total_reward": sum(rewards),
            "avg_reward": sum(rewards) / len(rewards),
            "avg_latency_ms": sum(latencies) / len(latencies),
            "success_rate": sum(successes) / len(successes),
            "capabilities_used": list(set(o.capability for o in self._observations)),
        }
