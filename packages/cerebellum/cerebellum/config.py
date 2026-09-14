"""Capability Configuration — API keys and settings for all capabilities.

Centralizes configuration for all capabilities.
Supports environment variables, config files, and runtime configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityConfig:
    """Configuration for a single capability."""

    name: str
    enabled: bool = True
    api_key: str = ""
    api_url: str = ""
    timeout_ms: int = 5000
    max_retries: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)


class ConfigManager:
    """Manages configuration for all capabilities.

    Supports:
    - Environment variables
    - Config file
    - Runtime configuration
    """

    def __init__(self):
        self._configs: dict[str, CapabilityConfig] = {}
        self._env_prefix = "AGENTOS_"

    def load_from_env(self) -> None:
        """Load configuration from environment variables."""
        # API Keys
        self._configs["openai"] = CapabilityConfig(
            name="openai",
            api_key=os.getenv("OPENAI_API_KEY", ""),
            api_url=os.getenv("OPENAI_API_URL", "https://api.openai.com/v1"),
        )

        self._configs["anthropic"] = CapabilityConfig(
            name="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            api_url=os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1"),
        )

        self._configs["openrouter"] = CapabilityConfig(
            name="openrouter",
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            api_url=os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1"),
        )

        self._configs["ollama"] = CapabilityConfig(
            name="ollama",
            api_url=os.getenv("OLLAMA_API_URL", "http://localhost:11434"),
        )

        self._configs["gemini"] = CapabilityConfig(
            name="gemini",
            api_key=os.getenv("GOOGLE_API_KEY", ""),
        )

        self._configs["azure_openai"] = CapabilityConfig(
            name="azure_openai",
            api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            api_url=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        )

        self._configs["aws_bedrock"] = CapabilityConfig(
            name="aws_bedrock",
            api_key=os.getenv("AWS_ACCESS_KEY_ID", ""),
        )

        # Databases
        self._configs["mongodb"] = CapabilityConfig(
            name="mongodb",
            api_url=os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
            metadata={"database": os.getenv("DB_NAME", "agentos")},
        )

        self._configs["redis"] = CapabilityConfig(
            name="redis",
            api_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        )

        self._configs["neo4j"] = CapabilityConfig(
            name="neo4j",
            api_url=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            api_key=os.getenv("NEO4J_PASSWORD", ""),
        )

        self._configs["influxdb"] = CapabilityConfig(
            name="influxdb",
            api_key=os.getenv("INFLUXDB_TOKEN", ""),
            api_url=os.getenv("INFLUXDB_URL", "http://localhost:8086"),
            metadata={"org": os.getenv("INFLUXDB_ORG", "agentos")},
        )

        self._configs["elasticsearch"] = CapabilityConfig(
            name="elasticsearch",
            api_url=os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"),
        )

        # Communication
        self._configs["slack"] = CapabilityConfig(
            name="slack",
            api_key=os.getenv("SLACK_WEBHOOK_URL", ""),
        )

        self._configs["email"] = CapabilityConfig(
            name="email",
            api_key=os.getenv("SMTP_PASSWORD", ""),
            api_url=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        )

        # Cloud
        self._configs["aws"] = CapabilityConfig(
            name="aws",
            api_key=os.getenv("AWS_ACCESS_KEY_ID", ""),
            metadata={"region": os.getenv("AWS_REGION", "us-east-1")},
        )

        self._configs["azure"] = CapabilityConfig(
            name="azure",
            api_key=os.getenv("AZURE_SUBSCRIPTION_ID", ""),
        )

        self._configs["google_cloud"] = CapabilityConfig(
            name="google_cloud",
            api_key=os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        )

    def get(self, capability_name: str) -> CapabilityConfig | None:
        """Get configuration for a capability."""
        return self._configs.get(capability_name)

    def set(self, capability_name: str, config: CapabilityConfig) -> None:
        """Set configuration for a capability."""
        self._configs[capability_name] = config

    def get_all(self) -> dict[str, CapabilityConfig]:
        return dict(self._configs)

    def summary(self) -> dict:
        return {
            "total_configs": len(self._configs),
            "configured": [name for name, config in self._configs.items() if config.api_key or config.api_url],
            "not_configured": [
                name for name, config in self._configs.items() if not config.api_key and not config.api_url
            ],
        }
