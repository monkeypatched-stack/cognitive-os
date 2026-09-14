"""Data Routing Middleware — routes entity queries to optimal data sources.

The middleware uses the learned DataRoutingPolicy to select the best
database for each business entity query. Business agents never know
which database was selected.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.monkey_brain.routing.policy import (
    DataRoutingPolicy,
    RoutingCandidate,
    RoutingObservation,
    RoutingEntry,
)
from src.monkey_brain.routing.adapters import (
    RepositoryAdapter,
)

logger = logging.getLogger("agentos.data_routing.middleware")


class DataRoutingMiddleware:
    """Routes entity queries to optimal data sources using learned policy.

    Business agents write:
        customer = context.resolve(Customer, id=customer_id)

    The middleware decides:
        Customer → Routing Policy → CustomerRepository → PostgreSQL

    No business agent should know which database was selected.
    """

    def __init__(self):
        self._policy = DataRoutingPolicy()
        self._adapters: dict[str, RepositoryAdapter] = {}
        self._connected: dict[str, bool] = {}

    async def register_adapter(self, adapter: RepositoryAdapter) -> None:
        """Register a database adapter."""
        self._adapters[adapter.adapter_type] = adapter
        connected = await adapter.connect()
        self._connected[adapter.adapter_type] = connected
        logger.info(
            "[routing] Registered adapter: %s (connected=%s)",
            adapter.adapter_type,
            connected,
        )

    async def resolve(
        self,
        capability: str,
        entity: str,
        entity_id: str | None = None,
        filters: dict | None = None,
    ) -> dict[str, Any]:
        """Resolve an entity query to the best data source.

        Args:
            capability: The business capability (e.g., "customer", "order")
            entity: The entity type (e.g., "Customer", "Order")
            entity_id: Optional specific entity ID
            filters: Optional query filters

        Returns:
            Query results from the selected data source
        """
        candidates = self._discover_candidates(capability)
        self._policy.register_candidates(capability, entity, candidates)

        selected = self._policy.select_source(capability, entity, candidates)
        if selected is None:
            logger.warning("[routing] No candidates for %s:%s", capability, entity)
            return {"results": [], "source": "none", "confidence": 0.0}

        adapter = self._adapters.get(selected.database)
        if adapter is None:
            logger.warning("[routing] Adapter not found for %s", selected.database)
            return {"results": [], "source": selected.database, "confidence": 0.0}

        t0 = time.time()
        try:
            results = await adapter.query(entity, entity_id, filters)
            latency_ms = (time.time() - t0) * 1000

            self._policy.observe(
                RoutingObservation(
                    capability=capability,
                    entity=entity,
                    database=selected.database,
                    repository=selected.repository,
                    latency_ms=latency_ms,
                    confidence=0.9 if results else 0.1,
                    completeness=1.0 if results else 0.0,
                    freshness=1.0,
                    success=bool(results),
                )
            )

            return {
                "results": results,
                "source": selected.database,
                "repository": selected.repository,
                "confidence": 0.9 if results else 0.1,
                "latency_ms": latency_ms,
                "capability": capability,
                "entity": entity,
            }

        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            self._policy.observe(
                RoutingObservation(
                    capability=capability,
                    entity=entity,
                    database=selected.database,
                    repository=selected.repository,
                    latency_ms=latency_ms,
                    confidence=0.0,
                    completeness=0.0,
                    freshness=0.0,
                    success=False,
                )
            )

            self._policy.invalidate(capability, entity, selected.database)

            alternatives = self._policy.revalidate(capability, entity)
            for alt in alternatives:
                if alt.database != selected.database:
                    alt_adapter = self._adapters.get(alt.database)
                    if alt_adapter:
                        try:
                            results = await alt_adapter.query(entity, entity_id, filters)
                            if results:
                                self._policy.observe(
                                    RoutingObservation(
                                        capability=capability,
                                        entity=entity,
                                        database=alt.database,
                                        repository=alt.repository,
                                        latency_ms=(time.time() - t0) * 1000,
                                        confidence=0.8,
                                        completeness=1.0,
                                        freshness=1.0,
                                        success=True,
                                    )
                                )
                                return {
                                    "results": results,
                                    "source": alt.database,
                                    "repository": alt.repository,
                                    "confidence": 0.8,
                                }
                        except Exception:
                            continue

            return {
                "results": [],
                "source": selected.database,
                "confidence": 0.0,
                "error": str(e),
            }

    def _discover_candidates(self, capability: str) -> list[RoutingCandidate]:
        """Discover candidate data sources for a capability."""
        candidates = []
        for adapter_type, adapter in self._adapters.items():
            if capability in adapter.capabilities():
                candidates.append(
                    RoutingCandidate(
                        database=adapter_type,
                        repository=f"{adapter_type}.{capability}",
                        capability=capability,
                        advertised=True,
                        available=self._connected.get(adapter_type, False),
                    )
                )
        return candidates

    def get_routing_table(self) -> list[RoutingEntry]:
        """Return the routing policy table."""
        return self._policy.get_routing_table()

    def get_entry(self, capability: str, entity: str) -> RoutingEntry | None:
        """Get a specific routing entry."""
        return self._policy.get_entry(capability, entity)

    def summary(self) -> dict[str, Any]:
        """Return routing middleware summary."""
        return {
            "adapters": list(self._adapters.keys()),
            "connected": {k: v for k, v in self._connected.items()},
            "policy": self._policy.summary(),
            "routing_table_size": len(self._policy.get_routing_table()),
        }
