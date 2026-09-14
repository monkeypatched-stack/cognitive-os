"""Secure keystore for API keys."""

from __future__ import annotations
import hashlib
import json
import os
from typing import Any
from uuid import uuid4


class SecureKeystore:
    def __init__(self, master_key: str = "", db_path: str = ".keystore.json"):
        self._db_path = db_path
        self._master_key = (master_key or os.getenv("MASTER_KEY", "default")).encode()
        self._keys: dict[str, dict] = {}
        self._load()

    def _derive_key(self) -> bytes:
        return hashlib.sha256(self._master_key).digest()[:32]

    def _load(self):
        if os.path.exists(self._db_path):
            try:
                with open(self._db_path) as f:
                    self._keys = json.load(f)
            except Exception:
                pass

    def _save(self):
        os.makedirs(
            os.path.dirname(self._db_path) if os.path.dirname(self._db_path) else ".",
            exist_ok=True,
        )
        with open(self._db_path, "w") as f:
            json.dump(self._keys, f)

    def add_key(self, user_id: str, service: str, api_key: str, **kwargs) -> dict:
        key_id = f"key-{uuid4().hex[:8]}"
        self._keys[key_id] = {
            "user_id": user_id,
            "service": service,
            "key_hash": hashlib.sha256(api_key.encode()).hexdigest()[:16],
            **kwargs,
        }
        self._save()
        return {"key_id": key_id, "service": service}

    def get_config(self, service: str, user_id: str) -> dict[str, Any]:
        for key in self._keys.values():
            if key.get("service") == service and key.get("user_id") == user_id:
                return key
        return {}

    def list_keys(self, user_id: str) -> list[dict]:
        return [
            {"key_id": k.get("key_id"), "service": k.get("service")}
            for k in self._keys.values()
            if k.get("user_id") == user_id
        ]

    def summary(self) -> dict:
        return {"total_keys": len(self._keys)}
