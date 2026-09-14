"""Graceful Degradation — Phase 5 Production Hardening.

Allows system to continue operating with reduced capabilities when components fail.

Strategy:
- Core functionality always available
- Optional features degrade independently
- Fallback behaviors when primary unavailable
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Callable, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("agentos.degradation")


class CapabilityState(Enum):
    """State of a capability."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass
class CapabilityInfo:
    """Information about a capability."""

    name: str
    state: CapabilityState
    message: str = ""
    fallback_available: bool = False


class DegradationManager:
    """Manages graceful degradation of optional features.

    Capabilities are registered with primary and fallback implementations.
    If primary fails, fallback is used automatically.
    If fallback also fails, feature marked unavailable.
    """

    def __init__(self) -> None:
        self.capabilities: dict[str, CapabilityInfo] = {}
        self.primary: dict[str, Callable] = {}
        self.fallback: dict[str, Callable] = {}
        self.failure_count: dict[str, int] = {}

    def register_capability(self, name: str, primary: Callable, fallback: Optional[Callable] = None) -> None:
        """Register a capability with optional fallback.

        Args:
            name: Capability name
            primary: Primary implementation
            fallback: Fallback implementation (optional)
        """
        self.capabilities[name] = CapabilityInfo(
            name=name,
            state=CapabilityState.AVAILABLE,
            fallback_available=fallback is not None,
        )
        self.primary[name] = primary
        if fallback:
            self.fallback[name] = fallback
        self.failure_count[name] = 0

        logger.info(
            "[degradation] Registered capability: %s (fallback=%s)",
            name,
            fallback is not None,
        )

    def call(self, name: str, *args, **kwargs) -> Any:
        """Call a capability with automatic fallback.

        Args:
            name: Capability name
            *args, **kwargs: Arguments for the capability

        Returns:
            Result from primary or fallback

        Raises:
            RuntimeError: If capability unavailable and no fallback
        """
        if name not in self.capabilities:
            raise RuntimeError(f"[degradation] Unknown capability: {name}")

        info = self.capabilities[name]

        # Try primary
        if info.state in [CapabilityState.AVAILABLE, CapabilityState.DEGRADED]:
            try:
                result = self.primary[name](*args, **kwargs)
                self.failure_count[name] = 0
                if info.state == CapabilityState.DEGRADED:
                    info.state = CapabilityState.AVAILABLE
                    logger.info("[degradation] %s recovered to AVAILABLE", name)
                return result
            except Exception as exc:
                self.failure_count[name] += 1
                logger.warning(
                    "[degradation] %s failed (attempt %d): %s",
                    name,
                    self.failure_count[name],
                    exc,
                )
                info.state = CapabilityState.DEGRADED
                info.message = str(exc)

        # Try fallback
        if name in self.fallback:
            try:
                result = self.fallback[name](*args, **kwargs)
                logger.warning("[degradation] %s using fallback", name)
                return result
            except Exception as exc:
                logger.error("[degradation] %s fallback failed: %s", name, exc)
                info.state = CapabilityState.UNAVAILABLE
                info.message = f"Primary and fallback failed: {exc}"

        # No fallback available
        raise RuntimeError(f"[degradation] Capability {name} unavailable. Message: {info.message}")

    def status(self) -> dict[str, Any]:
        """Get degradation status of all capabilities."""
        return {
            "capabilities": {
                name: {
                    "state": info.state.value,
                    "message": info.message,
                    "fallback_available": info.fallback_available,
                    "failures": self.failure_count.get(name, 0),
                }
                for name, info in self.capabilities.items()
            },
            "available_count": sum(1 for i in self.capabilities.values() if i.state == CapabilityState.AVAILABLE),
            "degraded_count": sum(1 for i in self.capabilities.values() if i.state == CapabilityState.DEGRADED),
            "unavailable_count": sum(1 for i in self.capabilities.values() if i.state == CapabilityState.UNAVAILABLE),
        }


# Singleton instance
_degradation_manager: DegradationManager | None = None


def get_degradation_manager() -> DegradationManager:
    """Get or create singleton degradation manager."""
    global _degradation_manager
    if _degradation_manager is None:
        _degradation_manager = DegradationManager()
    return _degradation_manager


def reset_degradation_manager() -> None:
    """Reset degradation manager (for testing)."""
    global _degradation_manager
    _degradation_manager = None
