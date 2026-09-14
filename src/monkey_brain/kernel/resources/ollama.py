"""OllamaResource — lightweight reachability check for the local Ollama
server, mirroring the exact TCP-connect pattern already used by
broca/agents/specification_discovery_agent.py's _ollama_available() and
sittingface/codegen_agent.py's OllamaClient/_try_ollama probing.
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


class OllamaResource:
    @property
    def name(self) -> str:
        return "ollama"

    @property
    def config(self) -> ResourceConfig:
        return ResourceConfig(name="ollama", required=False)

    async def initialize(self) -> ResourceHealth:
        return self._check()

    async def health(self) -> ResourceHealth:
        return self._check()

    async def shutdown(self) -> None:
        pass  # stateless reachability check — nothing to release

    def _check(self) -> ResourceHealth:
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 11434

        try:
            with socket.create_connection((host, port), timeout=1.0):
                return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
        except Exception as exc:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason=f"Cannot reach Ollama at {host}:{port}: {exc}",
                category=ErrorCategory.NETWORK,
                required=False,
            )
