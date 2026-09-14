"""GraphQL API Capability — GraphQL integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class GraphQLCapability(Capability):
    """GraphQL API integration capability."""

    def __init__(self, endpoint: str = "", api_key: str = ""):
        super().__init__(name="graphql")
        self._endpoint = endpoint
        self._api_key = api_key

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute GraphQL query."""
        import httpx

        endpoint = state.get("endpoint", self._endpoint)
        query = state.get("query", "")
        variables = state.get("variables", {})

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, headers=headers, json={"query": query, "variables": variables})
            return response.json()
