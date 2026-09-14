"""Capability Metadata — describes a capability's properties.

Every capability must expose metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityMetadata:
    """Metadata for a capability."""

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    category: str = ""  # api | database | ai | workflow | storage | communication
    tags: list[str] = field(default_factory=list)

    # Capabilities
    supported_inputs: list[str] = field(default_factory=list)
    supported_outputs: list[str] = field(default_factory=list)
    supported_protocols: list[str] = field(default_factory=list)

    # Dependencies
    required_permissions: list[str] = field(default_factory=list)
    required_secrets: list[str] = field(default_factory=list)
    required_config: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    # Health
    health_check_endpoint: str = ""

    # Versioning
    semantic_version: str = "1.0.0"
    compatibility_version: str = "1.0"
    deprecation_date: str | None = None

    # Documentation
    documentation_url: str = ""
    examples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "tags": self.tags,
            "supported_inputs": self.supported_inputs,
            "supported_outputs": self.supported_outputs,
            "supported_protocols": self.supported_protocols,
            "required_permissions": self.required_permissions,
            "required_secrets": self.required_secrets,
            "required_config": self.required_config,
            "dependencies": self.dependencies,
        }
