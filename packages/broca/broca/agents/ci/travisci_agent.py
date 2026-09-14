"""TravisCIAgent — triggers Travis CI builds via REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

from .._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ci.travisci")


class TravisCIAgent(BaseETASSAgent):
    agent_type = "ci_travisci"
    description = "Triggers Travis CI builds and polls for completion status"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        token = context.get("travis_token") or os.environ.get("TRAVIS_TOKEN", "")
        repo_slug = context.get("repo_slug") or os.environ.get("TRAVIS_REPO_SLUG", "")
        branch = context.get("branch", "main")
        config = context.get("config", {})

        if not token or not repo_slug:
            self._reward(False, 0.0)
            return self._result(
                payload={"triggered": False},
                observations=["missing travis_token or repo_slug"],
            )

        import httpx

        api = "https://api.travis-ci.com/repo/requests"
        headers = {
            "Authorization": f"token {token}",
            "Travis-API-Version": "3",
            "Content-Type": "application/json",
        }
        body = {"request": ({"branch": branch, "config": config} if config else {"branch": branch})}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(api, headers=headers, json=body)
            data = resp.json()
            success = resp.status_code in (200, 201)
        except Exception as e:
            self._reward(False, 0.1)
            return self._result(
                payload={"triggered": False, "error": str(e)},
                observations=[f"Travis API error: {e}"],
            )

        request_id = data.get("id", "")
        build_url = f"https://travis-ci.com/{repo_slug}"
        self._reward(success, 0.6)
        artifacts = (
            [Artifact(kind="ci_pipeline", name=f"Travis:{request_id}", uri=build_url)] if Artifact and success else []
        )

        return self._result(
            payload={
                "triggered": success,
                "request_id": request_id,
                "build_url": build_url,
                "branch": branch,
            },
            artifacts=artifacts,
            observations=[
                (
                    f"Travis CI build request {request_id} created"
                    if success
                    else f"Travis trigger failed: {data.get('@type', '')}"
                )
            ],
        )
