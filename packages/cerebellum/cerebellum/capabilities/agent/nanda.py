"""NANDACapability — Networked Agents and Decentralized AI integration.

NANDA is a decentralized, global DNS for AI agents.
MonkeyBrain uses NANDA as a pure provider — discover and route only.
We never publish to NANDA; we query it when no local agent handles a step type.

Operations:
  discover  — query NANDA for remote agents by capability/domain/type
  route     — send a step to a discovered remote agent and return its result

Agent Card schema (received from NANDA):
  {
    "agent_id": str,
    "name": str,
    "description": str,
    "version": str,
    "capabilities": [{"name": str, "description": str}],
    "domains": [str],
    "endpoint": str,
    "metadata": {str: str}
  }

Environment variables:
  NANDA_REGISTRY_URL  — NANDA registry base URL (required for live operation)
  NANDA_API_KEY       — API key for authenticated registries (optional)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from cerebellum.capability import Capability

logger = logging.getLogger("cerebellum.nanda")

NANDA_REGISTRY_URL = os.environ.get("NANDA_REGISTRY_URL", "")
NANDA_API_KEY = os.environ.get("NANDA_API_KEY", "")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if NANDA_API_KEY:
        h["Authorization"] = f"Bearer {NANDA_API_KEY}"
    return h


class NANDACapability(Capability):
    """HTTP client for NANDA provider operations: discover and route.

    NANDA is a discovery provider — MonkeyBrain queries it, never publishes to it.
    All NANDA network I/O is encapsulated here; agents never call NANDA directly.
    """

    def __init__(self, registry_url: str = "") -> None:
        super().__init__(name="nanda")
        self._registry_url = registry_url or NANDA_REGISTRY_URL
        self._available = bool(self._registry_url)

    def can_execute(self, state) -> bool:
        return self._available

    def estimate_reward(self, state) -> float:
        return 0.8 if self._available else 0.0

    def estimate_cost(self, state) -> float:
        return 0.1  # lightweight network call

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        op = state.get("operation", "discover")
        if op == "discover":
            return await self._discover(state)
        if op == "route":
            return await self._route(state)
        return {
            "status": "unknown_operation",
            "operation": op,
            "supported": ["discover", "route"],
        }

    # ------------------------------------------------------------------
    # Operations (discover + route only — NANDA is a provider, not a registry we publish to)
    # ------------------------------------------------------------------

    async def _discover(self, state: dict[str, Any]) -> dict[str, Any]:
        """Discover remote agents matching a capability or domain."""
        if not self._available:
            return {
                "status": "skipped",
                "agents": [],
                "reason": "NANDA_REGISTRY_URL not configured",
            }

        params: dict[str, str] = {}
        if state.get("capability"):
            params["capability"] = state["capability"]
        if state.get("domain"):
            params["domain"] = state["domain"]
        if state.get("agent_type"):
            params["type"] = state["agent_type"]

        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._registry_url}/agents",
                    params=params,
                    headers=_headers(),
                )
                resp.raise_for_status()
                agents = resp.json().get("agents", resp.json() if isinstance(resp.json(), list) else [])
                logger.debug("[nanda] discovered %d agents for %s", len(agents), params)
                return {"status": "ok", "agents": agents, "query": params}
        except Exception as exc:
            logger.warning("[nanda] discovery failed: %s", exc)
            return {"status": "error", "agents": [], "reason": str(exc)}

    async def _route(self, state: dict[str, Any]) -> dict[str, Any]:
        """Route a task to a remote NANDA agent."""
        endpoint = state.get("endpoint", "")
        if not endpoint:
            return {"status": "error", "reason": "endpoint missing"}

        task = state.get("task", state)
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{endpoint}/tasks",
                    json=task,
                    headers=_headers(),
                )
                resp.raise_for_status()
                result = resp.json()
                logger.info(
                    "[nanda] task routed to %s, status: %s",
                    endpoint,
                    result.get("status"),
                )
                return {"status": "routed", "endpoint": endpoint, "result": result}
        except Exception as exc:
            logger.warning("[nanda] routing failed to %s: %s", endpoint, exc)
            return {"status": "error", "endpoint": endpoint, "reason": str(exc)}
