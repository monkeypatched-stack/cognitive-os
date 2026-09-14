"""Response Schemas — standardized response objects for all agents.

Every capability returns structured objects. Never return:
- JSON inside strings
- Markdown wrapped JSON
- Double serialization
- Escaped objects

Transport serialization belongs to the runtime.
Business objects belong to the capability.
Presentation formatting belongs to the interface layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    PENDING = "pending"


@dataclass
class AgentResponse:
    """Standardized response from any agent execution."""

    answer: str
    success: bool = True
    status: ResponseStatus = ResponseStatus.SUCCESS
    intent: str = ""
    knowledge_sources: list[str] = field(default_factory=list)
    grounding_confidence: float = 0.0
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "answer": self.answer,
            "success": self.success,
            "status": self.status.value,
            "intent": self.intent,
            "knowledge_sources": self.knowledge_sources,
            "grounding_confidence": self.grounding_confidence,
            "state": self.state,
            "metadata": self.metadata,
        }


@dataclass
class TaskResponse(AgentResponse):
    """Response for task-related agents."""

    tasks: list[dict[str, Any]] = field(default_factory=list)
    task_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["tasks"] = self.tasks
        base["task_count"] = self.task_count
        return base


@dataclass
class BlogResponse(AgentResponse):
    """Response for blog/writing agents."""

    article: str = ""
    word_count: int = 0
    sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base["article"] = self.article
        base["word_count"] = self.word_count
        base["sections"] = self.sections
        return base


@dataclass
class ErrorResponse(AgentResponse):
    """Standardized error response."""

    error_code: str = ""
    error_detail: str = ""

    def __init__(self, error: str, error_code: str = "", **kwargs):
        super().__init__(
            answer=f"Error: {error}",
            success=False,
            status=ResponseStatus.FAILED,
            **kwargs,
        )
        self.error_code = error_code
        self.error_detail = error


def create_response(
    agent_name: str,
    answer: str,
    success: bool = True,
    intent: str = "",
    knowledge_sources: list[str] | None = None,
    grounding_confidence: float = 0.0,
    **kwargs,
) -> AgentResponse:
    """Factory function to create standardized responses."""
    if "task" in agent_name.lower():
        return TaskResponse(
            answer=answer,
            success=success,
            intent=intent,
            knowledge_sources=knowledge_sources or [],
            grounding_confidence=grounding_confidence,
            **kwargs,
        )
    if "blog" in agent_name.lower() or "writer" in agent_name.lower():
        return BlogResponse(
            answer=answer,
            article=answer,
            word_count=len(answer.split()),
            success=success,
            intent=intent,
            knowledge_sources=knowledge_sources or [],
            grounding_confidence=grounding_confidence,
            **kwargs,
        )
    return AgentResponse(
        answer=answer,
        success=success,
        intent=intent,
        knowledge_sources=knowledge_sources or [],
        grounding_confidence=grounding_confidence,
        **kwargs,
    )
