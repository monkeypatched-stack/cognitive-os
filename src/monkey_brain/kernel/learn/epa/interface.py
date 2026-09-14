"""Learning Loop Interface — abstraction for reinforcement learning loops.

This interface defines the contract for all learning loop implementations,
enabling different RL algorithms (Bellman, Q-Learning, Actor-Critic, etc.)
to be used interchangeably.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ILearningLoop(Protocol):
    """Interface for reinforcement learning loops."""

    @abstractmethod
    async def calculate_embedding_loss(self, query_state: Dict[str, Any], simulation_state: Dict[str, Any]) -> float:
        """Calculate loss between query and simulation state embeddings."""
        ...

    @abstractmethod
    async def run_query_and_simulation(self, question: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Run query and simulation execution in parallel."""
        ...

    @abstractmethod
    async def update_policy(
        self,
        query_state: Dict[str, Any],
        simulation_state: Dict[str, Any],
        pipeline_id: str,
        profiling_data: Dict[str, float] | None = None,
        question: str = "",
    ) -> None:
        """Update policy based on embedding loss and execution results."""
        ...

    @abstractmethod
    async def complete_learning_loop(self, question: str) -> Dict[str, Any]:
        """Complete reinforcement learning loop for a question."""
        ...


class LearningLoopConfig:
    """Configuration for learning loop implementations."""

    def __init__(
        self,
        embedding_dimension: int = 10,
        exploration_rate: float = 0.1,
        learning_rate: float = 0.1,
        discount_factor: float = 0.95,
        max_embedding_features: int = 8,
    ):
        self.embedding_dimension = embedding_dimension
        self.exploration_rate = exploration_rate
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.max_embedding_features = max_embedding_features
