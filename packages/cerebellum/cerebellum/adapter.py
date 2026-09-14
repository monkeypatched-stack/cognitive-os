"""Adapter — transforms data between capability and peripheral.

Adapters handle:
- Data format conversion
- Protocol translation
- Schema mapping
- Error transformation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from cerebellum.peripheral import Peripheral


@dataclass
class AdapterSchema:
    """Schema for adapter data transformation."""

    name: str = ""
    fields: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Adapter:
    """Transforms data between capability and peripheral.

    Responsibilities:
    - Read from peripheral and transform
    - Transform and write to peripheral
    - Map data schemas

    The Adapter never:
    - Makes execution decisions
    - Manages connections
    - Owns execution policy
    """

    def __init__(
        self,
        name: str,
        peripheral: Peripheral,
        schema: AdapterSchema | None = None,
    ):
        self.name = name
        self.peripheral = peripheral
        self.schema = schema or AdapterSchema()
        self._id = f"adapter-{uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._id

    async def read(self, query: dict[str, Any]) -> dict[str, Any]:
        """Read from peripheral and transform.

        Override this method to implement read logic.
        """
        raise NotImplementedError(f"Adapter '{self.name}' must implement read()")

    async def write(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform and write to peripheral.

        Override this method to implement write logic.
        """
        raise NotImplementedError(f"Adapter '{self.name}' must implement write()")

    async def transform(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform data without I/O.

        Override this method to implement transformation logic.
        """
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "name": self.name,
            "peripheral": self.peripheral.name,
            "schema": self.schema.name,
        }
