"""Registry — capability discovery and publishing.

The Registry is optional. Only the protocol is mandatory.

Supports:
- Local: in-memory, single process
- Enterprise: shared, multi-process
- Public: community, discoverable
- Offline: cached, no network
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from cerebellum.capability import CapabilityManifest


@dataclass
class RegistryQuery:
    """Query for capability discovery."""

    name: str | None = None
    tags: list[str] | None = None
    peripheral_type: str | None = None
    author: str | None = None
    min_version: str | None = None
    limit: int = 10


class Registry:
    """Capability registry protocol.

    Responsibilities:
    - Publish capabilities
    - Discover capabilities
    - Resolve capabilities by ID
    - Remove capabilities

    The Registry never:
    - Executes capabilities
    - Makes execution decisions
    - Manages connections
    """

    def __init__(self):
        self._capabilities: dict[str, CapabilityManifest] = {}

    async def publish(self, manifest: CapabilityManifest) -> str:
        """Publish a capability to the registry.

        Returns: capability_id
        """
        if not manifest.capability_id:
            manifest.capability_id = f"cap-{uuid4().hex[:8]}"
        if not manifest.published_at:
            manifest.published_at = datetime.now(timezone.utc).isoformat()

        self._capabilities[manifest.capability_id] = manifest
        return manifest.capability_id

    async def discover(self, query: RegistryQuery) -> list[CapabilityManifest]:
        """Discover capabilities matching query."""
        results = list(self._capabilities.values())

        if query.name:
            results = [m for m in results if query.name.lower() in m.name.lower()]

        if query.tags:
            results = [m for m in results if any(t in m.tags for t in query.tags)]

        if query.peripheral_type:
            results = [m for m in results if m.peripheral_type == query.peripheral_type]

        if query.author:
            results = [m for m in results if m.author == query.author]

        return results[: query.limit]

    async def resolve(self, capability_id: str) -> CapabilityManifest | None:
        """Resolve a capability by ID."""
        return self._capabilities.get(capability_id)

    async def remove(self, capability_id: str) -> bool:
        """Remove a capability from registry."""
        if capability_id in self._capabilities:
            del self._capabilities[capability_id]
            return True
        return False

    def count(self) -> int:
        return len(self._capabilities)

    def list_all(self) -> list[CapabilityManifest]:
        return list(self._capabilities.values())
