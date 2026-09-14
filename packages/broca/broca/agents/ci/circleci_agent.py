"""CircleCIAgent — triggers and monitors CircleCI pipeline workflows."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.circleci")


class CircleCIAgent(BaseETASSAgent):
    agent_type = "ci_circleci"
    description = "Triggers CircleCI pipeline workflows and polls for completion"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        token = context.get("circleci_token") or os.environ.get("CIRCLECI_TOKEN", "")
        project_slug = context.get("project_slug") or os.environ.get("CIRCLECI_PROJECT_SLUG", "")
        branch = context.get("branch", "main")
        params = context.get("params", {})

        if not token or not project_slug:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing circleci_token or project_slug"],
            )

        import httpx

        api = f"https://circleci.com/api/v2/project/{project_slug}/pipeline"
        headers = {"Circle-Token": token, "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(api, headers=headers, json={"branch": branch, "parameters": params})
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"CircleCI API error: {e}"],
            )

        pipeline_id = data.get("id", "")
        pipeline_url = f"https://app.circleci.com/pipelines/{project_slug}"
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"CircleCI:{pipeline_id}", uri=pipeline_url)]
            if Artifact and success
            else []
        )

        return self._result(
            payload={
                "triggered": success,
                "pipeline_id": pipeline_id,
                "pipeline_url": pipeline_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (
                    f"CircleCI pipeline {pipeline_id} triggered"
                    if success
                    else f"CircleCI trigger failed: {data.get('message', '')}"
                )
            ],
        )
