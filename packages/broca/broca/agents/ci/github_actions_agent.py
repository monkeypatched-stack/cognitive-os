"""GitHubActionsAgent — triggers and monitors GitHub Actions workflow runs."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.github_actions")


class GitHubActionsAgent(BaseETASSAgent):
    agent_type = "ci_github_actions"
    description = "Triggers GitHub Actions workflow_dispatch and polls run status"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        token = context.get("github_token") or os.environ.get("GITHUB_TOKEN", "")
        owner = context.get("github_owner") or os.environ.get("GITHUB_OWNER", "")
        repo = context.get("github_repo") or os.environ.get("GITHUB_REPO", "")
        workflow = context.get("workflow", "")
        branch = context.get("branch", "main")
        inputs = context.get("inputs", {})

        if not token or not owner or not repo or not workflow:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, or workflow"],
            )

        import httpx

        api = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(api, headers=headers, json={"ref": branch, "inputs": inputs})
            success = resp.status_code in (200, 204)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"GitHub Actions API error: {e}"],
            )

        run_url = f"https://github.com/{owner}/{repo}/actions/workflows/{workflow}"
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"GHActions:{workflow}", uri=run_url)] if Artifact and success else []
        )

        return self._result(
            payload={
                "triggered": success,
                "workflow": workflow,
                "branch": branch,
                "run_url": run_url,
            },
            artifacts=artifacts,
            observations=[
                (
                    f"GitHub Actions triggered: {workflow} ({branch})"
                    if success
                    else f"Trigger failed: {resp.status_code}"
                )
            ],
        )
