"""Redis Capability — key-value store integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class RedisCapability(Capability):
    """Redis integration capability."""

    def __init__(self, url: str = "redis://localhost:6379"):
        super().__init__(name="redis")
        self._url = url
        self._client = None

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute Redis operation."""
        import redis.asyncio as aioredis

        if not self._client:
            self._client = aioredis.from_url(self._url, decode_responses=True)

        operation = state.get("operation", "get")
        key = state.get("key", "")
        value = state.get("value")

        if operation == "get":
            result = await self._client.get(key)
            return {"value": result}

        elif operation == "set":
            await self._client.set(key, value)
            return {"status": "ok"}

        elif operation == "delete":
            await self._client.delete(key)
            return {"status": "deleted"}

        elif operation == "exists":
            result = await self._client.exists(key)
            return {"exists": bool(result)}

        return {"status": "unknown_operation"}
