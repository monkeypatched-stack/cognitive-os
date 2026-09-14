"""AzurePipelinesAgent — triggers and monitors Azure DevOps pipeline runs."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.azure_pipelines")


class AzurePipelinesAgent(BaseETASSAgent):
    agent_type = "ci_azure_pipelines"
    description = "Triggers Azure DevOps pipeline runs and polls for completion"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        org = context.get("azure_org") or os.environ.get("AZURE_DEVOPS_ORG", "")
        project = context.get("azure_project") or os.environ.get("AZURE_DEVOPS_PROJECT", "")
        pipeline_id = context.get("pipeline_id") or os.environ.get("AZURE_PIPELINE_ID", "")
        token = context.get("azure_token") or os.environ.get("AZURE_DEVOPS_TOKEN", "")
        branch = context.get("branch", "main")
        variables = context.get("variables", {})

        if not org or not project or not pipeline_id or not token:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing azure_org, project, pipeline_id, or token"],
            )

        import httpx

        api = f"https://dev.azure.com/{org}/{project}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1-preview.1"
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        body = {
            "resources": {"repositories": {"self": {"refName": f"refs/heads/{branch}"}}},
            "variables": {k: {"value": v} for k, v in variables.items()},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(api, headers=headers, json=body)
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"Azure API error: {e}"],
            )

        run_id = data.get("id", "")
        run_url = data.get("webUrl", "")
        self._reward(success, 0.6)
        artifacts = [Artifact(kind="ci_pipeline", name=f"Azure:{run_id}", uri=run_url)] if Artifact and success else []

        return self._result(
            payload={
                "triggered": success,
                "run_id": run_id,
                "run_url": run_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (
                    f"Azure pipeline run {run_id} started"
                    if success
                    else f"Azure trigger failed: {data.get('message', '')}"
                )
            ],
        )
