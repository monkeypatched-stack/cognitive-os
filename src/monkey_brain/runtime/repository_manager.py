"""Repository Manager — commits operations to repositories.

The runtime calls only:
    repository_manager.commit(result.operations)

The runtime never manipulates repositories directly.
Repositories own persistence, CRUD, transactions, consistency.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.monkey_brain.runtime.repository_operations import (
    RepositoryOperation,
    OperationType,
)

logger = logging.getLogger("agentos.repository_manager")

# Default state directory
STATE_DIR = Path.home() / ".monkeybrain" / "world_state"


class RepositoryManager:
    """Commits operations to repositories.

    Repositories own:
    - persistence
    - CRUD
    - transactions
    - consistency
    - optimistic locking
    - versioning

    Capabilities interact with repositories through interfaces.
    The runtime never accesses repository internals.
    """

    def __init__(self, state_dir: Path | None = None):
        self._state_dir = state_dir or STATE_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._repositories: dict[str, dict[str, Any]] = {}

    def commit(self, operations: list[RepositoryOperation]) -> list[dict[str, Any]]:
        """Commit a batch of operations to repositories.

        Returns list of results for each operation.
        """
        results = []
        for op in operations:
            try:
                result = self._commit_single(op)
                results.append({"success": True, "operation": op.to_dict(), "result": result})
            except Exception as e:
                logger.error("[repo] Failed to commit operation: %s", e)
                results.append({"success": False, "operation": op.to_dict(), "error": str(e)})
        return results

    def _commit_single(self, op: RepositoryOperation) -> dict[str, Any]:
        """Commit a single operation."""
        repo = self._get_repository(op.repository)

        if op.operation == OperationType.CREATE:
            return self._create(repo, op)
        elif op.operation == OperationType.UPDATE:
            return self._update(repo, op)
        elif op.operation == OperationType.DELETE:
            return self._delete(repo, op)
        elif op.operation == OperationType.READ:
            return self._read(repo, op)
        else:
            raise ValueError(f"Unknown operation type: {op.operation}")

    def _get_repository(self, name: str) -> dict[str, Any]:
        """Get or create a repository."""
        if name not in self._repositories:
            self._repositories[name] = self._load_repository(name)
        return self._repositories[name]

    def _load_repository(self, name: str) -> dict[str, Any]:
        """Load repository from disk."""
        path = self._state_dir / f"{name}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {"entities": {}}
        return {"entities": {}}

    def _save_repository(self, name: str, repo: dict[str, Any]) -> None:
        """Save repository to disk."""
        path = self._state_dir / f"{name}.json"
        try:
            path.write_text(json.dumps(repo, indent=2, default=str))
        except Exception as e:
            logger.warning("[repo] Failed to save %s: %s", name, e)

    def _create(self, repo: dict[str, Any], op: RepositoryOperation) -> dict[str, Any]:
        """Create a new entity."""
        entity_id = op.entity_id or f"entity_{int(time.time() * 1000)}"
        entity = {
            "id": entity_id,
            **op.payload,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        repo["entities"][entity_id] = entity
        self._save_repository(op.repository, repo)
        return entity

    def _update(self, repo: dict[str, Any], op: RepositoryOperation) -> dict[str, Any]:
        """Update an existing entity."""
        if op.entity_id not in repo["entities"]:
            raise KeyError(f"Entity {op.entity_id} not found in {op.repository}")
        entity = repo["entities"][op.entity_id]
        entity.update(op.payload)
        entity["updated_at"] = time.time()
        self._save_repository(op.repository, repo)
        return entity

    def _delete(self, repo: dict[str, Any], op: RepositoryOperation) -> dict[str, Any]:
        """Delete an entity."""
        if op.entity_id not in repo["entities"]:
            raise KeyError(f"Entity {op.entity_id} not found in {op.repository}")
        entity = repo["entities"].pop(op.entity_id)
        self._save_repository(op.repository, repo)
        return entity

    def _read(self, repo: dict[str, Any], op: RepositoryOperation) -> dict[str, Any]:
        """Read an entity."""
        if op.entity_id:
            return repo["entities"].get(op.entity_id, {})
        return {"entities": list(repo["entities"].values())}

    def list_repositories(self) -> list[str]:
        """List all repositories."""
        return list(self._repositories.keys())

    def get_entity(self, repository: str, entity_id: str) -> dict[str, Any] | None:
        """Get an entity from a repository."""
        repo = self._get_repository(repository)
        return repo["entities"].get(entity_id)

    def list_entities(self, repository: str) -> list[dict[str, Any]]:
        """List all entities in a repository."""
        repo = self._get_repository(repository)
        return list(repo["entities"].values())
