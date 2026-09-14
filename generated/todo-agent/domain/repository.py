from abc import ABC, abstractmethod
from typing import List, Optional


class TodoAgentItem:
    pass  # Assuming this is defined in domain/entities.py


class TodoAgent:
    pass  # Assuming this is defined in domain/aggregates.py


class TodoAgentRepository(ABC):
    @abstractmethod
    async def get(self, id: str) -> Optional[TodoAgent]:
        """Retrieve a TodoAgent by its ID."""

    @abstractmethod
    async def add(self, todo_agent: TodoAgent) -> None:
        """Add a new TodoAgent to the repository."""

    @abstractmethod
    async def update(self, todo_agent: TodoAgent) -> None:
        """Update an existing TodoAgent in the repository."""

    @abstractmethod
    async def delete(self, id: str) -> None:
        """Delete a TodoAgent from the repository."""


class RepositoryException(Exception):
    pass


class RepositoryConflict(RepositoryException):
    pass


class RepositoryNotFound(RepositoryException):
    pass
