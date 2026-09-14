"""Capability Lifecycle — uniform lifecycle for all capabilities.

Every capability must implement:
Initialize → Configure → Validate → Register → Discover → Execute → Observe → Shutdown
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class CapabilityState(str, Enum):
    """Capability lifecycle states."""

    CREATED = "created"
    INITIALIZED = "initialized"
    CONFIGURED = "configured"
    VALIDATED = "validated"
    REGISTERED = "registered"
    READY = "ready"
    EXECUTING = "executing"
    OBSERVING = "observing"
    SHUTDOWN = "shutdown"
    ERROR = "error"


class ICapabilityLifecycle(ABC):
    """Interface for capability lifecycle management."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the capability."""
        ...

    @abstractmethod
    async def configure(self, config: dict[str, Any]) -> None:
        """Configure the capability."""
        ...

    @abstractmethod
    async def validate(self) -> bool:
        """Validate the capability configuration."""
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check capability health."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the capability."""
        ...
