"""OQL QueryValidator — validates queries before execution.

Rejects invalid queries before they reach the database layer.
Enforces enterprise data governance rules.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.oql.query import Query, Operation

logger = logging.getLogger("agentos.oql.validator")

SUPPORTED_OPERATIONS = {op.value for op in Operation}

FORBIDDEN_KEYWORDS = frozenset(
    {
        "join",
        "left",
        "right",
        "full",
        "cross",
        "union",
        "intersect",
        "window",
        "cte",
        "recursive",
        "procedure",
        "trigger",
        "view",
        "dynamic_sql",
        "raw_sql",
        "raw_cypher",
        "raw_mongo",
        "aggregate",
        "analytics",
        "reporting",
        "warehouse",
        "olap",
    }
)

MAX_FILTER_DEPTH = 3
MAX_FIELDS = 50
MAX_SORT_CLAUSES = 5
MAX_LIMIT = 10000
MAX_BATCH_SIZE = 1000


class QueryValidationError(Exception):
    """Raised when a query fails validation."""

    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.code = code


class QueryValidator:
    """Validates OQL queries against governance rules."""

    def __init__(self, known_entities: set[str] | None = None):
        self._known_entities = known_entities or set()

    def register_entity(self, entity: str) -> None:
        """Register a known entity type."""
        self._known_entities.add(entity)

    def validate(self, query: Query) -> list[str]:
        """Validate a query and return list of errors. Empty list means valid."""
        errors = []

        if not query.entity:
            errors.append("Entity is required")

        if not query.operation:
            errors.append("Operation is required")
        elif query.operation.value not in SUPPORTED_OPERATIONS:
            errors.append(f"Unsupported operation: {query.operation.value}")

        if query.operation in (Operation.INSERT, Operation.UPDATE, Operation.UPSERT):
            if not query.data:
                errors.append(f"Data is required for {query.operation.value}")

        if query.operation == Operation.BATCH_INSERT:
            if not query.batch_data:
                errors.append("Batch data is required for batch_insert")
            elif len(query.batch_data) > MAX_BATCH_SIZE:
                errors.append(f"Batch size {len(query.batch_data)} exceeds maximum {MAX_BATCH_SIZE}")

        if query.operation == Operation.BATCH_UPDATE:
            if not query.batch_data:
                errors.append("Batch data is required for batch_update")

        if query.operation == Operation.BATCH_DELETE:
            if not query.batch_data:
                errors.append("Batch data is required for batch_delete")

        if len(query.fields) > MAX_FIELDS:
            errors.append(f"Too many fields: {len(query.fields)} > {MAX_FIELDS}")

        if len(query.sort) > MAX_SORT_CLAUSES:
            errors.append(f"Too many sort clauses: {len(query.sort)} > {MAX_SORT_CLAUSES}")

        if query.limit is not None:
            if query.limit < 0:
                errors.append("Limit must be non-negative")
            if query.limit > MAX_LIMIT:
                errors.append(f"Limit {query.limit} exceeds maximum {MAX_LIMIT}")

        if query.offset < 0:
            errors.append("Offset must be non-negative")

        errors.extend(self._validate_filters(query.filters, depth=0))

        return errors

    def _validate_filters(self, filters: dict[str, Any], depth: int) -> list[str]:
        """Validate filter structure recursively."""
        errors = []

        if depth > MAX_FILTER_DEPTH:
            errors.append(f"Filter depth exceeds maximum {MAX_FILTER_DEPTH}")
            return errors

        for key, value in filters.items():
            if not isinstance(key, str):
                errors.append(f"Filter key must be string: {key}")

            if isinstance(value, dict):
                errors.extend(self._validate_filters(value, depth + 1))
            elif isinstance(value, list):
                if len(value) > 100:
                    errors.append(f"Filter list too large: {len(value)} > 100")

        return errors

    def check_forbidden(self, query: Query) -> list[str]:
        """Check for forbidden patterns in query."""
        errors = []
        text = str(query.to_dict()).lower()
        for keyword in FORBIDDEN_KEYWORDS:
            if keyword in text:
                errors.append(f"Forbidden keyword detected: {keyword}")
        return errors

    def validate_and_reject(self, query: Query) -> None:
        """Validate and raise if invalid."""
        errors = self.validate(query)
        forbidden = self.check_forbidden(query)
        all_errors = errors + forbidden

        if all_errors:
            raise QueryValidationError(
                f"Query validation failed: {'; '.join(all_errors)}",
                code="query_validation_failed",
            )
