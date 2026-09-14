"""Capability Interface — Uniform execution interface for all runtime capabilities.

This module defines the ICapability interface that all capabilities must implement.
Every capability transforms execution state and contributes to policy learning.

Key Principles:
- Every capability implements the same interface
- All capabilities are treated identically by the runtime
- No privileged execution stages
- Every capability augments execution state
- Every capability contributes policy observations
- Bellman selects capabilities, not pipelines
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.monkey_brain.kernel.execute.state.execution import (
    ExecutionState,
    CapabilityResult,
)


class ICapability(ABC):
    """Core capability interface — all capabilities must implement.

    Every capability:
    - consumes an execution state
    - augments the execution state
    - emits observations
    - contributes policy updates
    - returns a new execution state

    The runtime treats all capabilities identically.
    """

    @property
    @abstractmethod
    def capability_name(self) -> str:
        """Unique name of this capability."""
        ...

    @property
    @abstractmethod
    def capability_type(self) -> str:
        """Type of capability (e.g., 'entity_resolution', 'knowledge_retrieval')."""
        ...

    @abstractmethod
    async def execute(self, state: ExecutionState, **kwargs: Any) -> CapabilityResult:
        """Execute the capability and return updated state.

        Args:
            state: Current execution state
            **kwargs: Additional execution context

        Returns:
            CapabilityResult with updated state and execution metadata
        """
        ...

    @abstractmethod
    def can_execute(self, state: ExecutionState) -> bool:
        """Determine if this capability can execute given current state.

        Used by Bellman for capability selection.

        Args:
            state: Current execution state

        Returns:
            True if capability can execute, False otherwise
        """
        ...

    @abstractmethod
    def estimate_reward(self, state: ExecutionState) -> float:
        """Estimate expected reward for executing this capability.

        Used by Bellman for policy evaluation.

        Args:
            state: Current execution state

        Returns:
            Estimated reward (0.0 to 1.0)
        """
        ...

    @abstractmethod
    def estimate_cost(self, state: ExecutionState) -> float:
        """Estimate execution cost for this capability.

        Used by Bellman for cost-benefit analysis.

        Args:
            state: Current execution state

        Returns:
            Estimated cost (0.0 to 1.0, where higher is more expensive)
        """
        ...

    def compute_confidence(self, state: ExecutionState) -> float:
        """Compute confidence that this capability will succeed.

        Args:
            state: Current execution state

        Returns:
            Confidence score (0.0 to 1.0)
        """
        return 0.5  # Default confidence - override in subclasses


class EntityResolutionCapability(ICapability):
    """Base class for entity resolution capabilities."""

    @property
    def capability_type(self) -> str:
        return "entity_resolution"


class KnowledgeRetrievalCapability(ICapability):
    """Base class for knowledge retrieval capabilities."""

    @property
    def capability_type(self) -> str:
        return "knowledge_retrieval"


class AnalysisCapability(ICapability):
    """Base class for analysis capabilities."""

    @property
    def capability_type(self) -> str:
        return "analysis"


class AnswerGenerationCapability(ICapability):
    """Base class for answer generation capabilities."""

    @property
    def capability_type(self) -> str:
        return "answer_generation"
