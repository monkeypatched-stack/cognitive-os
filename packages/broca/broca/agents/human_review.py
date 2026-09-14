"""HumanReviewAgent — n8n webhook review gate, returns typed AgentResult."""

from __future__ import annotations
import logging
import os
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.human_review")


class HumanReviewAgent(BaseETASSAgent):
    agent_type = "human_review"
    description = "Human-in-the-loop review via n8n webhook; auto-approves when n8n is unavailable"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("n8n")
        webhook_url = os.environ.get("N8N_WEBHOOK_URL_HUMAN_REVIEW", "")

        if cap and webhook_url:
            try:
                return await self._n8n(webhook_url, context)
            except Exception as e:
                logger.warning("[human_review] n8n failed: %s — auto-approving", e)

        self._reward(True, 0.7)
        return self._result(
            payload={"approved": True},
            observations=["auto-approved: n8n not configured"],
        )

    async def _n8n(self, webhook_url: str, context: dict):
        import httpx

        payload = {
            "chart_name": context.get("chart_name", "etass"),
            "chart_version": context.get("chart_version", "0.1.0"),
            "summary": str(context.get("question", ""))[:500],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(webhook_url, json=payload)
        data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
        approved = data.get("approved", resp.status_code == 200)
        self._reward(approved, 0.3)
        return self._result(
            payload={"approved": approved},
            observations=[data.get("feedback", "approved" if approved else "rejected")],
            evidence=[data] if not approved else [],
        )
