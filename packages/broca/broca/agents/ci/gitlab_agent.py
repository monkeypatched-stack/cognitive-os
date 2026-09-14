"""GitLabCIAgent — triggers and monitors GitLab CI/CD pipelines via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.gitlab")


class GitLabCIAgent(BaseETASSAgent):
    agent_type = "ci_gitlab"
    description = "Triggers GitLab CI pipeline creation and polls pipeline status"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        base_url = context.get("gitlab_url") or os.environ.get("GITLAB_URL", "https://gitlab.com")
        project_id = context.get("project_id") or os.environ.get("GITLAB_PROJECT_ID", "")
        token = context.get("gitlab_token") or os.environ.get("GITLAB_TOKEN", "")
        branch = context.get("branch", "main")
        variables = context.get("variables", {})

        if not project_id or not token:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing project_id or gitlab_token"],
            )

        import httpx

        api = f"{base_url.rstrip('/')}/api/v4/projects/{project_id}/pipeline"
        headers = {"PRIVATE-TOKEN": token}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    api,
                    headers=headers,
                    json={
                        "ref": branch,
                        "variables": [{"key": k, "value": v} for k, v in variables.items()],
                    },
                )
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"GitLab API error: {e}"],
            )

        pipeline_id = data.get("id", "")
        pipeline_url = data.get("web_url", "")
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"GitLab:{pipeline_id}", uri=pipeline_url)]
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
                    f"GitLab CI pipeline #{pipeline_id} created"
                    if success
                    else f"GitLab trigger failed: {data.get('message', '')}"
                )
            ],
        )
