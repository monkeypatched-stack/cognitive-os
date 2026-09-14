"""MonkeyBrain SDK Client."""

from __future__ import annotations

import httpx
from typing import Any


class MonkeyBrainClient:
    """Client for the MonkeyBrain API."""

    def __init__(self, base_url: str = "http://localhost:8031", api_key: str = ""):
        self._base_url = base_url
        self._api_key = api_key
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def query(self, question: str) -> dict[str, Any]:
        """Send a query to MonkeyBrain."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/api/v1/agentos/query",
                json={"question": question},
                headers=self._headers,
            )
            return response.json()

    async def health(self) -> dict[str, Any]:
        """Check health."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self._base_url}/health")
            return response.json()

    async def get_state(self) -> dict[str, Any]:
        """Get runtime state."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self._base_url}/state", headers=self._headers)
            return response.json()
