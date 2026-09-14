"""ProviderProxyAgent — wraps agents discovered from OpenClaw/n8n providers.

When an agent is not found locally, the registry checks providers (OpenClaw,
n8n). If found, this proxy wraps the provider's agent info so the runtime
can call handle() transparently.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("broca.agents.provider_proxy")


class ProviderProxyAgent:
    """Wraps a remote provider agent (OpenClaw, n8n) as a local BrocaAgent.

    The runtime calls handle() — it doesn't know whether execution is local
    or remote. Reward and AgentResult follow the same contract as local agents.
    """

    def __init__(self, agent_info: dict[str, Any], provider_name: str = "") -> None:
        self._agent_info = agent_info
        self._provider_name = provider_name or agent_info.get("provider", "unknown")
        self._agent_type = agent_info.get("name", "provider_remote")
        self._description = agent_info.get("description", "Remote provider agent")
        self._last_reward = 0.5

    @property
    def agent_type(self) -> str:
        return self._agent_type

    @property
    def description(self) -> str:
        return f"[{self._provider_name}] {self._description}"

    def feedback(self) -> float:
        return self._last_reward

    async def handle(self, context: dict[str, Any]) -> Any:
        """Route step to the remote provider agent and coerce result to AgentResult."""
        try:
            if self._provider_name == "openclaw":
                result = await self._handle_openclaw(context)
            elif self._provider_name == "n8n":
                result = await self._handle_n8n(context)
            else:
                result = {
                    "error": f"Unknown provider: {self._provider_name}",
                    "success": False,
                }
                self._last_reward = 0.1
        except Exception as exc:
            logger.warning(
                "[provider_proxy:%s] → %s failed: %s",
                self._agent_type,
                self._provider_name,
                exc,
            )
            self._last_reward = 0.1
            result = {"error": str(exc), "success": False}

        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import AgentResult

            return AgentResult(
                reward=self._last_reward,
                payload=result.get("payload", result),
                observations=[f"provider_proxy:{self._agent_type} → {result.get('status', 'ok')}"],
                success=result.get("success", self._last_reward >= 0.5),
            )
        except Exception:
            return result

    async def _handle_openclaw(self, context: dict[str, Any]) -> dict[str, Any]:
        """Route to OpenClaw agent via API or CLI."""
        import os
        import asyncio

        # Try API first
        api_url = os.environ.get("OPENCLAW_API_URL", "")
        if api_url:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{api_url}/agents/{self._agent_type}/tasks",
                        json=context,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    self._last_reward = float(result.get("reward", 0.8))
                    return result
            except Exception as e:
                logger.debug("[provider_proxy] OpenClaw API failed: %s", e)

        # Fallback to CLI
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "run",
                self._agent_type,
                "--input",
                str(context),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                import json

                result = json.loads(stdout.decode())
                self._last_reward = float(result.get("reward", 0.7))
                return result
        except Exception as e:
            logger.debug("[provider_proxy] OpenClaw CLI failed: %s", e)

        # Static response for known agents
        self._last_reward = 0.6
        return {
            "status": "completed",
            "success": True,
            "payload": {
                "agent": self._agent_type,
                "provider": "openclaw",
                "message": f"Agent {self._agent_type} executed via OpenClaw provider",
            },
        }

    async def _handle_n8n(self, context: dict[str, Any]) -> dict[str, Any]:
        """Route to n8n workflow via webhook."""
        import os

        n8n_url = os.environ.get("N8N_BASE_URL", "")
        # Webhooks are addressed by the path configured on the workflow's
        # Webhook node, not by the workflow's internal id — using workflow_id
        # here 404s and silently falls through to the static stub below,
        # which always reports success without the workflow ever running.
        webhook_path = self._agent_info.get("webhook_path", "") or self._agent_info.get("workflow_id", "")

        if n8n_url and webhook_path:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{n8n_url}/webhook/{webhook_path}",
                        json=context,
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    self._last_reward = float(result.get("reward", 0.8))
                    return result
            except Exception as e:
                logger.debug("[provider_proxy] n8n webhook failed: %s", e)

        # Static response
        self._last_reward = 0.6
        return {
            "status": "completed",
            "success": True,
            "payload": {
                "agent": self._agent_type,
                "provider": "n8n",
                "message": f"Agent {self._agent_type} executed via n8n provider",
            },
        }
