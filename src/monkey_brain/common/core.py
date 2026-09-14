"""Domain-Agnostic Core - The heart of Monkey Brain's domain-agnostic execution."""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypeVar
from abc import ABC, abstractmethod
import uuid
from datetime import datetime

logger = logging.getLogger("monkey_brain.common.core")

# Type variables for domain-agnostic components
T = TypeVar("T")
AgentType = TypeVar("AgentType", bound="BaseAgent")
CapabilityType = TypeVar("CapabilityType", bound="BaseCapability")


@dataclass
class DomainGoal:
    """Domain-agnostic goal representation."""

    domain: str
    objective: str
    success_criteria: str
    goal_id: str = field(default_factory=lambda: f"goal-{uuid.uuid4().hex[:8]}")
    priority: str = "medium"
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ExecutionOutcome:
    """Domain-agnostic execution result."""

    execution_id: str
    goal_id: str
    domain: str
    status: str  # SUCCESS, FAILED, PARTIAL, etc.
    confidence: float = 1.0
    results: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class BaseAgent(ABC):
    """Domain-agnostic base agent interface."""

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """Unique identifier for this agent."""
        pass

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain this agent operates in."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> List[str]:
        """List of capability IDs this agent provides."""
        pass

    @abstractmethod
    async def execute(self, goal: DomainGoal, context: Dict[str, Any]) -> ExecutionOutcome:
        """Execute against a domain goal."""
        pass

    @abstractmethod
    def validate_goal(self, goal: DomainGoal) -> bool:
        """Validate if this agent can handle the goal."""
        pass


class BaseCapability(ABC):
    """Domain-agnostic capability interface."""

    @property
    @abstractmethod
    def capability_id(self) -> str:
        """Unique identifier for this capability."""
        pass

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain this capability belongs to."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        pass

    @abstractmethod
    def can_handle(self, goal: DomainGoal) -> bool:
        """Check if this capability can handle the goal."""
        pass


class DomainRegistry:
    """Registry for domain-specific agents and capabilities."""

    def __init__(self):
        self._agents: Dict[str, List[BaseAgent]] = {}  # domain -> agents
        self._capabilities: Dict[str, List[BaseCapability]] = {}  # domain -> capabilities
        self._agent_capability_map: Dict[str, List[str]] = {}  # agent_id -> capability_ids

    def register_agent(self, agent: BaseAgent) -> None:
        """Register an agent in the registry."""
        if agent.domain not in self._agents:
            self._agents[agent.domain] = []
        self._agents[agent.domain].append(agent)

        # Map agent to its capabilities
        self._agent_capability_map[agent.agent_id] = agent.capabilities

    def register_capability(self, capability: BaseCapability) -> None:
        """Register a capability in the registry."""
        if capability.domain not in self._capabilities:
            self._capabilities[capability.domain] = []
        self._capabilities[capability.domain].append(capability)

    def find_agents_for_goal(self, goal: DomainGoal) -> List[BaseAgent]:
        """Find agents that can handle a specific goal."""
        domain_agents = self._agents.get(goal.domain, [])
        return [agent for agent in domain_agents if agent.validate_goal(goal)]

    def find_capabilities_for_goal(self, goal: DomainGoal) -> List[BaseCapability]:
        """Find capabilities that can handle a specific goal."""
        domain_capabilities = self._capabilities.get(goal.domain, [])
        return [cap for cap in domain_capabilities if cap.can_handle(goal)]

    def get_domain_agents(self, domain: str) -> List[BaseAgent]:
        """Get all agents for a specific domain."""
        return self._agents.get(domain, [])

    def get_domain_capabilities(self, domain: str) -> List[BaseCapability]:
        """Get all capabilities for a specific domain."""
        return self._capabilities.get(domain, [])

    def get_all_domains(self) -> List[str]:
        """Get all registered domains."""
        return list(set(self._agents.keys()) | set(self._capabilities.keys()))


class CognitiveProcessGenerator:
    """Domain-agnostic process generator that creates execution workflows."""

    def __init__(self, registry: DomainRegistry):
        self.registry = registry

    def generate_workflow(self, goal: DomainGoal) -> List[BaseAgent]:
        """Generate an execution workflow for a domain goal."""
        # Find agents that can handle this goal
        suitable_agents = self.registry.find_agents_for_goal(goal)

        if not suitable_agents:
            raise ValueError(f"No agents found for goal: {goal.objective} in domain: {goal.domain}")

        # ASSUMPTION: single planner owns workflow ordering.
        # No negotiation between agents over execution sequence.
        # STUB: naive sequential workflow — no dependency resolution, parallelism, priority ordering, or retry logic
        return suitable_agents

    async def execute_workflow(self, goal: DomainGoal) -> ExecutionOutcome:
        """Execute the generated workflow."""
        workflow = self.generate_workflow(goal)
        combined_results = {}
        all_evidence = {}

        # Execute each agent in sequence
        for agent in workflow:
            try:
                result = await agent.execute(goal, goal.context)
                combined_results[agent.agent_id] = result.results
                all_evidence[agent.agent_id] = result.evidence
            except Exception as e:
                # Agent failed, but continue with others
                combined_results[agent.agent_id] = {"error": str(e)}
                all_evidence[agent.agent_id] = {"error": str(e)}

        # Determine overall status
        success_count = sum(1 for r in combined_results.values() if "error" not in r)
        total_agents = len(workflow)

        if success_count == total_agents:
            status = "SUCCESS"
            confidence = 1.0
        elif success_count > 0:
            status = "PARTIAL"
            confidence = success_count / total_agents
        else:
            status = "FAILED"
            confidence = 0.0

        return ExecutionOutcome(
            execution_id=f"exec-{uuid.uuid4().hex[:8]}",
            goal_id=goal.goal_id,
            domain=goal.domain,
            status=status,
            confidence=confidence,
            results=combined_results,
            evidence=all_evidence,
        )


class MonkeyBrainRuntime:
    """Domain-agnostic Monkey Brain runtime."""

    def __init__(self):
        self.registry = DomainRegistry()
        self.process_generator = CognitiveProcessGenerator(self.registry)
        self.execution_history: List[ExecutionOutcome] = []

    def register_domain_components(self, agents: List[BaseAgent], capabilities: List[BaseCapability]) -> None:
        """Register agents and capabilities for a domain."""
        for agent in agents:
            self.registry.register_agent(agent)
        for capability in capabilities:
            self.registry.register_capability(capability)

    async def execute_goal(self, goal: DomainGoal) -> ExecutionOutcome:
        """Execute a domain goal through the cognitive process."""
        # Validate domain exists
        if goal.domain not in self.registry.get_all_domains():
            raise ValueError(f"Domain not registered: {goal.domain}")

        # Generate and execute workflow
        outcome = await self.process_generator.execute_workflow(goal)

        # Store execution history
        self.execution_history.append(outcome)

        return outcome

    def get_execution_history(self, domain: Optional[str] = None) -> List[ExecutionOutcome]:
        """Get execution history, optionally filtered by domain."""
        if domain:
            return [o for o in self.execution_history if o.domain == domain]
        return self.execution_history

    def get_domain_stats(self, domain: str) -> Dict[str, Any]:
        """Get statistics for a specific domain."""
        domain_outcomes = self.get_execution_history(domain)

        if not domain_outcomes:
            return {
                "domain": domain,
                "total_executions": 0,
                "success_rate": 0.0,
                "agents_registered": len(self.registry.get_domain_agents(domain)),
                "capabilities_registered": len(self.registry.get_domain_capabilities(domain)),
            }

        success_count = sum(1 for o in domain_outcomes if o.status == "SUCCESS")

        return {
            "domain": domain,
            "total_executions": len(domain_outcomes),
            "success_rate": (success_count / len(domain_outcomes)) * 100,
            "agents_registered": len(self.registry.get_domain_agents(domain)),
            "capabilities_registered": len(self.registry.get_domain_capabilities(domain)),
            "average_confidence": sum(o.confidence for o in domain_outcomes) / len(domain_outcomes),
        }
