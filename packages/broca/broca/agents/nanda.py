"""NANDAAgent and NANDAProxyAgent — NANDA as a discovery provider for Broca.

NANDA (Networked Agents and Decentralized AI) is the global DNS for AI agents.
MonkeyBrain uses it as a provider — discover only, never publish.

  NANDAAgent
    Handles explicit "nanda" workflow steps:
      {"name": "find_vision_agent", "agent": "nanda", "operation": "discover", "capability": "vision"}
    Wraps NANDACapability. Returns discovered agent cards in payload.

  NANDAProxyAgent
    Dynamically instantiated when BrocaAgentRegistry.discover() finds no local
    agent but NANDA returns a remote match.
    Wraps the remote agent card as a local BrocaAgent — the runtime, policy,
    and observers see no difference between local and remote execution.

Flow:
  runtime.execute(step) → registry.discover(step_type)
    → local? return it
    → NANDA? return NANDAProxyAgent(card)
    → none? log warning, skip step
"""

from __future__ import annotations

import logging
from typing import Any

from broca.agents._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.nanda")


class NANDAAgent(BaseETASSAgent):
    """Handles explicit NANDA discovery and routing workflow steps."""

    agent_type = "nanda"
    description = (
        "NANDA provider — discovers remote agents from the decentralized NANDA registry by capability or domain"
    )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict[str, Any]):
        cap = self._find_capability("nanda")
        if cap is None:
            self._reward(0.3)
            return self._result(
                payload={"status": "nanda_capability_not_registered"},
                observations=["NANDA capability not found — set NANDA_REGISTRY_URL"],
            )

        operation = context.get("operation", "discover")
        result = await cap.execute({**context, "operation": operation})
        success = result.get("status") in ("ok", "routed")
        self._reward(success)
        return self._result(
            payload=result,
            observations=[
                (
                    f"nanda {operation}: {result.get('status', 'unknown')} ({len(result.get('agents', []))} agents)"
                    if operation == "discover"
                    else f"nanda {operation}: {result.get('status', 'unknown')}"
                )
            ],
        )


class NANDAProxyAgent:
    """Wraps a remote NANDA agent card as a local BrocaAgent.

    The runtime calls handle() — it doesn't know whether execution is local or remote.
    Reward and AgentResult follow the same contract as local agents.
    """

    def __init__(self, remote_card: dict[str, Any]) -> None:
        self._card = remote_card
        self._endpoint = remote_card.get("endpoint", "")
        self._agent_type = remote_card.get("agent_id", "nanda_remote")
        self._description = remote_card.get("description", "Remote NANDA agent")
        self._last_reward = 0.5

    @property
    def agent_type(self) -> str:
        return self._agent_type

    @property
    def description(self) -> str:
        return f"[NANDA] {self._description}"

    def feedback(self) -> float:
        return self._last_reward

    async def handle(self, context: dict[str, Any]) -> Any:
        """Route step to the remote NANDA agent and coerce result to AgentResult."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._endpoint}/tasks",
                    json=context,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                result = resp.json()
                self._last_reward = float(result.get("reward", 0.8))
        except Exception as exc:
            logger.warning(
                "[nanda_proxy:%s] → %s failed: %s",
                self._agent_type,
                self._endpoint,
                exc,
            )
            self._last_reward = 0.1
            result = {"error": str(exc), "success": False}

        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import AgentResult

            return AgentResult(
                reward=self._last_reward,
                payload=result.get("payload", result),
                observations=[f"nanda_proxy:{self._agent_type} → {result.get('status', 'ok')}"],
                success=result.get("success", self._last_reward >= 0.5),
            )
        except Exception:
            return result
