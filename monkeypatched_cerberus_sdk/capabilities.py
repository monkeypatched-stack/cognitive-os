"""Capability base classes."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field


@dataclass
class CapabilityMetadata:
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)


class Capability(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(self, state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        pass

    def can_execute(self, state: dict[str, Any]) -> bool:
        return True

    def estimate_cost(self, state: dict[str, Any]) -> float:
        return 1.0

    def estimate_reward(self, state: dict[str, Any]) -> float:
        return 0.5
