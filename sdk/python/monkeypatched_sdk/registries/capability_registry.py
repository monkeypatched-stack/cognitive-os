"""Capability Registry for SDK.

Adapters register which capabilities they support.
The runtime queries this to find adapters for a given capability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AdapterInfo:
    adapter_id: str
    name: str
    capability: str
    backend: str
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    """Global registry of adapter capabilities.

    Adapters register at startup. Runtime queries at execution time.
    """

    _instance: "CapabilityRegistry | None" = None
    _adapters: dict[str, AdapterInfo] = {}

    @classmethod
    def instance(cls) -> "CapabilityRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(
        self,
        adapter_id: str,
        name: str,
        capability: str,
        backend: str,
        priority: int = 0,
        **metadata: Any,
    ) -> None:
        key = f"{adapter_id}:{capability}"
        self._adapters[key] = AdapterInfo(
            adapter_id=adapter_id,
            name=name,
            capability=capability,
            backend=backend,
            priority=priority,
            metadata=metadata,
        )
        logger.info(
            "Adapter %s registered capability '%s' (backend=%s)",
            adapter_id,
            capability,
            backend,
        )

    def unregister(self, adapter_id: str, capability: str | None = None) -> None:
        keys = [k for k in self._adapters if k.startswith(f"{adapter_id}:")]
        for k in keys:
            del self._adapters[k]

    def find_adapters_for_capability(self, capability: str) -> list[AdapterInfo]:
        return sorted(
            [a for a in self._adapters.values() if a.capability == capability],
            key=lambda a: a.priority,
            reverse=True,
        )

    def all_capabilities(self) -> list[str]:
        return list(set(a.capability for a in self._adapters.values()))

    def summary(self) -> dict[str, Any]:
        caps: dict[str, list] = {}
        for a in self._adapters.values():
            caps.setdefault(a.capability, []).append(
                {
                    "adapter_id": a.adapter_id,
                    "backend": a.backend,
                    "priority": a.priority,
                }
            )
        return {"capabilities": caps}
