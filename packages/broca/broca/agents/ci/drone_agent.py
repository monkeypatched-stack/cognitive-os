"""DroneCIAgent — triggers Drone CI builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.drone")


class DroneCIAgent(BaseETASSAgent):
    agent_type = "ci_drone"
    description = "Triggers Drone CI builds and polls for completion status"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        base_url = context.get("drone_url") or os.environ.get("DRONE_URL", "")
        token = context.get("drone_token") or os.environ.get("DRONE_TOKEN", "")
        repo_slug = context.get("repo_slug") or os.environ.get("DRONE_REPO_SLUG", "")
        branch = context.get("branch", "main")
        commit = context.get("commit", "HEAD")

        if not base_url or not token or not repo_slug:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing drone_url, drone_token, or repo_slug"],
            )

        import httpx

        api = f"{base_url.rstrip('/')}/api/repos/{repo_slug}/builds"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {"branch": branch, "commit": commit}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(api, headers=headers, json=body)
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"Drone API error: {e}"],
            )

        build_id = data.get("id", "")
        build_url = data.get("link", f"{base_url.rstrip('/')}/{repo_slug}/{build_id}")
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"Drone:{build_id}", uri=build_url)] if Artifact and success else []
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
                    f"Drone build #{build_id} triggered"
                    if success
                    else f"Drone trigger failed: {data.get('message', '')}"
                )
            ],
        )
