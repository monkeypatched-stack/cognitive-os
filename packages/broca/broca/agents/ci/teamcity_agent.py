"""TeamCityAgent — triggers TeamCity builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.teamcity")


class TeamCityAgent(BaseETASSAgent):
    agent_type = "ci_teamcity"
    description = "Triggers TeamCity build configurations and polls for completion"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        base_url = context.get("teamcity_url") or os.environ.get("TEAMCITY_URL", "")
        build_type = context.get("build_type") or os.environ.get("TEAMCITY_BUILD_TYPE", "")
        token = context.get("teamcity_token") or os.environ.get("TEAMCITY_TOKEN", "")
        branch = context.get("branch", "main")
        properties = context.get("properties", {})

        if not base_url or not build_type:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing teamcity_url or build_type"],
            )

        import httpx

        api = f"{base_url.rstrip('/')}/app/rest/buildQueue"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        build_body = {
            "buildType": {"id": build_type},
            "branchName": branch,
            "properties": (
                {"property": [{"name": k, "value": v} for k, v in properties.items()]} if properties else {}
            ),
        }

        try:
            async with httpx.AsyncClient(
                timeout=30,
                verify=__import__("os").getenv("CI_TLS_VERIFY", "true").strip().lower()
                not in ("false", "0", "no", "off"),
            ) as client:
                resp = await client.post(api, headers=headers, json=build_body)
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"TeamCity API error: {e}"],
            )

        build_id = data.get("id", "")
        build_url = f"{base_url.rstrip('/')}/buildConfiguration/{build_type}/{build_id}" if build_id else base_url
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"TeamCity:{build_id}", uri=build_url)] if Artifact and success else []
        )

        return self._result(
            payload={
                "triggered": success,
                "build_id": build_id,
                "build_url": build_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (f"TeamCity build {build_id} queued" if success else f"TeamCity trigger failed: {resp.status_code}")
            ],
        )
