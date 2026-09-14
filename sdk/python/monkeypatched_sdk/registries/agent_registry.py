"""Agent Registry for SDK.

Agents register themselves with capabilities.
The platform queries this to find who can perform an action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    agent_id: str
    name: str
    capabilities: list[str]
    model_constraints: dict[str, Any] = field(default_factory=dict)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    """Global registry of agents.

    Agents register at startup. Platform queries at runtime.

    Singleton via AgentRegistry.instance() — but _agents is an instance
    variable so test isolation works correctly (each instance gets its own dict).
    """

    _instance: "AgentRegistry | None" = None

    def __init__(self):
        # Instance variable — not class variable.
        # Prevents bleed between test cases when registry is re-instantiated.
        self._agents: dict[str, AgentInfo] = {}

    @classmethod
    def instance(cls) -> "AgentRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — test isolation only, never call in production."""
        cls._instance = None

    def register(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        model_constraints: dict[str, Any] | None = None,
        resource_limits: dict[str, Any] | None = None,
        **metadata: Any,
    ) -> None:
        self._agents[agent_id] = AgentInfo(
            agent_id=agent_id,
            name=name,
            capabilities=capabilities,
            model_constraints=model_constraints or {},
            resource_limits=resource_limits or {},
            metadata=metadata,
        )
        logger.info("Agent %s registered (%s)", agent_id, capabilities)

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        return self._agents.get(agent_id)

    def find_agents_for_capability(self, capability: str) -> list[AgentInfo]:
        """Return all agents that advertise the given capability."""
        return [a for a in self._agents.values() if capability in a.capabilities]

    def find_best_agent_for_capability(
        self,
        capability: str,
        constraints: dict[str, Any] | None = None,
    ) -> AgentInfo | None:
        """Return the highest-priority agent matching capability and constraints.

        Constraint matching is exact — every key/value in constraints must match
        the agent's model_constraints. Agents that don't satisfy all constraints
        are excluded.

        Scoring is currently first-registered wins. This is the seam where the
        UCB/Q-network bandit scheduler slots in on v3 — replace the return
        statement with a scored selection over candidates without changing
        any call sites.

        Args:
            capability: pipeline_id or action name the agent must support.
            constraints: optional model_constraints filter e.g.
                         {"model": "qwen2.5:32b", "max_tokens": 4096}

        Returns:
            Best matching AgentInfo, or None if no candidates found.
        """
        candidates = self.find_agents_for_capability(capability)

        if not candidates:
            logger.warning("No agents found for capability '%s'", capability)
            return None

        if constraints:
            candidates = [a for a in candidates if all(a.model_constraints.get(k) == v for k, v in constraints.items())]

        if not candidates:
            logger.warning(
                "No agents for capability '%s' satisfy constraints %s",
                capability,
                constraints,
            )
            return None

        # TODO v3: replace with UCB/Q-network bandit scoring over candidates
        # scored = sorted(candidates, key=lambda a: bandit.score(a.agent_id), reverse=True)
        # return scored[0]
        return candidates[0]

    def all_agents(self) -> list[AgentInfo]:
        return list(self._agents.values())

    def summary(self) -> dict[str, Any]:
        return {
            "agent_count": len(self._agents),
            "agents": {a.agent_id: {"name": a.name, "capabilities": a.capabilities} for a in self._agents.values()},
        }
