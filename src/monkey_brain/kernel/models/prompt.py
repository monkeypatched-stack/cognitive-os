"""Prompt endpoint schemas and log capture."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

RunType = Literal["full", "healing", "stability", "codegen"]

_EXCLUDED_LOG_PATTERNS = [
    "unauthenticated",
    "HF_TOKEN",
    "rate limit",
    "huggingface",
    "credit balance",
    "LLM generation failed",
]


class PromptRequest(BaseModel):
    question: str = Field(None, min_length=1, max_length=10_000)
    prompt: str = Field(None, min_length=1, max_length=10_000)  # Alternative field name
    context: dict[str, Any] | None = None
    run_query: bool = True
    run_simulate: bool = True
    run_type: RunType = "full"
    meta: dict[str, Any] | None = None  # overrides: meta.run_type, meta.max_healing_attempts,
    # meta.propagation_mode ("SYNCHRONOUS"|"ASYNCHRONOUS", default SYNCHRONOUS),
    # meta.propagation_scope ("POINT_TO_POINT"|"BROADCAST", default BROADCAST),
    # meta.propagation_target_actor_id (required when propagation_scope="POINT_TO_POINT")

    def model_post_init(self, __context: Any) -> None:
        """Support both 'question' and 'prompt' fields."""
        if self.question is None and self.prompt is not None:
            self.question = self.prompt
        elif self.question is None and self.prompt is None:
            raise ValueError("Either 'question' or 'prompt' must be provided")


class PromptResponse(BaseModel):
    question: str
    query_result: dict[str, Any] | None = None
    simulation_result: Any = None
    business_flow: dict[str, Any] | None = None
    execution_summary: dict[str, Any]
    workload_result: dict[str, Any] | None = None
    error_lines: list[str] = []


class _AgentOSFilter(logging.Filter):
    """Accept only records from the agentos namespace to avoid cross-request contamination."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("agentos")


class _RequestErrorCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.addFilter(_AgentOSFilter())
        self.lines: list[str] = []

    # TODO: the errors are not not being captured anywhere so we need to send the logs to elastic search
    # to capture the errors and then we can use that to send the errors to the user.

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if not any(p in msg for p in _EXCLUDED_LOG_PATTERNS):
            self.lines.append(f"[{record.name}] {msg}")
