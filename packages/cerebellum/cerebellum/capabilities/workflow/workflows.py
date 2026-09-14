"""Workflow Capabilities — workflow engine integrations."""

from __future__ import annotations

from cerebellum.capability import Capability


class N8nCapability(Capability):
    """n8n workflow capability with robust error handling and fallback."""

    def __init__(self, webhook_url: str = ""):
        super().__init__(name="n8n")
        self._webhook_url = webhook_url
        self._is_available = bool(webhook_url.strip()) if webhook_url else False

    async def execute(self, state, **kwargs):
        if not self._is_available:
            return {
                "status": "unavailable",
                "workflow": "n8n",
                "reason": "n8n webhook URL not configured",
                "state": state,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        self._webhook_url,
                        json=state,
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "workflow": "n8n",
                        "error": str(e),
                        "http_status": e.response.status_code,
                        "state": state,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "workflow": "n8n",
                        "error": "request timed out",
                        "state": state,
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "workflow": "n8n",
                        "error": str(e),
                        "state": state,
                    }
        except Exception as e:
            return {
                "status": "exception",
                "workflow": "n8n",
                "error": str(e),
                "state": state,
            }


class TemporalCapability(Capability):
    def __init__(self, server_url: str = ""):
        super().__init__(name="temporal")
        self._server_url = server_url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "workflow": "temporal"}


class AirflowCapability(Capability):
    def __init__(self, api_url: str = "", username: str = "", password: str = ""):
        super().__init__(name="airflow")
        self._api_url = api_url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "workflow": "airflow"}


class PrefectCapability(Capability):
    def __init__(self, api_url: str = ""):
        super().__init__(name="prefect")
        self._api_url = api_url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "workflow": "prefect"}


class ArgoCapability(Capability):
    def __init__(self, server_url: str = ""):
        super().__init__(name="argo")
        self._server_url = server_url

    async def execute(self, state, **kwargs):
        return {"status": "configured", "workflow": "argo"}
