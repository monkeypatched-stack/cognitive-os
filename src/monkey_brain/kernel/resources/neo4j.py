"""Neo4jResource — health-state wrapper around GraphStore.

GraphStore.connect() is lazy (AsyncGraphDatabase.driver() doesn't verify
auth/reachability until the first real query — this session's own
debugging found the "Unauthorized" failure only surfaced later, during
sync_graph()) — this wrapper calls the driver's own verify_connectivity()
to get a real answer instead of trusting is_connected() alone. Optional:
never blocks boot, matches GraphStore's existing log-and-continue design.
"""

from __future__ import annotations

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class Neo4jResource:
    def __init__(self, store) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "neo4j"

    @property
    def config(self) -> ResourceConfig:
        # No config_keys: GraphStore already defaults to bolt://localhost:7687.
        return ResourceConfig(name="neo4j", required=False)

    async def initialize(self) -> ResourceHealth:
        return await self._check()

    async def health(self) -> ResourceHealth:
        return await self._check()

    async def shutdown(self) -> None:
        await self._store.close()

    async def _check(self) -> ResourceHealth:
        if not self._store.is_connected():
            try:
                await self._store.connect()
            except Exception as exc:
                return ResourceHealth(
                    name=self.name,
                    state=ResourceState.FAILED,
                    reason=str(exc)[:200],
                    category=ErrorCategory.INTERNAL,
                    required=False,
                )

        driver = getattr(self._store, "_driver", None)
        if driver is None:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason="neo4j driver not installed or connect failed",
                category=ErrorCategory.DEPENDENCY_MISSING,
                required=False,
            )

        try:
            await driver.verify_connectivity()
            return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
        except Exception as exc:
            msg = str(exc).lower()
            category = (
                ErrorCategory.AUTHENTICATION if ("unauthorized" in msg or "auth" in msg) else ErrorCategory.NETWORK
            )
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason=str(exc)[:200],
                category=category,
                required=False,
            )
