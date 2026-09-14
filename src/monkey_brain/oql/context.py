"""OQL Context — the interface business agents use to query data.

Business agents write:
    customer = context.query(entity="Customer", filters={"id": "123"})

The middleware handles everything else.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.oql.query import (
    Query,
    Operation,
    SortClause,
    SortDirection,
    QueryResult,
)
from src.monkey_brain.oql.engine import OQLEngine

logger = logging.getLogger("agentos.oql.context")


class OQLContext:
    """Business agent interface for data access.

    Provides a simple, database-independent API for querying
    and modifying operational data.
    """

    def __init__(self, engine: OQLEngine):
        self._engine = engine

    async def query(
        self,
        entity: str,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        sort: list[tuple[str, str]] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> QueryResult:
        """Query entities.

        Args:
            entity: Entity type (e.g., "Customer", "Order")
            filters: Filter conditions
            fields: Fields to return
            sort: Sort clauses as (field, direction) tuples
            limit: Maximum results
            offset: Skip results

        Returns:
            QueryResult with data
        """
        sort_clauses = []
        if sort:
            for field_name, direction in sort:
                sort_clauses.append(SortClause(field=field_name, direction=SortDirection(direction)))

        query = Query(
            entity=entity,
            operation=Operation.SELECT,
            filters=filters or {},
            fields=fields or [],
            sort=sort_clauses,
            limit=limit,
            offset=offset,
        )
        return await self._engine.execute(query)

    async def insert(self, entity: str, data: dict[str, Any]) -> QueryResult:
        """Insert a new entity."""
        query = Query(
            entity=entity,
            operation=Operation.INSERT,
            data=data,
        )
        return await self._engine.execute(query)

    async def update(self, entity: str, filters: dict[str, Any], data: dict[str, Any]) -> QueryResult:
        """Update entities matching filters."""
        query = Query(
            entity=entity,
            operation=Operation.UPDATE,
            filters=filters,
            data=data,
        )
        return await self._engine.execute(query)

    async def delete(self, entity: str, filters: dict[str, Any]) -> QueryResult:
        """Delete entities matching filters."""
        query = Query(
            entity=entity,
            operation=Operation.DELETE,
            filters=filters,
        )
        return await self._engine.execute(query)

    async def upsert(self, entity: str, data: dict[str, Any], key: str = "id") -> QueryResult:
        """Insert or update an entity."""
        query = Query(
            entity=entity,
            operation=Operation.UPSERT,
            data=data,
            upsert_key=key,
        )
        return await self._engine.execute(query)

    async def count(self, entity: str, filters: dict[str, Any] | None = None) -> int:
        """Count entities matching filters."""
        query = Query(
            entity=entity,
            operation=Operation.COUNT,
            filters=filters or {},
        )
        result = await self._engine.execute(query)
        return result.count if result.success else 0

    async def exists(self, entity: str, filters: dict[str, Any]) -> bool:
        """Check if entities matching filters exist."""
        query = Query(
            entity=entity,
            operation=Operation.EXISTS,
            filters=filters,
        )
        result = await self._engine.execute(query)
        return result.success and result.count > 0

    async def batch_insert(self, entity: str, data: list[dict[str, Any]]) -> QueryResult:
        """Insert multiple entities."""
        query = Query(
            entity=entity,
            operation=Operation.BATCH_INSERT,
            batch_data=data,
        )
        return await self._engine.execute(query)

    async def batch_update(self, entity: str, data: list[dict[str, Any]]) -> QueryResult:
        """Update multiple entities."""
        query = Query(
            entity=entity,
            operation=Operation.BATCH_UPDATE,
            batch_data=data,
        )
        return await self._engine.execute(query)

    async def batch_delete(self, entity: str, data: list[dict[str, Any]]) -> QueryResult:
        """Delete multiple entities."""
        query = Query(
            entity=entity,
            operation=Operation.BATCH_DELETE,
            batch_data=data,
        )
        return await self._engine.execute(query)

    def raw_query(self, query: Query) -> Any:
        """Execute a raw OQL query object."""
        return self._engine.execute(query)
