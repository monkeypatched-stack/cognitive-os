"""RedisResource — health-state wrapper around RedisAdapter.

Same lazy-connect issue as MongoDBAdapter: from_url() never verifies
reachability, so this wrapper does a real PING. Required: boot aborts if
this never comes up.

UNAVAILABLE vs FAILED: same gap as MongoResource (kernel/resources/
mongo.py — see its docstring for the full mechanism), fixed the same way.
ResourceManager._try_initialize() retries FAILED and UNAVAILABLE
identically, but ResourceManager.initialize_all() only raises for a
required resource that ends up FAILED after retries are exhausted — never
UNAVAILABLE. Since RedisResource is always required=True with no optional
path, a missing driver or an unreachable/unauthenticated server are exactly
as terminal as a config exception here, so both branches below use FAILED
instead so the required-resource contract actually fires.
"""

from __future__ import annotations

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class RedisResource:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "redis"

    @property
    def config(self) -> ResourceConfig:
        # No config_keys: same reasoning as MongoResource — RedisAdapter
        # already defaults to redis://localhost:6379 when unset.
        return ResourceConfig(name="redis", required=True)

    async def initialize(self) -> ResourceHealth:
        return await self._ping()

    async def health(self) -> ResourceHealth:
        return await self._ping()

    async def shutdown(self) -> None:
        await self._adapter.disconnect()

    async def _ping(self) -> ResourceHealth:
        client = getattr(self._adapter, "_client", None)
        if client is None:
            try:
                await self._adapter.connect()
            except Exception as exc:
                return ResourceHealth(
                    name=self.name,
                    state=ResourceState.FAILED,
                    reason=str(exc)[:200],
                    category=ErrorCategory.INTERNAL,
                    required=True,
                )
            client = getattr(self._adapter, "_client", None)

        if client is None:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.FAILED,
                reason="Redis client not constructed — redis package not installed?",
                category=ErrorCategory.DEPENDENCY_MISSING,
                required=True,
            )

        try:
            await client.ping()
            return ResourceHealth(name=self.name, state=ResourceState.READY, required=True)
        except Exception as exc:
            msg = str(exc).lower()
            category = ErrorCategory.AUTHENTICATION if ("auth" in msg or "noauth" in msg) else ErrorCategory.NETWORK
            return ResourceHealth(
                name=self.name,
                state=ResourceState.FAILED,
                reason=str(exc)[:200],
                category=category,
                required=True,
            )
