"""Capability registry."""

from __future__ import annotations
from typing import Any
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class RegistryQuery:
    name: str | None = None
    tags: list[str] | None = None
    category: str | None = None
    limit: int = 10


class Registry:
    def __init__(self):
        self._capabilities: dict[str, Any] = {}

    async def publish(self, manifest: dict[str, Any]) -> str:
        cap_id = manifest.get("capability_id", f"cap-{uuid4().hex[:8]}")
        self._capabilities[cap_id] = manifest
        return cap_id

    async def discover(self, query: RegistryQuery) -> list[dict[str, Any]]:
        results = list(self._capabilities.values())
        if query.name:
            results = [r for r in results if query.name in r.get("name", "")]
        return results[: query.limit]

    async def resolve(self, capability_id: str) -> dict[str, Any] | None:
        return self._capabilities.get(capability_id)

    async def remove(self, capability_id: str) -> bool:
        if capability_id in self._capabilities:
            del self._capabilities[capability_id]
            return True
        return False

    def summary(self) -> dict:
        return {"total": len(self._capabilities)}
