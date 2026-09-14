"""Execution State — Single source of truth for runtime execution.

This module implements the unified ExecutionState that gets incrementally
augmented by capabilities. Every capability consumes the current state,
performs its operation, and returns an updated state.

Key Principles:
- Single source of truth for all runtime data
- Incrementally augmented, never replaced
- Contains all knowledge accumulated during execution
- Used by Bellman for policy decisions
- Observed by ExecutionObserver for loss calculation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class ExecutionPhase(str, Enum):
    """Current phase of execution."""

    INITIALIZED = "initialized"
    ENTITY_RESOLUTION = "entity_resolution"
    HIERARCHY_RESOLUTION = "hierarchy_resolution"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    ANALYSIS = "analysis"
    ANSWER_GENERATION = "answer_generation"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionState:
    """Unified execution state that gets incrementally augmented by capabilities."""

    # Core execution context
    question: str = ""
    intent: str = ""
    phase: ExecutionPhase = ExecutionPhase.INITIALIZED

    # Entity resolution results
    entities: list[dict[str, Any]] = field(default_factory=list)
    resolved_entities: dict[str, dict[str, Any]] = field(default_factory=dict)  # entity_type -> entity

    # Hierarchy resolution results
    hierarchy: dict[str, Any] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)

    # Knowledge retrieval results
    graph_entities: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    web_results: list[dict[str, Any]] = field(default_factory=list)

    # Execution metadata
    execution_stats: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    policy_updates: list[dict[str, Any]] = field(default_factory=list)

    # Capability execution history
    capability_history: list[str] = field(default_factory=list)
    execution_trace: list[dict[str, Any]] = field(default_factory=list)

    # Current answer state
    current_answer: str = ""
    answer_quality: float = 0.0
    confidence: float = 0.0

    # Error handling
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_entity(self, entity_type: str, entity: dict[str, Any]) -> None:
        """Add a resolved entity to the state."""
        self.entities.append(entity)
        self.resolved_entities[entity_type] = entity

    def add_hierarchy(self, hierarchy_data: dict[str, Any]) -> None:
        """Add hierarchy information to the state."""
        self.hierarchy.update(hierarchy_data)

    def add_relationship(self, relationship: dict[str, Any]) -> None:
        """Add a relationship to the state."""
        self.relationships.append(relationship)

    def add_graph_entities(self, entities: list[dict[str, Any]]) -> None:
        """Add graph entities to the state."""
        self.graph_entities.extend(entities)

    def add_documents(self, documents: list[dict[str, Any]]) -> None:
        """Add documents to the state."""
        self.documents.extend(documents)

    def add_web_results(self, results: list[dict[str, Any]]) -> None:
        """Add web search results to the state."""
        self.web_results.extend(results)

    def record_capability_execution(self, capability_name: str, result: dict[str, Any]) -> None:
        """Record that a capability was executed."""
        self.capability_history.append(capability_name)
        self.execution_trace.append(
            {
                "capability": capability_name,
                "result": result,
                "timestamp": result.get("timestamp", ""),
            }
        )

    def add_observation(self, observation: dict[str, Any]) -> None:
        """Add an observation for policy learning."""
        self.observations.append(observation)

    def add_policy_update(self, policy_update: dict[str, Any]) -> None:
        """Add a policy update for Bellman optimization."""
        self.policy_updates.append(policy_update)

    def set_answer(self, answer: str, quality: float = 0.0, confidence: float = 0.0) -> None:
        """Set the current answer state."""
        self.current_answer = answer
        self.answer_quality = quality
        self.confidence = confidence

    def add_error(self, error: str) -> None:
        """Add an error to the state."""
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add a warning to the state."""
        self.warnings.append(warning)

    def is_complete(self) -> bool:
        """Check if execution is complete."""
        return self.phase == ExecutionPhase.COMPLETED or self.phase == ExecutionPhase.FAILED

    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return len(self.errors) > 0

    def get_entity(self, entity_type: str) -> Optional[dict[str, Any]]:
        """Get a resolved entity by type."""
        return self.resolved_entities.get(entity_type)

    def has_data(self, key: str) -> bool:
        """Check if state contains specific data."""
        return key in self.execution_stats

    def get_data(self, key: str) -> Any:
        """Get data from execution stats."""
        return self.execution_stats.get(key)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "question": self.question,
            "intent": self.intent,
            "phase": self.phase.value,
            "entities": self.entities,
            "resolved_entities": self.resolved_entities,
            "hierarchy": self.hierarchy,
            "relationships": self.relationships,
            "graph_entities": self.graph_entities,
            "documents": self.documents,
            "web_results": self.web_results,
            "execution_stats": self.execution_stats,
            "observations": self.observations,
            "policy_updates": self.policy_updates,
            "capability_history": self.capability_history,
            "execution_trace": self.execution_trace,
            "current_answer": self.current_answer,
            "answer_quality": self.answer_quality,
            "confidence": self.confidence,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionState:
        """Create state from dictionary."""
        state = cls(
            question=data.get("question", ""),
            intent=data.get("intent", ""),
            phase=ExecutionPhase(data.get("phase", ExecutionPhase.INITIALIZED.value)),
        )

        # Restore all the collected data
        state.entities = data.get("entities", [])
        state.resolved_entities = data.get("resolved_entities", {})
        state.hierarchy = data.get("hierarchy", {})
        state.relationships = data.get("relationships", [])
        state.graph_entities = data.get("graph_entities", [])
        state.documents = data.get("documents", [])
        state.web_results = data.get("web_results", [])
        state.execution_stats = data.get("execution_stats", {})
        state.observations = data.get("observations", [])
        state.policy_updates = data.get("policy_updates", [])
        state.capability_history = data.get("capability_history", [])
        state.execution_trace = data.get("execution_trace", [])
        state.current_answer = data.get("current_answer", "")
        state.answer_quality = data.get("answer_quality", 0.0)
        state.confidence = data.get("confidence", 0.0)
        state.errors = data.get("errors", [])
        state.warnings = data.get("warnings", [])

        return state


@dataclass
class CapabilityResult:
    """Result returned by every capability execution."""

    # Updated execution state
    updated_state: ExecutionState

    # Execution metadata
    capability_name: str = ""
    success: bool = True
    latency: float = 0.0
    timestamp: str = ""

    # Policy learning data
    observations: list[dict[str, Any]] = field(default_factory=list)
    transition_reward: float = 0.0
    execution_cost: float = 0.0
    confidence: float = 0.0

    # Execution details
    execution_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "capability_name": self.capability_name,
            "success": self.success,
            "latency": self.latency,
            "timestamp": self.timestamp,
            "observations": self.observations,
            "transition_reward": self.transition_reward,
            "execution_cost": self.execution_cost,
            "confidence": self.confidence,
            "execution_metadata": self.execution_metadata,
            "updated_state": self.updated_state.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityResult:
        """Create from dictionary."""
        return cls(
            updated_state=ExecutionState.from_dict(data["updated_state"]),
            capability_name=data.get("capability_name", ""),
            success=data.get("success", True),
            latency=data.get("latency", 0.0),
            timestamp=data.get("timestamp", ""),
            observations=data.get("observations", []),
            transition_reward=data.get("transition_reward", 0.0),
            execution_cost=data.get("execution_cost", 0.0),
            confidence=data.get("confidence", 0.0),
            execution_metadata=data.get("execution_metadata", {}),
        )
