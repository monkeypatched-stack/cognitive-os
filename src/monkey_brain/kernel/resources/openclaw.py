"""OpenClawResource — mirrors cerebellum/providers.py's OpenClaw
registration: no OPENCLAW_API_URL means CLI/local-gateway mode, which is
the normal default (not degraded) — only an explicitly-configured but
unreachable HTTP endpoint counts as a real problem.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class OpenClawResource:
    @property
    def name(self) -> str:
        return "openclaw"

    @property
    def config(self) -> ResourceConfig:
        return ResourceConfig(name="openclaw", required=False)

    async def initialize(self) -> ResourceHealth:
        return self._check()

    async def health(self) -> ResourceHealth:
        return self._check()

    async def shutdown(self) -> None:
        pass  # no connection held

    def _check(self) -> ResourceHealth:
        api_url = os.environ.get("OPENCLAW_API_URL", "").strip()
        if not api_url:
            return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)

        parsed = urlparse(api_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.FAILED,
                reason=f"OPENCLAW_API_URL is not a valid URL: {api_url!r}",
                category=ErrorCategory.CONFIGURATION,
                required=False,
            )

        try:
            with socket.create_connection((host, port), timeout=1.0):
                return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
        except Exception as exc:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason=f"Cannot reach OpenClaw at {host}:{port}: {exc}",
                category=ErrorCategory.NETWORK,
                required=False,
            )
