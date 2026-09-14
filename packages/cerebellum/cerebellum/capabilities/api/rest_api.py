"""REST API Capability — HTTP REST integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class RestAPICapability(Capability):
    """REST API integration capability."""

    def __init__(self, base_url: str = "", api_key: str = ""):
        super().__init__(name="rest_api")
        self._base_url = base_url
        self._api_key = api_key

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute REST API call."""
        import httpx

        url = state.get("url", self._base_url)
        method = state.get("method", "GET")
        headers = state.get("headers", {})
        data = state.get("data")

        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=data)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=data)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                return {"status": "error", "error": f"Unsupported method: {method}"}

            return {
                "status_code": response.status_code,
                "data": (
                    response.json()
                    if response.headers.get("content-type", "").startswith("application/json")
                    else response.text
                ),
                "headers": dict(response.headers),
            }
