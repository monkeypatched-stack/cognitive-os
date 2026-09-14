"""
Configuration Manager
=====================

Loads adapter configuration from YAML, resolves ${ENV_VAR} placeholders,
applies environment overrides, and supports runtime reload.

YAML structure
--------------

.. code-block:: yaml

    sdk:
      log_level: INFO
      health_check_interval: 30
      telemetry_exporter: prometheus   # logging | prometheus | influx | jaeger

    adapters:

      cmms:
        enabled: true
        endpoint: https://cmms.example.com/api
        credentials:
          api_key: ${CMMS_API_KEY}
          username: ${CMMS_USER}
        timeout: 45
        retry_count: 3
        extra:
          work_order_prefix: WO

      sap:
        enabled: true
        endpoint: ${SAP_ENDPOINT}
        credentials:
          oauth_endpoint: https://sap.example.com/oauth
          client_id: ${SAP_CLIENT_ID}
          client_secret: ${SAP_CLIENT_SECRET}
        timeout: 60

Environment overrides
---------------------

Any adapter field can be overridden at runtime:

    ADAPTER_CMMS_ENDPOINT=https://new-cmms.example.com
    ADAPTER_CMMS_TIMEOUT=60
    SDK_LOG_LEVEL=DEBUG
"""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import yaml

from ..contracts.exceptions import ConfigurationError
from ..contracts.interfaces import IConfigurationManager

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


# ---------------------------------------------------------------------------
# Environment-variable interpolation
# ---------------------------------------------------------------------------


def _interpolate(value: Any) -> Any:
    """
    Recursively replace ``${VAR}`` placeholders with environment variable
    values.  Unknown variables are left as-is (the literal ``${VAR}``
    string), making misconfiguration easy to spot in logs.
    """
    if isinstance(value, str):

        def _replace(match: re.Match) -> str:
            env_var = match.group(1)
            resolved = os.environ.get(env_var)
            if resolved is None:
                logger.debug("Environment variable '%s' not set.", env_var)
                return match.group(0)
            return resolved

        return _ENV_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_interpolate(item) for item in value]

    return value


# ---------------------------------------------------------------------------
# AdapterConfig
# ---------------------------------------------------------------------------


@dataclass
class AdapterConfig:
    """
    Parsed configuration for a single adapter.

    The ``get()`` helper checks in order:
    1. Environment variable  ``ADAPTER_<NAME>_<KEY>``  (upper-cased)
    2. The ``extra`` dict
    3. The provided default
    """

    name: str
    enabled: bool = True
    endpoint: Optional[str] = None
    credentials: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    retry_count: int = 3
    extra: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a configuration value with environment-override support.

        Environment variable format:  ``ADAPTER_<NAME>_<KEY>``
        Example:                      ``ADAPTER_CMMS_TIMEOUT=60``
        """
        env_key = f"ADAPTER_{self.name.upper()}_{key.upper()}"
        env_value = os.environ.get(env_key)
        if env_value is not None:
            return env_value

        if key in self.extra:
            return self.extra[key]

        # Also check top-level fields
        if hasattr(self, key):
            attr = getattr(self, key)
            if attr is not None:
                return attr

        return default

    def require(self, key: str) -> Any:
        """Return value or raise ConfigurationError if absent."""
        value = self.get(key)
        if value is None:
            raise ConfigurationError(
                f"Adapter '{self.name}' requires config key '{key}' "
                f"but it was not set. "
                f"Set ADAPTER_{self.name.upper()}_{key.upper()} or add it to config.yaml."
            )
        return value


# ---------------------------------------------------------------------------
# ConfigurationManager
# ---------------------------------------------------------------------------


class ConfigurationManager(IConfigurationManager):
    """
    Loads and manages SDK + adapter configuration.

    Implements IConfigurationManager interface for dependency injection.

    Features
    --------
    - YAML file loading
    - ``${ENV_VAR}`` interpolation
    - Per-adapter environment override (``ADAPTER_<NAME>_<KEY>``)
    - Runtime reload via ``reload()``
    - Validation
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path = config_path
        self._raw: Dict[str, Any] = {}
        self._sdk_config: Dict[str, Any] = {}
        self._adapters: Dict[str, AdapterConfig] = {}

        if config_path is not None:
            self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load and parse the YAML configuration file."""
        if self.config_path is None:
            return

        try:
            with open(self.config_path, "r") as fh:
                raw = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            raise ConfigurationError(f"Configuration file not found: {self.config_path}")
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Failed to parse configuration file {self.config_path}: {exc}")

        # Interpolate environment variables throughout the entire config
        self._raw = _interpolate(raw)

        # Parse SDK-level settings
        self._sdk_config = self._raw.get("sdk", {})
        self._apply_sdk_env_overrides()

        # Parse adapter configs
        self._adapters = {}
        for name, adapter_raw in self._raw.get("adapters", {}).items():
            if adapter_raw is None:
                adapter_raw = {}
            self._adapters[name] = AdapterConfig(
                name=name,
                enabled=adapter_raw.get("enabled", True),
                endpoint=adapter_raw.get("endpoint"),
                credentials=adapter_raw.get("credentials") or {},
                timeout=int(adapter_raw.get("timeout", 30)),
                retry_count=int(adapter_raw.get("retry_count", 3)),
                extra=adapter_raw.get("extra") or {},
            )

        logger.info(
            "Configuration loaded from '%s' (%d adapter(s)).",
            self.config_path,
            len(self._adapters),
        )

    def _apply_sdk_env_overrides(self) -> None:
        """Apply SDK-level environment overrides (``SDK_<KEY>``)."""
        for key in ("log_level", "health_check_interval", "telemetry_exporter"):
            env_key = f"SDK_{key.upper()}"
            if env_key in os.environ:
                self._sdk_config[key] = os.environ[env_key]

    def load_from_dict(self, config: Dict[str, Any]) -> None:
        """
        Load configuration from a Python dict (useful in tests).

        The dict undergoes the same interpolation as a YAML file.
        """
        self._raw = _interpolate(config)
        self._sdk_config = self._raw.get("sdk", {})
        self._adapters = {}
        for name, adapter_raw in self._raw.get("adapters", {}).items():
            if adapter_raw is None:
                adapter_raw = {}
            self._adapters[name] = AdapterConfig(
                name=name,
                enabled=adapter_raw.get("enabled", True),
                endpoint=adapter_raw.get("endpoint"),
                credentials=adapter_raw.get("credentials") or {},
                timeout=int(adapter_raw.get("timeout", 30)),
                retry_count=int(adapter_raw.get("retry_count", 3)),
                extra=adapter_raw.get("extra") or {},
            )

    def reload(self) -> None:
        """Re-read the configuration file from disk."""
        logger.info("Reloading configuration from '%s'.", self.config_path)
        self._load()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_adapter_config(self, adapter_name: str) -> Optional[AdapterConfig]:
        """Return config for the named adapter, or None."""
        return self._adapters.get(adapter_name)

    def get_all_configs(self) -> Dict[str, AdapterConfig]:
        """Return a copy of all adapter configs."""
        return dict(self._adapters)

    def get_sdk_setting(self, key: str, default: Any = None) -> Any:
        """Return a top-level SDK setting."""
        return self._sdk_config.get(key, default)

    @property
    def log_level(self) -> str:
        return str(self._sdk_config.get("log_level", "INFO")).upper()

    @property
    def health_check_interval(self) -> int:
        return int(self._sdk_config.get("health_check_interval", 30))

    # ------------------------------------------------------------------
    # IConfigurationManager interface implementation
    # ------------------------------------------------------------------

    def load_configuration(self, adapter_id: str) -> Dict[str, Any]:
        """Load configuration for a specific adapter (interface method)."""
        adapter_config = self.get_adapter_config(adapter_id)
        if adapter_config is None:
            return {}
        return {
            "name": adapter_config.name,
            "enabled": adapter_config.enabled,
            "endpoint": adapter_config.endpoint,
            "credentials": adapter_config.credentials,
            "timeout": adapter_config.timeout,
            "retry_count": adapter_config.retry_count,
            **adapter_config.extra,
        }

    def validate_configuration(self, config: Dict[str, Any]) -> bool:
        """Validate adapter configuration (interface method)."""
        try:
            if not isinstance(config, dict):
                return False
            if "enabled" not in config:
                return False
            return True
        except Exception:
            return False

    def get_global_config(self) -> Dict[str, Any]:
        """Get global SDK configuration (interface method)."""
        return dict(self._sdk_config)

    @property
    def telemetry_exporter(self) -> str:
        return str(self._sdk_config.get("telemetry_exporter", "logging"))
