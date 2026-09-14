"""BuildkiteAgent — triggers Buildkite pipeline builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.buildkite")


class BuildkiteAgent(BaseETASSAgent):
    agent_type = "ci_buildkite"
    description = "Triggers Buildkite pipeline builds and polls for completion"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        token = context.get("buildkite_token") or os.environ.get("BUILDKITE_TOKEN", "")
        pipeline_slug = context.get("pipeline_slug") or os.environ.get("BUILDKITE_PIPELINE_SLUG", "")
        branch = context.get("branch", "main")
        commit = context.get("commit", "HEAD")
        message = context.get("message", "Triggered by Cognitive OS")
        meta_data = context.get("meta_data", {})

        if not token or not pipeline_slug:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing buildkite_token or pipeline_slug"],
            )

        import httpx

        api = f"https://api.buildkite.com/v2/organizations/{os.environ.get('BUILDKITE_ORG', '')}/pipelines/{pipeline_slug}/builds"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "branch": branch,
            "commit": commit,
            "message": message,
            "meta_data": meta_data,
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
                observations=[f"Buildkite API error: {e}"],
            )

        build_id = data.get("id", "")
        build_url = data.get("web_url", "")
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"Buildkite:{build_id}", uri=build_url)] if Artifact and success else []
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
                (
                    f"Buildkite build {build_id} created"
                    if success
                    else f"Buildkite trigger failed: {data.get('message', '')}"
                )
            ],
        )
