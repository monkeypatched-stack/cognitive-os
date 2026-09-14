"""Repository Operations — capabilities return operations, not mutations.

Capabilities should never mutate runtime state directly.
Instead they return operations that the runtime commits.

The runtime simply commits the operations.
It does not understand their contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class OperationType(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class RepositoryOperation:
    """A single operation to be committed to a repository.

    The runtime commits these without understanding their contents.
    """

    repository: str
    operation: OperationType
    payload: dict[str, Any] = field(default_factory=dict)
    entity_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "operation": self.operation.value,
            "payload": self.payload,
            "entity_id": self.entity_id,
            "metadata": self.metadata,
        }


@dataclass
class CreateOperation(RepositoryOperation):
    """Create operation."""

    def __init__(self, repository: str, payload: dict[str, Any], **kwargs):
        super().__init__(
            repository=repository,
            operation=OperationType.CREATE,
            payload=payload,
            **kwargs,
        )


@dataclass
class UpdateOperation(RepositoryOperation):
    """Update operation."""

    def __init__(self, repository: str, entity_id: str, payload: dict[str, Any], **kwargs):
        super().__init__(
            repository=repository,
            operation=OperationType.UPDATE,
            entity_id=entity_id,
            payload=payload,
            **kwargs,
        )


@dataclass
class DeleteOperation(RepositoryOperation):
    """Delete operation."""

    def __init__(self, repository: str, entity_id: str, **kwargs):
        super().__init__(
            repository=repository,
            operation=OperationType.DELETE,
            entity_id=entity_id,
            **kwargs,
        )
