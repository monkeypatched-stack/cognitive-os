"""Secure Keystore — user-scrypted API key management.

Each user has their own isolated key store.
Capabilities read keys from the keystore by user.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _generate_key() -> bytes:
    from cryptography.fernet import Fernet

    return Fernet.generate_key()


def _encrypt(data: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    return Fernet(key).encrypt(data.encode()).decode()


def _decrypt(encrypted: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    return Fernet(key).decrypt(encrypted.encode()).decode()


@dataclass
class SecureKey:
    """An encrypted API key record."""

    key_id: str = field(default_factory=lambda: f"key-{uuid4().hex[:12]}")
    user_id: str = ""
    service: str = ""
    key_name: str = ""
    encrypted_key: str = ""
    api_url: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True

    def to_public(self) -> dict:
        return {
            "key_id": self.key_id,
            "user_id": self.user_id,
            "service": self.service,
            "key_name": self.key_name,
            "api_url": self.api_url,
            "created_at": self.created_at,
            "is_active": self.is_active,
        }


class SecureKeystore:
    """User-scoped encrypted API key storage."""

    def __init__(self, master_key: str | None = None, db_path: str = ".monkeybrain/keystore"):
        self._db_path = db_path
        self._master_key = (master_key or os.getenv("AGENTOS_MASTER_KEY", "")).encode()
        if not self._master_key:
            # Previously this silently generated a RANDOM, non-persisted master key. Anything
            # encrypted in that session became permanently undecryptable on the next restart
            # (a fresh random key) — silent, irreversible data loss. The live keystore route
            # constructs SecureKeystore() with no key, so that was the production path.
            if os.getenv("AGENTOS_KEYSTORE_EPHEMERAL", "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            ):
                self._master_key = _generate_key()
            else:
                raise RuntimeError(
                    "SecureKeystore: no master key. Set AGENTOS_MASTER_KEY (from your secrets "
                    "manager) — an auto-generated key is not persisted, so every stored key "
                    "would become undecryptable on restart. Set AGENTOS_KEYSTORE_EPHEMERAL=1 "
                    "only for throwaway/dev stores."
                )
        self._kdf_lock = threading.Lock()  # guards salt creation + key derivation
        self._salt_cache: bytes | None = None
        self._derived_key: bytes | None = None
        self._keys: dict[str, SecureKey] = {}
        self._load()

    # ── key derivation ──────────────────────────────────────────────────────────────
    # The docstring promised "scrypt"; the code ran a single UNSALTED sha256 over the master
    # key. A passphrase-derived key with one unsalted hash is cheap to brute-force offline if
    # the keystore file leaks, and identical master keys derive identical keys across installs.
    # Now: scrypt with a persisted random salt, with the legacy derivation kept for reading
    # existing ciphertext (transparently re-encrypted on next write).

    def _salt_path(self) -> str:
        return f"{self._db_path}.salt"

    def _salt(self) -> bytes:
        """The keystore's salt, created exactly once — safely, under concurrency.

        Two hazards, both of which produced InvalidToken on decrypt:
          * two callers each generate a DIFFERENT salt and race the write;
          * a plain O_EXCL create publishes the file EMPTY and fills it afterwards, so a
            racing reader can read zero bytes and derive a key from an empty salt.

        So: serialise with a lock (threads in this process), and publish the salt with an
        ATOMIC RENAME (other processes never observe a partial file). A loser of the rename
        race simply reads the winner's salt.
        """
        with self._kdf_lock:
            if self._salt_cache is not None:
                return self._salt_cache
            path = self._salt_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

            if not os.path.exists(path):
                salt = os.urandom(16)
                tmp = f"{path}.tmp.{os.getpid()}"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    os.write(fd, salt)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.link(tmp, path)  # atomic create-if-absent; fails if it now exists
                except FileExistsError:
                    pass  # someone else published first — use theirs
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)

            with open(path, "rb") as fh:  # always read back the PUBLISHED salt
                salt = fh.read()
            if not salt:
                raise RuntimeError(f"keystore salt at {path} is empty")
            self._salt_cache = salt
            return salt

    def _derive_key(self) -> bytes:
        # Cached under the same lock: scrypt(n=2**14) is deliberately expensive — running it on
        # every encrypt and decrypt would be a self-inflicted DoS.
        if self._derived_key is not None:
            return self._derived_key
        salt = self._salt()
        with self._kdf_lock:
            if self._derived_key is None:
                self._derived_key = hashlib.scrypt(self._master_key, salt=salt, n=2**14, r=8, p=1, dklen=32)
            return self._derived_key

    def _legacy_derive_key(self) -> bytes:
        """Unsalted SHA-256 — only used to READ ciphertext written before the KDF fix."""
        return hashlib.sha256(self._master_key).digest()[:32]

    def _encrypt_value(self, value: str) -> str:
        key = base64.urlsafe_b64encode(self._derive_key())
        return _encrypt(value, key)

    def _decrypt_value(self, encrypted: str) -> str:
        try:
            return _decrypt(encrypted, base64.urlsafe_b64encode(self._derive_key()))
        except Exception:
            # Legacy ciphertext (unsalted SHA-256). Read it rather than destroy it; the next
            # write re-encrypts under scrypt.
            return _decrypt(encrypted, base64.urlsafe_b64encode(self._legacy_derive_key()))

    def _load(self) -> None:
        if os.path.exists(self._db_path):
            try:
                with open(self._db_path, "r") as f:
                    data = json.load(f)
                for key_data in data.get("keys", []):
                    key = SecureKey(**key_data)
                    self._keys[key.key_id] = key
            except Exception:
                pass

    def _save(self) -> None:
        os.makedirs(
            os.path.dirname(self._db_path) if os.path.dirname(self._db_path) else ".",
            exist_ok=True,
        )
        with open(self._db_path, "w") as f:
            json.dump(
                {
                    "keys": [
                        {
                            "key_id": k.key_id,
                            "user_id": k.user_id,
                            "service": k.service,
                            "key_name": k.key_name,
                            "encrypted_key": k.encrypted_key,
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
        os.chmod(self._db_path, 0o600)

    def add_key(
        self,
        user_id: str,
        service: str,
        key_name: str,
        api_key: str,
        api_url: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict:
        """Add an encrypted API key for a specific user."""
        encrypted = self._encrypt_value(api_key)
        key = SecureKey(
            user_id=user_id,
            service=service,
            key_name=key_name,
            encrypted_key=encrypted,
            api_url=api_url,
            config=config or {},
        )
        self._keys[key.key_id] = key
        self._save()
        return key.to_public()

    def get_key(self, key_id: str, user_id: str) -> str | None:
        """Get decrypted API key (user-scoped)."""
        key = self._keys.get(key_id)
        if key and key.user_id == user_id and key.encrypted_key:
            return self._decrypt_value(key.encrypted_key)
        return None

    def get_config(self, service: str, user_id: str) -> dict[str, Any]:
        """Get merged config for a service (user-scoped)."""
        keys = [k for k in self._keys.values() if k.service == service and k.user_id == user_id and k.is_active]
        if not keys:
            return {}

        config = {}
        for key in keys:
            config.update(key.config)
            if key.api_url:
                config["api_url"] = key.api_url
            api_key = self.get_key(key.key_id, user_id)
            if api_key:
                config["api_key"] = api_key
        return config

    def list_keys(self, user_id: str, service: str | None = None) -> list[dict]:
        """List keys for a user (without key material)."""
        keys = [k for k in self._keys.values() if k.user_id == user_id]
        if service:
            keys = [k for k in keys if k.service == service]
        return [k.to_public() for k in keys]

    def remove_key(self, key_id: str, user_id: str) -> bool:
        """Remove a key (user-scoped)."""
        key = self._keys.get(key_id)
        if key and key.user_id == user_id:
            del self._keys[key_id]
            self._save()
            return True
        return False

    def summary(self, user_id: str | None = None) -> dict:
        keys = self._keys.values()
        if user_id:
            keys = [k for k in keys if k.user_id == user_id]
        by_service = {}
        for k in keys:
            by_service[k.service] = by_service.get(k.service, 0) + 1
        return {
            "total_keys": len(keys),
            "by_service": by_service,
            "encrypted": True,
        }
