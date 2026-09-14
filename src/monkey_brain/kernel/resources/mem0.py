"""Mem0Resource — health-state wrapper around Mem0Adapter.

Mem0Adapter already behaves well (logs once at connect(), not per-access —
verified this session), this wrapper just gives it a place in the health
dashboard. Missing credentials are the expected, common case (mem0 is
optional here) — reported as UNAVAILABLE/OPTIONAL_MISSING with a
"local memory only" reason, not a warning-worthy failure.
"""

from __future__ import annotations

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class Mem0Resource:
    def __init__(self, adapter) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mem0"

    @property
    def config(self) -> ResourceConfig:
        return ResourceConfig(name="mem0", required=False)

    async def initialize(self) -> ResourceHealth:
        return await self._check()

    async def health(self) -> ResourceHealth:
        return await self._check()

    async def shutdown(self) -> None:
        await self._adapter.disconnect()

    async def _check(self) -> ResourceHealth:
        if not self._adapter.is_connected():
            try:
                await self._adapter.connect()
            except Exception:
                pass  # Mem0Adapter.connect() already swallows and logs internally

        if self._adapter.is_connected():
            return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)

        return ResourceHealth(
            name=self.name,
            state=ResourceState.UNAVAILABLE,
            reason="Credentials missing or mem0ai not installed — falling back to local memory only",
            category=ErrorCategory.OPTIONAL_MISSING,
            required=False,
        )
