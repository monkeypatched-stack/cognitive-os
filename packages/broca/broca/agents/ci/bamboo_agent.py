"""BambooAgent — triggers Atlassian Bamboo builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.bamboo")


class BambooAgent(BaseETASSAgent):
    agent_type = "ci_bamboo"
    description = "Triggers Atlassian Bamboo build plans and polls for completion"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        base_url = context.get("bamboo_url") or os.environ.get("BAMBOO_URL", "")
        plan_key = context.get("plan_key") or os.environ.get("BAMBOO_PLAN_KEY", "")
        token = context.get("bamboo_token") or os.environ.get("BAMBOO_TOKEN", "")
        branch = context.get("branch", "master")
        variables = context.get("variables", {})

        if not base_url or not plan_key:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing bamboo_url or plan_key"],
            )

        import httpx

        api = f"{base_url.rstrip('/')}/rest/api/latest/plan/{plan_key}/stage"
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params = {"os_authType": "basic"}
        if variables:
            params["bamboo.variable"] = [f"{k}={v}" for k, v in variables.items()]

        try:
            async with httpx.AsyncClient(
                timeout=30,
                verify=__import__("os").getenv("CI_TLS_VERIFY", "true").strip().lower()
                not in ("false", "0", "no", "off"),
            ) as client:
                resp = await client.post(api, headers=headers, params=params)
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"Bamboo API error: {e}"],
            )

        build_number = data.get("buildNumber", "")
        build_url = data.get("link", {}).get("href", f"{base_url.rstrip('/')}/browse/{plan_key}-{build_number}")
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"Bamboo:{build_number}", uri=build_url)] if Artifact and success else []
        )

        return self._result(
            payload={
                "triggered": success,
                "build_number": build_number,
                "build_url": build_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (f"Bamboo build #{build_number} queued" if success else f"Bamboo trigger failed: {resp.status_code}")
            ],
        )
