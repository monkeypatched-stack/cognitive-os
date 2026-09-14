"""Redis Adapter — runtime working memory.

Responsibilities:
- Active Sessions
- Conversation State
- Active Workflow State
- Scheduler Queues
- Locks, Runtime Cache
- Temporary Variables

Redis represents the runtime's working memory.
Redis should survive only as long as execution requires.
"""

from __future__ import annotations

import logging
import os

from typing import Any
from src.monkey_brain.persistence.adapters import IStoreAdapter
from src.monkey_brain.persistence.events import PersistenceEvent, EventType

logger = logging.getLogger("monkey_brain.persistence.redis_adapter")


class RedisAdapter(IStoreAdapter):
    """Redis persistence adapter."""

    def __init__(self, url: str = "redis://localhost:6379"):
        self._url = url
        self._client = None
        self._connected = False

    @property
    def name(self) -> str:
        return "redis"

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis

            # redis-py defaults socket_timeout to None — a HUNG (not merely slow) server means
            # a read that never returns, blocking the awaiting coroutine forever. Bound both
            # the connect and the per-operation socket.
            client = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
                socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
                health_check_interval=int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL_SEC", "30")),
            )
            # Only cache on success - if exception occurs below, connection stays None
            self._client = client
            self._connected = True
            logger.info("Redis adapter connected to %s", self._url)
        except ImportError:
            logger.warning("redis driver not installed — Redis adapter disabled")
            # Leave _client as None (don't cache failure)
            self._connected = False
        except Exception as exc:
            logger.warning("Redis connection failed: %s (will retry on next operation)", exc)
            # Leave _client as None (don't cache failure)
            # Next operation will see _connected=False and automatically retry
            self._connected = False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()
        self._connected = False

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "unhealthy",
        }

    async def persist(self, event: PersistenceEvent) -> dict[str, Any]:
        if not self._connected:
            logger.debug("Redis not connected, attempting to reconnect...")
            await self.connect()
            if not self._connected:
                logger.warning("Redis reconnection failed, cannot persist")
                return {"status": "disconnected"}

        key = f"{event.entity_type}:{event.entity_id}"

        if event.event_type in (
            EventType.ENTITY_CREATED,
            EventType.ENTITY_UPDATED,
            EventType.MEMORY_STORED,
            EventType.MEMORY_UPDATED,
            EventType.WORLD_STATE_SAVED,
            EventType.ACTOR_STATE_SAVED,
            EventType.SOCIETY_STATE_SAVED,
            EventType.MEMBERSHIP_SAVED,
            EventType.CONTEXT_SAVED,
        ):
            import json as _json

            await self._client.set(key, _json.dumps(event.data, default=str))
            return {"status": "stored", "key": key}

        elif event.event_type in (EventType.ENTITY_DELETED, EventType.MEMORY_FORGOTTEN):
            await self._client.delete(key)
            return {"status": "deleted", "key": key}

        return {"status": "ignored"}

    async def query(self, entity_type: str, entity_id: str | None = None) -> Any:
        if not self._connected:
            logger.debug("Redis not connected, attempting to reconnect...")
            await self.connect()
            if not self._connected:
                logger.warning("Redis reconnection failed, cannot query")
                return None

        if entity_id:
            key = f"{entity_type}:{entity_id}"
            return await self._client.get(key)

        pattern = f"{entity_type}:*"
        keys = await self._client.keys(pattern)
        return keys

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not self._connected:
            logger.debug("Redis not connected, attempting to reconnect...")
            await self.connect()
            if not self._connected:
                logger.warning("Redis reconnection failed, cannot set value")
                return

        if ttl:
            await self._client.setex(key, ttl, str(value))
        else:
            await self._client.set(key, str(value))

    async def get(self, key: str) -> Any:
        if not self._connected:
            logger.debug("Redis not connected, attempting to reconnect...")
            await self.connect()
            if not self._connected:
                logger.warning("Redis reconnection failed, cannot get value")
                return None

        return await self._client.get(key)

    def is_connected(self) -> bool:
        return self._connected
