"""Request/response schemas for the /execute endpoint."""

from __future__ import annotations


from pydantic import BaseModel, Field
from src.monkey_brain.kernel.models.graph import CanonicalGraph


class ExecuteRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10_000)


class ExecuteResponse(BaseModel):
    """Execute returns the canonical graph.

    The graph IS the response. No duplicated fields.
    """

    graph: CanonicalGraph
    answer: str
    elapsed_ms: float
    semantic_hits: list
    graph_paths: list
    citations: list
    llm_answered: bool
    # Explicit outcome. Previously a run whose step FAILED (and could not be compensated)
    # still returned 200 with llm_answered=true and the failure only visible as "[failed]"
    # inside the prose answer — a client could not detect it. Defaults keep old clients working.
    success: bool = True
    failed_steps: list = Field(default_factory=list)
    # Is anything actually BEHIND the answer? An LLM answer with zero retrieval hits, zero
    # graph paths and zero cited sources is the model's prior stated as fact — /execute was
    # returning exactly that, fluently and with invented specifics. `llm_answered` says an LLM
    # spoke; `grounded` says whether it had anything to speak from.
    grounded: bool = True
    grounding: dict = Field(default_factory=dict)
    metadata: dict
    run_id: str = ""
    question: str = ""
    graph_id: str = ""
    graph_type: str = ""
    timestamp: str = ""
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    execution_order: list[list[str]] = Field(default_factory=list)
    annotations: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)
