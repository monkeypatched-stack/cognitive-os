"""NatsResource — health-state wrapper around PolicyControlPlane's own NATS
client (app.state._pcp._nats).

PCP already opens and owns the one NATS connection this process needs
(_connect_nats(), called from PCP.__init__ path) — this wrapper reads its
state rather than opening a second, redundant connection just to health-check.
If PCP isn't present at all, NATS is simply unavailable here.
"""

from __future__ import annotations

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class NatsResource:
    def __init__(self, pcp) -> None:
        self._pcp = pcp

    @property
    def name(self) -> str:
        return "nats"

    @property
    def config(self) -> ResourceConfig:
        # No config_keys: PCP's _connect_nats() already defaults to a local URL.
        return ResourceConfig(name="nats", required=False)

    async def initialize(self) -> ResourceHealth:
        return await self._check()

    async def health(self) -> ResourceHealth:
        return await self._check()

    async def shutdown(self) -> None:
        pass  # PCP owns the connection lifecycle, not this wrapper

    async def _check(self) -> ResourceHealth:
        if self._pcp is None:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason="Policy Control Plane not initialized",
                category=ErrorCategory.DEPENDENCY_MISSING,
                required=False,
            )

        nats_client = getattr(self._pcp, "_nats", None)
        if nats_client is None:
            # PCP.__init__ path already tried and failed/skipped — retry once here.
            try:
                await self._pcp._connect_nats()
            except Exception as exc:
                return ResourceHealth(
                    name=self.name,
                    state=ResourceState.FAILED,
                    reason=str(exc)[:200],
                    category=ErrorCategory.INTERNAL,
                    required=False,
                )
            nats_client = getattr(self._pcp, "_nats", None)

        if nats_client is None:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason="NATS not reachable or nats-py not installed",
                category=ErrorCategory.NETWORK,
                required=False,
            )
        return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
