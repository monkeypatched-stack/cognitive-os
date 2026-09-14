"""OQL Engine — executes compiled queries against database adapters.

The engine orchestrates validation, compilation, routing, and execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.monkey_brain.oql.query import Query, QueryResult
from src.monkey_brain.oql.validator import QueryValidator, QueryValidationError
from src.monkey_brain.oql.compiler import QueryCompiler, NativeQuery

logger = logging.getLogger("agentos.oql.engine")


class OQLEngine:
    """Executes OQL queries against database adapters."""

    def __init__(self, routing_middleware: Any = None):
        self._validator = QueryValidator()
        self._compiler = QueryCompiler()
        self._routing = routing_middleware
        self._query_count = 0
        self._total_latency_ms = 0.0

    def register_entity(self, entity: str) -> None:
        """Register a known entity type."""
        self._validator.register_entity(entity)

    async def execute(self, query: Query) -> QueryResult:
        """Execute an OQL query.

        Pipeline: validate → compile → route → execute → learn
        """
        t0 = time.time()
        self._query_count += 1
        query.query_id = f"q-{self._query_count}"

        # 1. Validate
        try:
            self._validator.validate_and_reject(query)
        except QueryValidationError as e:
            return QueryResult(
                success=False,
                entity=query.entity,
                latency_ms=(time.time() - t0) * 1000,
                query_id=query.query_id,
                metadata={"error": str(e), "code": e.code},
            )

        # 2. Route to database
        if self._routing:
            resolution = await self._routing.resolve(
                capability=query.entity.lower(),
                entity=query.entity,
            )
            database = resolution.get("source", "unknown")
            repository = resolution.get("repository", f"{database}.{query.entity.lower()}")
        else:
            database = "unknown"
            repository = f"unknown.{query.entity.lower()}"

        # 3. Compile
        native_query = self._compiler.compile(query, database)

        # 4. Execute (placeholder — actual execution depends on adapter)
        try:
            result_data = await self._execute_native(native_query, query)
            latency_ms = (time.time() - t0) * 1000
            self._total_latency_ms += latency_ms

            # 5. Learn from result
            if self._routing:
                from src.monkey_brain.routing.policy import RoutingObservation

                self._routing._policy.observe(
                    RoutingObservation(
                        capability=query.entity.lower(),
                        entity=query.entity,
                        database=database,
                        repository=repository,
                        latency_ms=latency_ms,
                        confidence=0.9 if result_data else 0.1,
                        completeness=1.0 if result_data else 0.0,
                        freshness=1.0,
                        success=True,
                    )
                )

            return QueryResult(
                success=True,
                data=(result_data if isinstance(result_data, list) else [result_data] if result_data else []),
                count=(len(result_data) if isinstance(result_data, list) else 1 if result_data else 0),
                entity=query.entity,
                database=database,
                repository=repository,
                latency_ms=latency_ms,
                query_id=query.query_id,
                metadata={
                    "operation": query.operation.value,
                    "native_query_type": native_query.query_type,
                },
            )

        except Exception as e:
            latency_ms = (time.time() - t0) * 1000

            if self._routing:
                from src.monkey_brain.routing.policy import RoutingObservation

                self._routing._policy.observe(
                    RoutingObservation(
                        capability=query.entity.lower(),
                        entity=query.entity,
                        database=database,
                        repository=repository,
                        latency_ms=latency_ms,
                        confidence=0.0,
                        completeness=0.0,
                        freshness=0.0,
                        success=False,
                    )
                )

            return QueryResult(
                success=False,
                entity=query.entity,
                database=database,
                repository=repository,
                latency_ms=latency_ms,
                query_id=query.query_id,
                metadata={"error": str(e)},
            )

    async def _execute_native(self, native_query: NativeQuery, query: Query) -> Any:
        """Execute a native query against the database adapter."""
        if not self._routing or not self._routing._adapters:
            return []

        adapter = self._routing._adapters.get(native_query.database)
        if adapter is None:
            raise RuntimeError(f"No adapter for database: {native_query.database}")

        return await adapter.query(
            entity=query.entity,
            entity_id=query.filters.get("id") or query.filters.get("entity_id"),
            filters=query.filters,
        )

    def stats(self) -> dict[str, Any]:
        """Return engine statistics."""
        return {
            "total_queries": self._query_count,
            "total_latency_ms": round(self._total_latency_ms, 2),
            "avg_latency_ms": round(self._total_latency_ms / max(1, self._query_count), 2),
        }
