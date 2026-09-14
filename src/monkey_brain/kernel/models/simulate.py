"""Simulation endpoint schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from src.monkey_brain.kernel.models.graph import CanonicalGraph


class SimulateRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Free-text prompt to simulate")
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional context dict passed through to the simulator",
    )


class SimulateResponse(BaseModel):
    """Simulate returns the canonical graph plus simulation metadata.

    The graph IS the response. No duplicated graph fields.
    """

    graph: CanonicalGraph
    run_id: str
    target: str = "simulate"
    question: str
    answer: str = ""
    simulation_id: str = ""
    workload_id: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    grounding_confidence: float = 0.0
    grounding_score: float = 0.0
    low_grounding: bool = False
    needs_correction: bool = False
    user_id: str = ""
    elapsed_ms: float
    metadata: dict
    intent: Any = None
    context: Any = None
    intent_ir: dict | None = None
    graph_id: str = ""
    graph_type: str = ""
    timestamp: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    execution_order: list[list[str]] = Field(default_factory=list)
    annotations: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class CompareRequest(BaseModel):
    question: str = Field(..., min_length=1)
    context: dict[str, Any] | None = None


class CompareResponse(BaseModel):
    """Compare returns the canonical graph plus comparison results.

    The graph IS the response. No duplicated graph fields.
    """

    graph: CanonicalGraph
    question: str
    # Without this the response carried no way to correlate the comparison
    # back to the plan/execution it came from.
    run_id: str = ""
    comparison: dict[str, Any] | None = None


class TransitionValuesResponse(BaseModel):
    transitions: list[dict[str, Any]]
    action_values: dict[str, Any]


class BellmanCycleResponse(BaseModel):
    question: str
    workload_name: str
    simulate: dict[str, Any]
    query_answer_preview: str
    loss: dict[str, Any]
    transition: dict[str, Any]
    bellman_values: dict[str, Any]
