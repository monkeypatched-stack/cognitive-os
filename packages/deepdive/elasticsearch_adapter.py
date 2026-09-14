"""Elasticsearch Adapter — aggregation and search persistence.

The Aggregation Layer persists everything in Elasticsearch.
Provides full-text search, semantic search, analytics, and dashboards.
"""

from __future__ import annotations

from typing import Any
from src.monkey_brain.persistence.adapters import IStoreAdapter
from src.monkey_brain.persistence.events import PersistenceEvent, EventType


class ElasticsearchAdapter(IStoreAdapter):
    """Elasticsearch persistence adapter for aggregation."""

    def __init__(self, url: str = "http://localhost:9200"):
        self._url = url
        self._client = None
        self._connected = False

    @property
    def name(self) -> str:
        return "elasticsearch"

    async def connect(self) -> None:
        try:
            from elasticsearch import AsyncElasticsearch

            self._client = AsyncElasticsearch([self._url])
            await self._client.ping()
            self._connected = True
        except ImportError:
            self._connected = False
        except Exception:
            self._connected = False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()
        self._connected = False

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "unhealthy",
            "url": self._url,
        }

    async def persist(self, event: PersistenceEvent) -> dict[str, Any]:
        if not self._connected:
            return {"status": "disconnected"}

        index = f"agentos-{event.entity_type}"
        doc = {
            "event_type": event.event_type.value,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "data": event.data,
            "timestamp": event.timestamp,
            "source": event.source,
        }

        try:
            result = await self._client.index(index=index, document=doc)
            return {"status": "indexed", "index": index, "id": result["_id"]}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def query(self, entity_type: str, entity_id: str | None = None) -> Any:
        if not self._connected:
            return None

        index = f"agentos-{entity_type}"

        if entity_id:
            try:
                result = await self._client.get(index=index, id=entity_id)
                return result["_source"]
            except Exception:
                return None

        try:
            result = await self._client.search(
                index=index, body={"query": {"match_all": {}}, "size": 100}
            )
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

    async def search(
        self, index: str, query: dict[str, Any], size: int = 100
    ) -> list[dict]:
        """Full-text search."""
        if not self._connected:
            return []

        try:
            result = await self._client.search(
                index=index, body={"query": query, "size": size}
            )
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

    async def aggregate(self, index: str, aggs: dict[str, Any]) -> dict[str, Any]:
        """Run aggregation query."""
        if not self._connected:
            return {}

        try:
            result = await self._client.search(
                index=index, body={"size": 0, "aggs": aggs}
            )
            return result.get("aggregations", {})
        except Exception:
            return {}

    def is_connected(self) -> bool:
        return self._connected
