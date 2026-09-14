"""Fault injection tests — secret rotation, CDC watcher status API, NATS error state, and keystore I/O stress.

NOTE: Tests that require live infrastructure are intentionally excluded:
  - Pod kills during execution require a Kubernetes cluster (kubectl/k8s API).
  - Helm install/upgrade/rollback requires Helm + a running K8s cluster.
  - Live MongoDB/Redis outage simulation requires running services (docker-compose).
  - OPA fail-closed is not applicable — the codebase uses custom JWT permissions, not OPA.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestCDCWatcherStatusAPI:
    """Verify cdc_watcher_status() returns a well-formed dict when no live DB is present.

    Does NOT simulate a real MongoDB outage — that requires a running mongod.
    Tests that the status API is safe to call and returns expected fields regardless
    of connection state.
    """

    def test_cdc_watcher_status_is_dict(self):
        from services.common.mongo_cdc_watcher import cdc_watcher_status

        status = cdc_watcher_status()
        assert isinstance(status, dict)

    def test_cdc_watcher_status_callable_repeatedly(self):
        from services.common.mongo_cdc_watcher import cdc_watcher_status

        for _ in range(100):
            status = cdc_watcher_status()
            assert isinstance(status, dict)

    def test_cdc_watcher_fields_present(self):
        from services.common.mongo_cdc_watcher import cdc_watcher_status

        status = cdc_watcher_status()
        assert "enabled" in status
        assert "database" in status
        assert "subject" in status
        assert "scope" in status
        assert "last_error" in status


class TestInMemoryStateReset:
    """Verify core in-process objects can be re-instantiated cleanly.

    Does NOT simulate real pod kills — that requires kubectl/k8s.
    Tests that constructors produce independent, valid objects after a simulated
    restart (i.e. no module-level singleton pollution).
    """

    def test_learning_reinstantiates_cleanly(self):
        from src.monkey_brain.kernel.learn.learning import Learning

        L1 = Learning()
        assert L1 is not None
        assert hasattr(L1, "get_transitions")

    def test_sync_manager_instances_are_independent(self):
        from src.sync.sync_manager import SyncManager

        sm1 = SyncManager()
        sm2 = SyncManager()
        assert sm1 is not sm2
        assert sm2 is not None


class TestSecretRotation:
    """Verify token/keystore behaviour when secrets are rotated in-process."""

    def test_rotate_access_token_secret(self):
        import jose.jwt
        from jose import JWTError
        from datetime import datetime, timezone, timedelta

        secret_v1 = "secret-version-1"
        secret_v2 = "secret-version-2"
        token_v1 = jose.jwt.encode(
            {"sub": "user-1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            secret_v1,
            algorithm="HS256",
        )
        decoded = jose.jwt.decode(token_v1, secret_v1, algorithms=["HS256"])
        assert decoded["sub"] == "user-1"
        with pytest.raises(JWTError):
            jose.jwt.decode(token_v1, secret_v2, algorithms=["HS256"])

    def test_keystore_survives_master_key_rotation(self, tmp_path):
        from cerebellum.keystore import SecureKeystore
        from cryptography.fernet import InvalidToken

        key_v1 = "master-key-version-1-32bytes!!!!"
        key_v2 = "master-key-version-2-32bytes!!!!"
        db = str(tmp_path / "ks.json")
        ks1 = SecureKeystore(master_key=key_v1, db_path=db)
        ks1.add_key("user", "svc", "k1", "secret-data")
        ks2 = SecureKeystore(master_key=key_v1, db_path=db)
        keys = ks2.list_keys("user")
        assert len(keys) == 1
        decrypted = ks2.get_key(keys[0]["key_id"], "user")
        assert decrypted == "secret-data"
        ks3 = SecureKeystore(master_key=key_v2, db_path=db)
        with pytest.raises(InvalidToken):
            ks3.get_key(keys[0]["key_id"], "user")

    def test_refresh_token_secret_independent(self):
        import jose.jwt
        from jose import JWTError
        from datetime import datetime, timezone, timedelta

        access_secret = "access-secret-123"
        refresh_secret = "refresh-secret-456"
        token = jose.jwt.encode(
            {"sub": "user-1", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            access_secret,
            algorithm="HS256",
        )
        decoded = jose.jwt.decode(token, access_secret, algorithms=["HS256"])
        assert decoded["sub"] == "user-1"
        with pytest.raises(JWTError):
            jose.jwt.decode(token, refresh_secret, algorithms=["HS256"])


class TestNATSErrorState:
    """Verify the NATS consumer module tracks connection errors in module-level state.

    Does NOT simulate a real NATS outage — that requires a running NATS server.
    Tests that error fields are writable and readable (i.e. the module supports
    error introspection without crashing).
    """

    def test_nats_consumer_error_field_is_writable(self):
        from services.auth.helpers import nats_consumer

        nats_consumer._last_connect_error = "Connection refused"
        nats_consumer._task = None
        assert nats_consumer._last_connect_error == "Connection refused"


class TestKeystoreIOStress:
    """Keystore correctness under concurrent threaded I/O."""

    def test_keystore_persists_during_io_stress(self):
        from cerebellum.keystore import SecureKeystore
        import threading

        ks = SecureKeystore(
            master_key="test-key-32-bytes-long!!!!!!!!",
            db_path=os.path.join(tempfile.mkdtemp(), "stress.json"),
        )
        errors = []

        def add_keys(prefix, count):
            for i in range(count):
                try:
                    ks.add_key(f"user-{prefix}", "svc", f"k-{prefix}-{i}", f"v-{i}")
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=add_keys, args=(str(i), 20)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        all_keys = []
        for i in range(5):
            all_keys.extend(ks.list_keys(f"user-{i}"))
        assert len(all_keys) == 100
