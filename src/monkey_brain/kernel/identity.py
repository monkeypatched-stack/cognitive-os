"""Runtime Identity — asymmetric cryptographic identity for cognitive runtimes.

Every runtime owns an Ed25519 keypair.  All signatures use the private key;
verification uses the public key.  No shared secrets remain in the system.

The identity is the root of the trust chain — every proposal, checkpoint,
execution graph, and agent contribution carries a runtime-signed envelope
that any receiving runtime can independently verify.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


import contextlib


@contextlib.contextmanager
def _file_lock(path: str):
    """Advisory inter-process exclusive lock (flock). Serializes key/passphrase creation
    across processes that share a key store, so two runtimes can't generate divergent keys.
    Best-effort where flock is unavailable (e.g. Windows)."""
    try:
        import fcntl
    except Exception:
        yield
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except Exception:
            logger.debug("_file_lock: suppressed exception", exc_info=True)
        fh.close()


def _atomic_write(path: str, data: bytes, mode: int = 0o600) -> None:
    """Write bytes durably: temp file → fsync → os.replace. A crash mid-write can never
    leave a truncated/corrupt private key at `path` (the old key survives intact)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger("agentos.identity")


# ── Nonce tracking (replay protection) ─────────────────────────────────────


class NonceStore:
    """Thread-safe set of recently seen nonces.  Prevents replay attacks.

    Nonces expire after `ttl` seconds.  Bounded to `max_size` to resist
    memory exhaustion from proposal flooding.
    """

    def __init__(self, max_size: int = 100_000, ttl: float = 3600.0) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max = max_size
        self._ttl = ttl

    def check_and_record(self, nonce: str) -> bool:
        """Return True if the nonce is fresh (not seen before).  Record it."""
        now = time.time()
        with self._lock:
            self._expire(now)
            if nonce in self._seen:
                return False
            if len(self._seen) >= self._max:
                return False
            self._seen[nonce] = now
            return True

    def _expire(self, now: float) -> None:
        cutoff = now - self._ttl
        expired = [k for k, v in self._seen.items() if v < cutoff]
        for k in expired:
            del self._seen[k]


# ── Runtime Identity ───────────────────────────────────────────────────────


@dataclass
class RuntimeIdentity:
    """A runtime's cryptographic identity.

    Attributes:
        runtime_id:     unique identifier (UUID)
        runtime_type:   personal | community | enterprise | institution | government
        owner:          who controls this runtime
        jurisdiction:   legal jurisdiction
        public_key_pem: Ed25519 public key (PEM-encoded)
        certificate:    optional X.509 certificate reference
        created_at:     creation timestamp
        expires_at:     expiration timestamp
        capabilities:   what this runtime can do
        trust_policies: trust rules this runtime enforces
    """

    runtime_id: str = field(default_factory=lambda: str(uuid4()))
    runtime_type: str = "personal"
    owner: str = ""
    jurisdiction: str = ""
    public_key_pem: str = ""
    certificate: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    trust_policies: dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        return bool(self.public_key_pem)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "runtime_type": self.runtime_type,
            "owner": self.owner,
            "jurisdiction": self.jurisdiction,
            "public_key_pem": self.public_key_pem,
            "certificate": self.certificate,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "capabilities": self.capabilities,
            "trust_policies": self.trust_policies,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RuntimeIdentity:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Key Management ─────────────────────────────────────────────────────────


class KeyManager:
    """Manages Ed25519 keypairs for runtime identity.

    Keys are encrypted at rest using a passphrase from AGENTOS_KEY_PASSWORD
    (or auto-generated on first boot and stored in ~/.monkeybrain/key_password).
    """

    def __init__(self, key_dir: str | None = None) -> None:
        self._key_dir = key_dir or os.path.expanduser("~/.monkeybrain/keys")
        self._cache: dict[str, ed25519.Ed25519PrivateKey] = {}
        self._cache_order: list[str] = []  # LRU order for eviction
        self._cache_max_size = 1000  # Maximum cached keys
        self._lock = threading.Lock()
        self._passphrase = self._load_or_create_passphrase()

    def _load_or_create_passphrase(self) -> bytes:
        """Load passphrase from env or generate and persist one."""
        env_pass = os.environ.get("AGENTOS_KEY_PASSWORD", "")
        if env_pass:
            return env_pass.encode("utf-8")

        pw_file = os.path.expanduser("~/.monkeybrain/key_password")
        # Inter-process lock so two processes can't persist DIFFERENT passphrases (which would
        # make one process unable to decrypt keys written by the other).
        with _file_lock(pw_file + ".lock"):
            if os.path.exists(pw_file):
                with open(pw_file, "r") as f:
                    return f.read().strip().encode("utf-8")
            import secrets

            pw = secrets.token_hex(32)
            _atomic_write(pw_file, pw.encode("utf-8"))
            return pw.encode("utf-8")

    def get_or_create(self, runtime_id: str) -> ed25519.Ed25519PrivateKey:
        """Return the private key for a runtime, creating one if needed.

        Keys are encrypted at rest with BestAvailableEncryption.
        """
        with self._lock:
            if runtime_id in self._cache:
                # Move to end (most recently used)
                self._cache_order.remove(runtime_id)
                self._cache_order.append(runtime_id)
                return self._cache[runtime_id]

            key_path = os.path.join(self._key_dir, f"{runtime_id}.key")
            os.makedirs(self._key_dir, exist_ok=True)
            # Inter-process lock: re-check existence INSIDE the lock so two processes sharing a
            # key store can't both "see no file" and generate divergent keys.
            with _file_lock(key_path + ".lock"):
                if os.path.exists(key_path):
                    with open(key_path, "rb") as f:
                        raw = f.read()
                    try:
                        key = serialization.load_pem_private_key(raw, password=self._passphrase)
                    except TypeError:
                        # Legacy unencrypted key — re-encrypt with current passphrase
                        key = serialization.load_pem_private_key(raw, password=None)
                        _atomic_write(
                            key_path,
                            key.private_bytes(
                                serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(self._passphrase),
                            ),
                        )
                else:
                    key = ed25519.Ed25519PrivateKey.generate()
                    _atomic_write(
                        key_path,
                        key.private_bytes(
                            serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(self._passphrase),
                        ),
                    )
                self._cache[runtime_id] = key
                self._cache_order.append(runtime_id)
                # Evict LRU entries if cache is full
                while len(self._cache) > self._cache_max_size:
                    old_id = self._cache_order.pop(0)
                    self._cache.pop(old_id, None)
                return key

    def get_public_key_pem(self, runtime_id: str) -> str:
        """Return the PEM-encoded public key for a runtime."""
        key = self.get_or_create(runtime_id)
        pub = key.public_key()
        return pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def get_public_key(self, public_key_pem: str) -> ed25519.Ed25519PublicKey:
        """Deserialize a PEM-encoded public key."""
        return serialization.load_pem_public_key(
            public_key_pem.encode() if isinstance(public_key_pem, str) else public_key_pem
        )

    def rotate(self, runtime_id: str) -> tuple[ed25519.Ed25519PrivateKey, str]:
        """Generate a new keypair for a runtime.  Returns (new_private_key, old_public_key_pem).

        The old public key is returned so callers can re-sign artifacts that
        were signed with the previous key.
        """
        with self._lock:
            # Get old public key before rotation
            old_key = self._cache.get(runtime_id)
            old_pub_pem = ""
            if old_key:
                old_pub_pem = self.get_public_key_pem(runtime_id)

            # Generate new key
            new_key = ed25519.Ed25519PrivateKey.generate()
            self._cache[runtime_id] = new_key

            # Archive old key
            key_path = os.path.join(self._key_dir, f"{runtime_id}.key")
            archive_path = os.path.join(self._key_dir, f"{runtime_id}.key.old")
            if os.path.exists(key_path):
                os.rename(key_path, archive_path)

            # Write new encrypted key (atomic — old key was archived above)
            _atomic_write(
                key_path,
                new_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(self._passphrase),
                ),
            )
            os.chmod(key_path, 0o600)

            logger.info("[identity] rotated key for %s", runtime_id)
            return new_key, old_pub_pem

    def needs_rotation(self, runtime_id: str, max_age_days: float = 90) -> bool:
        """Check if a key should be rotated based on file age."""
        key_path = os.path.join(self._key_dir, f"{runtime_id}.key")
        if not os.path.exists(key_path):
            return True
        age_days = (time.time() - os.path.getmtime(key_path)) / 86400
        return age_days > max_age_days


# ── Signing / Verification ─────────────────────────────────────────────────


def sign_bytes(data: bytes, private_key: ed25519.Ed25519PrivateKey) -> str:
    """Sign bytes with an Ed25519 private key.  Returns hex signature."""
    return private_key.sign(data).hex()


def verify_bytes(data: bytes, signature_hex: str, public_key_pem: str) -> bool:
    """Verify an Ed25519 signature.  Constant-time comparison."""
    try:
        pub = serialization.load_pem_public_key(
            public_key_pem.encode() if isinstance(public_key_pem, str) else public_key_pem
        )
        pub.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


def sign_payload(
    payload: dict[str, Any],
    private_key: ed25519.Ed25519PrivateKey,
    runtime_id: str,
    nonce: str = "",
) -> dict[str, Any]:
    """Create a signed envelope around a payload.

    Returns the payload plus: runtime_id, signature, timestamp, nonce.
    """
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    timestamp = time.time()
    nonce = nonce or uuid4().hex
    signed_blob = f"{runtime_id}:{timestamp}:{nonce}:".encode() + blob
    signature = sign_bytes(signed_blob, private_key)

    return {
        **payload,
        "_runtime_id": runtime_id,
        "_signature": signature,
        "_timestamp": timestamp,
        "_nonce": nonce,
    }


def verify_signed_payload(
    envelope: dict[str, Any],
    public_key_pem: str,
    nonce_store: NonceStore | None = None,
    max_age: float = 3600.0,
) -> tuple[bool, str]:
    """Verify a signed envelope.

    Checks: signature, timestamp freshness, nonce uniqueness.
    Returns (valid, reason).
    """
    runtime_id = envelope.get("_runtime_id", "")
    signature = envelope.get("_signature", "")
    timestamp = envelope.get("_timestamp", 0.0)
    nonce = envelope.get("_nonce", "")

    if not runtime_id or not signature:
        return False, "missing_runtime_id_or_signature"

    # Timestamp freshness
    if abs(time.time() - timestamp) > max_age:
        return False, "timestamp_expired"

    # Replay protection
    if nonce_store and not nonce_store.check_and_record(nonce):
        return False, "replay_detected"

    # Signature verification
    payload = {k: v for k, v in envelope.items() if not k.startswith("_")}
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    signed_blob = f"{runtime_id}:{timestamp}:{nonce}:".encode() + blob

    if not verify_bytes(signed_blob, signature, public_key_pem):
        return False, "signature_invalid"

    return True, "ok"


# ── Process-global defaults ────────────────────────────────────────────────

_default_key_manager: KeyManager | None = None
_default_nonce_store: NonceStore = NonceStore()
# RLock (not Lock): get_identity() holds this while calling create_identity() →
# get_key_manager(), which re-acquires it. A plain Lock deadlocks on first use.
_identity_lock = threading.RLock()


def get_key_manager() -> KeyManager:
    global _default_key_manager
    if _default_key_manager is None:
        with _identity_lock:
            if _default_key_manager is None:
                _default_key_manager = KeyManager()
    return _default_key_manager


def get_nonce_store() -> NonceStore:
    return _default_nonce_store


def create_identity(runtime_id: str | None = None, **kwargs: Any) -> RuntimeIdentity:
    """Create or load a RuntimeIdentity for the given (or default) runtime."""
    km = get_key_manager()
    rid = runtime_id or os.getenv("RUNTIME_ID", str(uuid4()))
    km.get_or_create(rid)
    pub_pem = km.get_public_key_pem(rid)

    identity = RuntimeIdentity(
        runtime_id=rid,
        public_key_pem=pub_pem,
        **kwargs,
    )
    return identity


_default_identity: RuntimeIdentity | None = None


def get_identity() -> RuntimeIdentity:
    global _default_identity
    if _default_identity is None:
        with _identity_lock:
            if _default_identity is None:
                _default_identity = create_identity()
    return _default_identity
