"""State Models — strongly-typed data structures for RL states.

Replaces generic Dict[str, Any] with proper data classes for better type safety,
IDE support, and maintainability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionProfiling:
    """Profiling data for pipeline execution."""

    total_latency: float = 0.0
    state_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    step_execution_times: List[float] = field(default_factory=list)
    memory_usage: Optional[float] = None
    cpu_usage: Optional[float] = None


@dataclass
class ExecutionDetails:
    """Detailed execution information."""

    profiling: ExecutionProfiling = field(default_factory=ExecutionProfiling)
    pipeline_steps_executed: int = 0
    agents_discovered: int = 0
    capabilities_resolved: int = 0
    errors_encountered: List[str] = field(default_factory=list)


@dataclass
class RLState:
    """Reinforcement learning state representation."""

    answer: str = ""
    semantic_hits: List[str] = field(default_factory=list)
    graph_paths: List[str] = field(default_factory=list)
    llm_answered: bool = False
    intent: str = "unknown"
    execution_details: ExecutionDetails = field(default_factory=ExecutionDetails)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for compatibility."""
        return {
            "answer": self.answer,
            "semantic_hits": self.semantic_hits,
            "graph_paths": self.graph_paths,
            "llm_answered": self.llm_answered,
            "intent": self.intent,
            "execution_details": {
                "profiling": {
                    "total_latency": self.execution_details.profiling.total_latency,
                    "state_snapshots": self.execution_details.profiling.state_snapshots,
                    "step_execution_times": self.execution_details.profiling.step_execution_times,
                    "memory_usage": self.execution_details.profiling.memory_usage,
                    "cpu_usage": self.execution_details.profiling.cpu_usage,
                },
                "pipeline_steps_executed": self.execution_details.pipeline_steps_executed,
                "agents_discovered": self.execution_details.agents_discovered,
                "capabilities_resolved": self.execution_details.capabilities_resolved,
                "errors_encountered": self.execution_details.errors_encountered,
            },
            **self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RLState:
        """Create state from dictionary."""
        execution_details_data = data.get("execution_details", {})
        profiling_data = execution_details_data.get("profiling", {})

        return cls(
            answer=data.get("answer", ""),
            semantic_hits=data.get("semantic_hits", []),
            graph_paths=data.get("graph_paths", []),
            llm_answered=data.get("llm_answered", False),
            intent=data.get("intent", "unknown"),
            execution_details=ExecutionDetails(
                profiling=ExecutionProfiling(
                    total_latency=profiling_data.get("total_latency", 0.0),
                    state_snapshots=profiling_data.get("state_snapshots", []),
                    step_execution_times=profiling_data.get("step_execution_times", []),
                    memory_usage=profiling_data.get("memory_usage"),
                    cpu_usage=profiling_data.get("cpu_usage"),
                ),
                pipeline_steps_executed=execution_details_data.get("pipeline_steps_executed", 0),
                agents_discovered=execution_details_data.get("agents_discovered", 0),
                capabilities_resolved=execution_details_data.get("capabilities_resolved", 0),
                errors_encountered=execution_details_data.get("errors_encountered", []),
            ),
            metadata={
                k: v
                for k, v in data.items()
                if k
                not in [
                    "answer",
                    "semantic_hits",
                    "graph_paths",
                    "llm_answered",
                    "intent",
                    "execution_details",
                ]
            },
        )


@dataclass
class LearningLoopResult:
    """Result of a complete learning loop execution."""

    query_state: RLState
    simulation_state: RLState
    loss: float
    success: bool
    profiling: Dict[str, float]
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfilingMetrics:
    """Profiling metrics for learning loop execution."""

    total_time: float = 0.0
    query_time: float = 0.0
    simulation_time: float = 0.0
    embedding_time: float = 0.0
    policy_update_time: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {
            "total_time": self.total_time,
            "query_time": self.query_time,
            "simulation_time": self.simulation_time,
            "embedding_time": self.embedding_time,
            "policy_update_time": self.policy_update_time,
        }
