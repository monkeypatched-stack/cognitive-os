"""Minimal CapabilityBus stub for cognitive kernel wiring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CapabilityResult:
    """Standardised result envelope from capability execution."""

    name: str
    success: bool
    produced: dict[str, Any] = field(default_factory=dict)
    events_emitted: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


class CapabilityBus:
    """Minimal CapabilityBus stub for cognitive kernel.

    Capabilities can declare required_permissions — a set of permission
    strings that must be present in the execution context for the
    capability to run.  Missing permissions cause a denied result instead
    of execution.
    """

    def __init__(self):
        self._capabilities = {}
        self._mesh = None

    def register_capability(self, capability: Any) -> None:
        """Register a capability with the bus."""
        name = getattr(capability, "name", str(capability))
        self._capabilities[name] = capability

    def set_mesh(self, mesh: Any) -> None:
        """Set the agent mesh for capability execution."""
        self._mesh = mesh

    def get_permissions(self, capability_name: str) -> set[str]:
        """Return the required permissions for a capability, or empty set."""
        cap = self._capabilities.get(capability_name)
        if cap is None:
            return set()
        return set(getattr(cap, "required_permissions", []))

    def check_authorization(self, capability_name: str, granted: set[str]) -> tuple[bool, str]:
        """Check if granted permissions satisfy the capability's requirements.

        Returns (allowed, reason).
        """
        required = self.get_permissions(capability_name)
        if not required:
            return True, ""
        missing = required - granted
        if missing:
            return False, f"missing permissions: {', '.join(sorted(missing))}"
        return True, ""

    async def execute(self, capability_name: str, *, context: Any = None, **kwargs: Any) -> CapabilityResult:
        """Execute a capability by name, checking authorization first."""
        import time

        # Authorization check — runs ALWAYS. Previously it was gated on
        # `if context is not None`, so any caller that omitted a context (e.g. the graph
        # scheduler) skipped the permission check entirely: authorization failed OPEN.
        # With no context the caller holds NO permissions, so a capability that declares
        # required_permissions is denied; capabilities that declare none still pass.
        granted = set(getattr(context, "permissions", [])) if context is not None else set()
        allowed, reason = self.check_authorization(capability_name, granted)
        if not allowed:
            logger.warning("Capability '%s' denied: %s", capability_name, reason)
            return CapabilityResult(
                name=capability_name,
                success=False,
                produced={"error": "authorization_denied", "detail": reason},
            )

        capability = self._capabilities.get(capability_name)
        if capability is None:
            return CapabilityResult(
                name=capability_name,
                success=False,
                produced={"error": "capability_not_found"},
                latency_ms=0.0,
            )

        t0 = time.monotonic()
        try:
            fn = getattr(capability, "fn", None)
            if callable(fn):
                produced = fn(kwargs)
            elif callable(capability):
                produced = capability(kwargs)
            else:
                produced = {"error": "no_callable"}
            latency = (time.monotonic() - t0) * 1000
            return CapabilityResult(
                name=capability_name,
                success=True,
                produced=(produced if isinstance(produced, dict) else {"result": produced}),
                latency_ms=latency,
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            return CapabilityResult(
                name=capability_name,
                success=False,
                produced={"error": str(e)},
                latency_ms=latency,
            )

    def summary(self) -> dict[str, Any]:
        """Get bus summary."""
        return {
            "capabilities_registered": len(self._capabilities),
            "mesh_attached": self._mesh is not None,
        }


__all__ = ["CapabilityBus", "CapabilityResult"]
