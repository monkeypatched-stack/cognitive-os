"""Keystore security tests — race conditions, encryption, user scoping, file safety."""

from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest


class TestKeystoreEncryption:
    """Verify keys are encrypted at rest."""

    def test_stored_key_is_encrypted(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("u1", "github", "pat", "ghp_REAL_TOKEN_12345")
        with open(tmp_path / "ks.json") as f:
            raw = f.read()
        assert "ghp_REAL_TOKEN_12345" not in raw
        assert "ghp_" not in raw

    def test_decryption_roundtrip(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("u1", "slack", "token", "xoxb-secret-token")
        keys = ks.list_keys("u1")
        assert len(keys) == 1
        decrypted = ks.get_key(keys[0]["key_id"], "u1")
        assert decrypted == "xoxb-secret-token"


class TestKeystoreUserScoping:
    """Verify users cannot access each other's keys."""

    def test_cross_user_read_blocked(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("alice", "github", "pat", "alice-secret")
        keys_alice = ks.list_keys("alice")
        result = ks.get_key(keys_alice[0]["key_id"], "bob")
        assert result is None

    def test_cross_user_delete_blocked(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("alice", "github", "pat", "alice-secret")
        keys_alice = ks.list_keys("alice")
        result = ks.remove_key(keys_alice[0]["key_id"], "bob")
        assert result is False
        assert len(ks.list_keys("alice")) == 1

    def test_user_only_sees_own_keys(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("alice", "github", "pat", "alice-secret")
        ks.add_key("bob", "slack", "token", "bob-secret")
        assert len(ks.list_keys("alice")) == 1
        assert len(ks.list_keys("bob")) == 1


class TestKeystoreConcurrency:
    """Test that concurrent writes don't corrupt the keystore file."""

    def test_concurrent_add_keys(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )

        def add_key(i):
            ks.add_key(f"user-{i % 5}", "service", f"key-{i}", f"secret-{i}")
            return True

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(add_key, i) for i in range(50)]
            results = [f.result() for f in as_completed(futures)]
        assert all(results)
        all_keys = []
        for i in range(5):
            all_keys.extend(ks.list_keys(f"user-{i}"))
        assert len(all_keys) == 50

    def test_concurrent_add_and_remove(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        for i in range(10):
            ks.add_key("user", "svc", f"key-{i}", f"secret-{i}")
        keys = ks.list_keys("user")
        key_ids = [k["key_id"] for k in keys]

        def remove_key(kid):
            return ks.remove_key(kid, "user")

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(remove_key, kid) for kid in key_ids]
            results = [f.result() for f in as_completed(futures)]
        assert all(results)
        assert len(ks.list_keys("user")) == 0

    def test_concurrent_read_write_integrity(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("reader", "svc", "key0", "secret0")

        errors = []

        def writer():
            for i in range(20):
                ks.add_key("writer", "svc", f"w-{i}", f"secret-{i}")

        def reader():
            for _ in range(20):
                try:
                    ks.list_keys("reader")
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors


class TestKeystoreFilePermissions:
    """Verify keystore file has restrictive permissions."""

    def test_file_permissions_600(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("u1", "svc", "k", "v")
        mode = os.stat(tmp_path / "ks.json").st_mode & 0o777
        assert mode == 0o600


class TestKeystorePersistence:
    """Verify keystore persists across instances."""

    def test_reload_from_disk(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        db = str(tmp_path / "ks.json")
        ks1 = SecureKeystore(master_key="test-key-32-bytes-long!!!!!!!!", db_path=db)
        ks1.add_key("u1", "svc", "k1", "persistent-secret")
        ks2 = SecureKeystore(master_key="test-key-32-bytes-long!!!!!!!!", db_path=db)
        keys = ks2.list_keys("u1")
        assert len(keys) == 1
        assert ks2.get_key(keys[0]["key_id"], "u1") == "persistent-secret"

    def test_wrong_master_key_raises_invalid_token(self, tmp_path):
        from cerebellum.keystore import SecureKeystore
        from cryptography.fernet import InvalidToken

        db = str(tmp_path / "ks.json")
        ks1 = SecureKeystore(master_key="correct-key-32-bytes-long!!!!", db_path=db)
        ks1.add_key("u1", "svc", "k1", "my-secret")
        ks2 = SecureKeystore(master_key="wrong-key-32-bytes-long-xxxx!", db_path=db)
        keys = ks2.list_keys("u1")
        assert len(keys) == 1
        with pytest.raises(InvalidToken):
            ks2.get_key(keys[0]["key_id"], "u1")

    def test_key_not_in_list_for_other_service(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=str(tmp_path / "ks.json"),
        )
        ks.add_key("u1", "github", "pat", "secret")
        assert len(ks.list_keys("u1", service="slack")) == 0
        assert len(ks.list_keys("u1", service="github")) == 1
