"""Elasticsearch Capability — search engine integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class ElasticsearchCapability(Capability):
    """Elasticsearch integration capability."""

    def __init__(self, url: str = "http://localhost:9200"):
        super().__init__(name="elasticsearch")
        self._url = url
        self._client = None

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute Elasticsearch operation."""
        from elasticsearch import AsyncElasticsearch

        if not self._client:
            self._client = AsyncElasticsearch([self._url])

        operation = state.get("operation", "search")
        index = state.get("index", "")
        query = state.get("query", {})

        if operation == "search":
            result = await self._client.search(index=index, body=query)
            return {
                "results": [hit["_source"] for hit in result["hits"]["hits"]],
                "total": result["hits"]["total"]["value"],
            }

        elif operation == "index":
            body = state.get("body", {})
            result = await self._client.index(index=index, document=body)
            return {"id": result["_id"], "status": result["result"]}

        return {"status": "unknown_operation"}
