"""JenkinsAgent — triggers and monitors Jenkins pipeline builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.jenkins")


class JenkinsAgent(BaseETASSAgent):
    agent_type = "ci_jenkins"
    description = "Triggers Jenkins pipeline builds and polls for completion status"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        base_url = context.get("jenkins_url") or os.environ.get("JENKINS_URL", "")
        job_name = context.get("job_name") or os.environ.get("JENKINS_JOB", "")
        token = context.get("jenkins_token") or os.environ.get("JENKINS_TOKEN", "")
        branch = context.get("branch", "main")
        params = context.get("params", {})

        if not base_url or not job_name:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing jenkins_url or job_name"],
            )

        import httpx

        build_url = f"{base_url.rstrip('/')}/job/{job_name}/buildWithParameters"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params["BRANCH"] = branch
        try:
            async with httpx.AsyncClient(
                timeout=30,
                verify=__import__("os").getenv("CI_TLS_VERIFY", "true").strip().lower()
                not in ("false", "0", "no", "off"),
            ) as client:
                resp = await client.post(build_url, headers=headers, params=params)
            success = resp.status_code in (200, 201)
        except Exception as e:
            logger.warning("[jenkins] trigger failed: %s", e)
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"Jenkins API error: {e}"],
            )

        queue_url = resp.headers.get("Location", "")
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"Jenkins:{job_name}", uri=queue_url)] if Artifact and success else []
        )

        return self._result(
            payload={
                "triggered": success,
                "job": job_name,
                "queue_url": queue_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (
                    f"Jenkins build triggered: {job_name} ({branch})"
                    if success
                    else f"Jenkins trigger failed: {resp.status_code}"
                )
            ],
        )
