"""Capability — a unit of work that transforms execution state.

Capabilities are the execution primitives.
The Runtime executes capabilities.
The Policy selects capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from cerebellum.peripheral import Peripheral


@dataclass
class CapabilityManifest:
    """Capability metadata for registry."""

    capability_id: str = ""
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    peripheral_type: str | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    checksum: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Capability:
    """A unit of work that transforms execution state.

    Responsibilities:
    - Execute a specific operation
    - Read from/write to peripherals
    - Return structured results

    The Capability never:
    - Makes execution decisions
    - Selects other capabilities
    - Manages threads
    - Owns execution policy
    """

    def __init__(
        self,
        name: str,
        peripheral: Peripheral | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.name = name
        self.peripheral = peripheral
        self.config = config or {}
        self._id = f"cap-{uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._id

    async def execute(
        self,
        state: dict[str, Any],
        context: Any = None,
    ) -> dict[str, Any]:
        """Execute the capability.

        Args:
            state: Current execution state (read-only)
            context: Runtime context (memory, stream)

        Returns:
            Result dict with capability output
        """
        raise NotImplementedError(f"Capability '{self.name}' must implement execute()")

    def can_execute(self, state: dict[str, Any]) -> bool:
        """Check if capability can execute."""
        return True

    def estimate_cost(self, state: dict[str, Any]) -> float:
        """Estimate execution cost."""
        return 1.0

    def estimate_reward(self, state: dict[str, Any]) -> float:
        """Estimate expected reward."""
        return 0.5

    def manifest(self) -> CapabilityManifest:
        """Generate manifest for registry."""
        return CapabilityManifest(
            capability_id=self._id,
            name=self.name,
            description=f"Capability: {self.name}",
            peripheral_type=self.peripheral.type.value if self.peripheral else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "name": self.name,
            "peripheral": self.peripheral.name if self.peripheral else None,
        }
