"""Keystore crypto (C-1, C-2).

C-1  With no master key, SecureKeystore silently generated a RANDOM, non-persisted one.
     Anything encrypted that session became permanently undecryptable after a restart —
     and the live /keys route constructed it exactly that way. It must fail loudly.
C-2  Key derivation was a single UNSALTED sha256 (despite the docstring promising scrypt):
     cheap to brute-force offline from a leaked keystore, and identical master keys derived
     identical keys across installs. Now scrypt + a persisted random salt, with legacy
     ciphertext still readable.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from cerebellum.keystore import SecureKeystore, _encrypt

MASTER = "unit-test-master-key"


# ── C-1: no silent ephemeral master key ──────────────────────────────────────────


def test_missing_master_key_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTOS_MASTER_KEY", raising=False)
    monkeypatch.delenv("AGENTOS_KEYSTORE_EPHEMERAL", raising=False)
    with pytest.raises(RuntimeError, match="master key"):
        SecureKeystore(db_path=str(tmp_path / "ks.json"))


def test_ephemeral_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTOS_MASTER_KEY", raising=False)
    monkeypatch.setenv("AGENTOS_KEYSTORE_EPHEMERAL", "1")
    SecureKeystore(db_path=str(tmp_path / "ks.json"))  # explicit throwaway store: allowed


def test_master_key_from_env_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTOS_MASTER_KEY", MASTER)
    ks = SecureKeystore(db_path=str(tmp_path / "ks.json"))
    assert ks._master_key == MASTER.encode()


# ── C-2: scrypt + persisted salt ─────────────────────────────────────────────────


def test_derivation_is_scrypt_salted_not_bare_sha256(tmp_path):
    ks = SecureKeystore(master_key=MASTER, db_path=str(tmp_path / "ks.json"))
    derived = ks._derive_key()
    assert derived != hashlib.sha256(MASTER.encode()).digest()[:32]  # not the old KDF
    assert derived == hashlib.scrypt(MASTER.encode(), salt=ks._salt(), n=2**14, r=8, p=1, dklen=32)


def test_salt_is_persisted_so_keys_survive_restart(tmp_path):
    db = str(tmp_path / "ks.json")
    ks1 = SecureKeystore(master_key=MASTER, db_path=db)
    blob = ks1._encrypt_value("sk-live-secret")
    ks2 = SecureKeystore(master_key=MASTER, db_path=db)  # "restart"
    assert ks2._decrypt_value(blob) == "sk-live-secret"  # same salt -> same key


def test_salt_differs_across_installs(tmp_path):
    a = SecureKeystore(master_key=MASTER, db_path=str(tmp_path / "a" / "ks.json"))
    b = SecureKeystore(master_key=MASTER, db_path=str(tmp_path / "b" / "ks.json"))
    assert a._salt() != b._salt()  # same passphrase must not derive the same key


def test_legacy_ciphertext_is_still_readable(tmp_path):
    """Existing values encrypted with the old unsalted SHA-256 must not be destroyed."""
    ks = SecureKeystore(master_key=MASTER, db_path=str(tmp_path / "ks.json"))
    legacy_key = base64.urlsafe_b64encode(hashlib.sha256(MASTER.encode()).digest()[:32])
    legacy_blob = _encrypt("old-secret", legacy_key)
    assert ks._decrypt_value(legacy_blob) == "old-secret"
