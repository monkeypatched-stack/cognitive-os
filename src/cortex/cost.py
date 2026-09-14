"""Cost Engine — estimates execution cost.

Cost includes:
- CPU
- Memory
- Storage
- Network
- Database
- Token Usage
- LLM Cost
- External API Cost
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostEstimate:
    """Estimated cost of an execution."""

    total_cost: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class CostEngine:
    """Estimates execution cost.

    Optimization considers both correctness and cost.
    """

    def __init__(self):
        self._cost_rates: dict[str, float] = {
            "cpu_per_ms": 0.0001,
            "memory_per_mb": 0.001,
            "storage_per_mb": 0.0005,
            "network_per_kb": 0.0001,
            "database_per_query": 0.001,
            "token_per_1k": 0.002,
            "llm_per_call": 0.01,
            "api_per_call": 0.005,
        }

    def estimate(
        self,
        latency_ms: float = 0.0,
        memory_mb: float = 0.0,
        database_queries: int = 0,
        tokens: int = 0,
        llm_calls: int = 0,
        api_calls: int = 0,
    ) -> CostEstimate:
        """Estimate execution cost."""
        components = {}

        components["cpu"] = latency_ms * self._cost_rates["cpu_per_ms"]
        components["memory"] = memory_mb * self._cost_rates["memory_per_mb"]
        components["database"] = database_queries * self._cost_rates["database_per_query"]
        components["tokens"] = (tokens / 1000) * self._cost_rates["token_per_1k"]
        components["llm"] = llm_calls * self._cost_rates["llm_per_call"]
        components["api"] = api_calls * self._cost_rates["api_per_call"]

        total = sum(components.values())

        return CostEstimate(
            total_cost=total,
            components=components,
        )
