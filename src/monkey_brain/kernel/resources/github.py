"""GitHubResource — config-presence check mirroring cerebellum/providers.py's
existing GitHub registration (GITHUB_TOKEN env var). Unlike most optional
resources, a missing token isn't a failure — GitHubCapability already works
unauthenticated (read-only, rate-limited) — so this reports DEGRADED, not
UNAVAILABLE, when the token is absent.
"""

from __future__ import annotations

import os

from src.monkey_brain.kernel.resource_manager import (
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class GitHubResource:
    @property
    def name(self) -> str:
        return "github"

    @property
    def config(self) -> ResourceConfig:
        return ResourceConfig(name="github", required=False)

    async def initialize(self) -> ResourceHealth:
        return self._check()

    async def health(self) -> ResourceHealth:
        return self._check()

    async def shutdown(self) -> None:
        pass  # no connection held

    def _check(self) -> ResourceHealth:
        if os.environ.get("GITHUB_TOKEN", "").strip():
            return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
        return ResourceHealth(
            name=self.name,
            state=ResourceState.DEGRADED,
            reason="GITHUB_TOKEN not set — unauthenticated, read-only, rate-limited",
            required=False,
        )
