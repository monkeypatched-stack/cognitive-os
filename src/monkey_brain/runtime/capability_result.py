"""CapabilityResult — standard return type for all capabilities.

Every capability returns a CapabilityResult. The runtime only understands
this interface. It must never inspect domain-specific fields.

The runtime never branches based on:
- agent type
- capability name
- provider
- domain object
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Response:
    """Structured response from a capability."""

    answer: str
    format: str = "text"  # text, markdown, json, html
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Artifact:
    """An artifact produced by a capability."""

    kind: str  # file, pr, branch, commit, report
    name: str
    uri: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """An event emitted by a capability."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass
class CapabilityResult:
    """Standard result from capability execution.

    The runtime only understands this interface.
    It must never inspect domain-specific fields.
    """

    success: bool
    response: Response
    operations: list["RepositoryOperation"] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "response": {
                "answer": self.response.answer,
                "format": self.response.format,
                "metadata": self.response.metadata,
            },
            "operations": [op.to_dict() for op in self.operations],
            "artifacts": [{"kind": a.kind, "name": a.name, "uri": a.uri} for a in self.artifacts],
            "events": [{"event_type": e.event_type, "payload": e.payload} for e in self.events],
            "metadata": self.metadata,
        }


# Forward reference for RepositoryOperation
from src.monkey_brain.runtime.repository_operations import RepositoryOperation
