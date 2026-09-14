"""SittingFaceResource — health-state wrapper around the SomaticCompiler
init_sittingface() attaches to app.state.somatic_compiler.

There's no separate connect step to retry here — init_sittingface() already
ran (log-and-continue) by the time this is registered — so this wrapper
just reports whatever state resulted (DEGRADED if the compiler exists but
loaded zero charts, UNAVAILABLE if it's missing entirely).
"""

from __future__ import annotations

from src.monkey_brain.kernel.resource_manager import (
    ErrorCategory,
    ResourceConfig,
    ResourceHealth,
    ResourceState,
)


class SittingFaceResource:
    def __init__(self, app) -> None:
        self._app = app

    @property
    def name(self) -> str:
        return "sittingface"

    @property
    def config(self) -> ResourceConfig:
        return ResourceConfig(name="sittingface", required=False)

    async def initialize(self) -> ResourceHealth:
        return self._check()

    async def health(self) -> ResourceHealth:
        return self._check()

    async def shutdown(self) -> None:
        pass  # no connection/handle to release — file-backed compiler

    def _check(self) -> ResourceHealth:
        compiler = getattr(self._app.state, "somatic_compiler", None)
        if compiler is None:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.UNAVAILABLE,
                reason="SittingFace repo not found or failed to load",
                category=ErrorCategory.DEPENDENCY_MISSING,
                required=False,
            )
        chart_count = len(getattr(compiler, "charts", []))
        if chart_count == 0:
            return ResourceHealth(
                name=self.name,
                state=ResourceState.DEGRADED,
                reason="Compiler loaded but 0 charts found",
                category=ErrorCategory.CONFIGURATION,
                required=False,
            )
        return ResourceHealth(name=self.name, state=ResourceState.READY, required=False)
