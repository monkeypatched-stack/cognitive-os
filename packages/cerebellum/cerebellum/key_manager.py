"""Key Manager — user-managed API keys and configuration.

Users manage their own keys through the API.
Keys are stored securely in the local database.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class ApiKey:
    """An API key record."""

    key_id: str = field(default_factory=lambda: f"key-{uuid4().hex[:8]}")
    service: str = ""
    key_name: str = ""
    key_hash: str = ""
    api_url: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used: str = ""
    is_active: bool = True


class KeyManager:
    """User-managed API key storage.

    Keys are stored in the local database.
    Users manage their own keys through the API.
    """

    def __init__(self, db_path: str = ".monkeybrain/keys.json"):
        self._db_path = db_path
        self._keys: dict[str, ApiKey] = {}
        self._load()

    def _load(self) -> None:
        """Load keys from disk."""
        if os.path.exists(self._db_path):
            try:
                with open(self._db_path, "r") as f:
                    data = json.load(f)
                for key_data in data.get("keys", []):
                    key = ApiKey(**key_data)
                    self._keys[key.key_id] = key
            except Exception:
                pass

    def _save(self) -> None:
        """Save keys to disk."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with open(self._db_path, "w") as f:
            json.dump(
                {
                    "keys": [
                        {
                            "key_id": k.key_id,
                            "service": k.service,
                            "key_name": k.key_name,
                            "key_hash": k.key_hash,
                            "api_url": k.api_url,
                            "config": k.config,
                            "created_at": k.created_at,
                            "is_active": k.is_active,
                        }
                        for k in self._keys.values()
                    ]
                },
                f,
                indent=2,
            )

    def add_key(
        self,
        service: str,
        key_name: str,
        api_key: str,
        api_url: str = "",
        config: dict[str, Any] | None = None,
    ) -> ApiKey:
        """Add a new API key."""
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]

        api_key_record = ApiKey(
            service=service,
            key_name=key_name,
            key_hash=key_hash,
            api_url=api_url,
            config=config or {},
        )

        self._keys[api_key_record.key_id] = api_key_record
        self._save()
        return api_key_record

    def get_key(self, key_id: str) -> ApiKey | None:
        return self._keys.get(key_id)

    def get_keys_by_service(self, service: str) -> list[ApiKey]:
        return [k for k in self._keys.values() if k.service == service and k.is_active]

    def get_all_keys(self) -> list[ApiKey]:
        return list(self._keys.values())

    def remove_key(self, key_id: str) -> bool:
        if key_id in self._keys:
            del self._keys[key_id]
            self._save()
            return True
        return False

    def get_config_for_service(self, service: str) -> dict[str, Any]:
        """Get merged config for a service."""
        keys = self.get_keys_by_service(service)
        if not keys:
            return {}

        config = {}
        for key in keys:
            config.update(key.config)
            if key.api_url:
                config["api_url"] = key.api_url
        return config

    def summary(self) -> dict:
        by_service = {}
        for k in self._keys.values():
            by_service[k.service] = by_service.get(k.service, 0) + 1
        return {
            "total_keys": len(self._keys),
            "by_service": by_service,
        }
