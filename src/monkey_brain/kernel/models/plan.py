"""Request/response schemas for the /plan endpoint.

/plan is shared across all three runtimes:

    Runtime            | Compile | Run       | Replay
    Cognitive Runtime  | /plan   | /execute  | /replay
    Simulation Runtime | /plan   | /simulate | /replay
    Comparator Runtime | /plan   | /compare  | /replay

`target` selects which runtime compiles (and later runs/replays) the plan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from src.monkey_brain.kernel.models.graph import CanonicalGraph

Target = Literal["execute", "simulate", "compare"]


class PlanRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10_000)
    target: Target = "execute"


class PlanResponse(BaseModel):
    """Plan returns the canonical graph plus plan metadata.

    The graph IS the response. No duplicated graph fields.
    """

    model_config = {"extra": "allow"}

    graph: CanonicalGraph
    run_id: str
    target: Target = "execute"
    question: str
    intent_ir: dict | None = None
    elapsed_ms: float
    # Non-graph plan annotations (profiling, grounding, mesh_reuse hints).
    # Without this declared, pydantic silently DROPPED the metadata kwarg
    # the route has always passed — the mesh-reuse hint was being computed
    # and logged server-side but never reached any caller.
    metadata: dict | None = None

    # Fields that were previously dropped by Pydantic
    workload_id: str = ""
    steps: list[dict] = Field(default_factory=list)
    answer: str = ""
    grounding_confidence: float = 0.0
    low_grounding: bool = False
    user_id: str = ""
    intent: dict | None = None
    graph_id: str | None = None
    graph_type: str | None = None
    timestamp: str | None = None
    nodes: list[dict] | None = None
    edges: list[dict] | None = None
    execution_order: list[list[str]] | None = None
    annotations: dict | None = None
    state: dict | None = None
