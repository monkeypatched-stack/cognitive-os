"""Query endpoint schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10_000)


class QueryResponse(BaseModel):
    question: str
    answer: str
    semantic_hits: list
    graph_paths: list
    citations: list
    llm_answered: bool
    user_id: str
    metadata: dict
