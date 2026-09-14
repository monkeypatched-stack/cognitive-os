"""OQL Query — strongly typed query object for operational data access.

Every OQL query targets exactly one entity and exactly one repository.
Business agents never generate SQL, Cypher, or Mongo queries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operation(str, Enum):
    """Supported OQL operations."""

    SELECT = "select"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"
    COUNT = "count"
    EXISTS = "exists"
    BATCH_INSERT = "batch_insert"
    BATCH_UPDATE = "batch_update"
    BATCH_DELETE = "batch_delete"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


@dataclass
class SortClause:
    """Sort specification for a query."""

    field: str
    direction: SortDirection = SortDirection.ASC

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "direction": self.direction.value}


@dataclass
class Query:
    """OQL query object — the canonical way to access operational data.

    Every query targets exactly one entity. The middleware resolves
    the entity to a repository and database automatically.

    Business agents write:
        query = Query(entity="Customer", operation="select", filters={"id": "123"})

    The middleware compiles this into the native database query.
    """

    entity: str
    operation: Operation
    filters: dict[str, Any] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)
    sort: list[SortClause] = field(default_factory=list)
    limit: int | None = None
    offset: int = 0
    data: dict[str, Any] | None = None
    batch_data: list[dict[str, Any]] | None = None
    upsert_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    query_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "entity": self.entity,
            "operation": self.operation.value,
            "filters": self.filters,
            "fields": self.fields,
            "sort": [s.to_dict() for s in self.sort],
            "limit": self.limit,
            "offset": self.offset,
            "data": self.data,
            "batch_data": self.batch_data,
            "upsert_key": self.upsert_key,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Query:
        sort_clauses = []
        for s in data.get("sort", []):
            if isinstance(s, dict):
                sort_clauses.append(
                    SortClause(
                        field=s.get("field", ""),
                        direction=SortDirection(s.get("direction", "asc")),
                    )
                )
            elif isinstance(s, (list, tuple)) and len(s) == 2:
                sort_clauses.append(SortClause(field=s[0], direction=SortDirection(s[1])))

        return cls(
            entity=data.get("entity", ""),
            operation=Operation(data.get("operation", "select")),
            filters=data.get("filters", {}),
            fields=data.get("fields", []),
            sort=sort_clauses,
            limit=data.get("limit"),
            offset=data.get("offset", 0),
            data=data.get("data"),
            batch_data=data.get("batch_data"),
            upsert_key=data.get("upsert_key"),
            metadata=data.get("metadata", {}),
            query_id=data.get("query_id", ""),
            timestamp=data.get("timestamp", time.time()),
        )


@dataclass
class QueryResult:
    """Result of an OQL query execution."""

    success: bool
    data: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0
    affected: int = 0
    entity: str = ""
    database: str = ""
    repository: str = ""
    latency_ms: float = 0.0
    query_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "count": self.count,
            "affected": self.affected,
            "entity": self.entity,
            "database": self.database,
            "repository": self.repository,
            "latency_ms": round(self.latency_ms, 2),
            "query_id": self.query_id,
            "metadata": self.metadata,
        }
