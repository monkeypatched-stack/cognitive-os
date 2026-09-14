"""Code Vault — password-protected encryption for generated code.

Uses Fernet (AES-128-CBC) symmetric encryption to protect generated code.
Password is derived via PBKDF2 with SHA-256.

Vault files:
- .soma/vault/key.enc  — encrypted master key
- .soma/vault/salt     — salt for key derivation
- <file>.enc            — encrypted generated code
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet key from password using PBKDF2."""
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 480000, dklen=32)
    return base64.urlsafe_b64encode(key)


class CodeVault:
    """Password-protects generated code files using Fernet encryption."""

    def __init__(self, vault_dir: Path | None = None, password: str | None = None):
        self.vault_dir = vault_dir or Path(".soma/vault")
        self._password = password
        self._fernet = None

    def _get_fernet(self):
        """Get or create Fernet instance from password."""
        if self._fernet:
            return self._fernet

        if not self._password:
            return None

        self.vault_dir.mkdir(parents=True, exist_ok=True)
        salt_file = self.vault_dir / "salt"

        if salt_file.exists():
            salt = salt_file.read_bytes()
        else:
            salt = os.urandom(16)
            salt_file.write_bytes(salt)
            os.chmod(salt_file, 0o600)

        key = _derive_key(self._password, salt)

        from cryptography.fernet import Fernet

        self._fernet = Fernet(key)
        return self._fernet

    def encrypt_file(self, source: Path, password: str | None = None) -> Path:
        """Encrypt a file and return the path to the encrypted version."""
        if password:
            self._password = password
            self._fernet = None

        fernet = self._get_fernet()
        if not fernet:
            return source

        content = source.read_bytes()
        encrypted = fernet.encrypt(content)

        enc_path = source.parent / (source.name + ".enc")
        enc_path.write_bytes(encrypted)
        os.chmod(enc_path, 0o600)
        return enc_path

    def decrypt_file(self, enc_path: Path, password: str | None = None) -> bytes:
        """Decrypt a .enc file and return the plaintext."""
        if password:
            self._password = password
            self._fernet = None

        fernet = self._get_fernet()
        if not fernet:
            return enc_path.read_bytes()

        encrypted = enc_path.read_bytes()
        return fernet.decrypt(encrypted)

    def encrypt_directory(self, source_dir: Path, password: str | None = None) -> Path:
        """Encrypt all files in a directory."""
        if password:
            self._password = password
            self._fernet = None

        fernet = self._get_fernet()
        if not fernet:
            return source_dir

        enc_dir = source_dir.parent / (source_dir.name + ".vault")
        enc_dir.mkdir(parents=True, exist_ok=True)

        for f in source_dir.rglob("*.py"):
            content = f.read_bytes()
            encrypted = fernet.encrypt(content)
            rel = f.relative_to(source_dir)
            enc_file = enc_dir / rel
            enc_file.parent.mkdir(parents=True, exist_ok=True)
            enc_file.write_bytes(encrypted)
            os.chmod(enc_file, 0o600)

        return enc_dir

    def decrypt_directory(self, enc_dir: Path, password: str | None = None) -> Path:
        """Decrypt all files in a .vault directory."""
        if password:
            self._password = password
            self._fernet = None

        fernet = self._get_fernet()
        if not fernet:
            return enc_dir

        out_dir = enc_dir.parent / (enc_dir.name.replace(".vault", ""))
        out_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for f in enc_dir.rglob("*"):
            if not f.is_file() or f.name.startswith(".") or f.name.endswith(".json"):
                continue

            encrypted = f.read_bytes()
            try:
                decrypted = fernet.decrypt(encrypted)
                rel = f.relative_to(enc_dir)
                out_file = out_dir / rel
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_bytes(decrypted)
                count += 1
            except Exception:
                pass

        return out_dir

    def check_password(self, password: str) -> bool:
        """Verify a password against the vault."""
        salt_file = self.vault_dir / "salt"
        if not salt_file.exists():
            return True

        self._password = password
        self._fernet = None
        self._get_fernet()

        enc_file = self.vault_dir / "test.enc"
        if enc_file.exists():
            try:
                self.decrypt_file(enc_file)
                return True
            except Exception:
                return False
        return True


def encrypt_generated_code(source_dir: Path, password: str) -> Path:
    """Encrypt all generated code in a directory."""
    vault = CodeVault(password=password)
    return vault.encrypt_directory(source_dir, password=password)


def decrypt_generated_code(vault_dir: Path, password: str) -> Path:
    """Decrypt all generated code from a vault directory."""
    vault = CodeVault(password=password)
    return vault.decrypt_directory(vault_dir, password=password)
