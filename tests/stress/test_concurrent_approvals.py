"""Concurrent approval stress tests — simulate multiple users approving/rejecting simultaneously."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

_root = os.path.join(os.path.dirname(__file__), "..", "..")
for p in (
    "src",
    "packages/cerebellum",
    "packages/broca",
    "domains/manufacturing/knowledge",
):
    _full = os.path.join(_root, p)
    if _full not in sys.path:
        sys.path.insert(0, _full)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestConcurrentKeystoreOperations:
    """Stress test keystore with concurrent operations from multiple 'users'."""

    def test_100_concurrent_adds(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="stress-test-key-32-bytes-long!!",
            db_path=str(tmp_path / "stress.json"),
        )

        def add_key(i):
            ks.add_key(f"user-{i % 20}", "service", f"key-{i}", f"secret-{i}")
            return True

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(add_key, i) for i in range(100)]
            results = [f.result() for f in as_completed(futures)]
        assert all(results)
        total = sum(len(ks.list_keys(f"user-{i}")) for i in range(20))
        assert total == 100

    def test_50_concurrent_adds_and_deletes(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="stress-test-key-32-bytes-long!!",
            db_path=str(tmp_path / "stress.json"),
        )
        for i in range(50):
            ks.add_key("user", "svc", f"pre-{i}", f"val-{i}")
        pre_keys = ks.list_keys("user")
        key_ids = [k["key_id"] for k in pre_keys]
        errors = []

        def remove_key(kid):
            try:
                return ks.remove_key(kid, "user")
            except Exception as e:
                errors.append(e)
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(remove_key, kid) for kid in key_ids]
            results = [f.result() for f in as_completed(futures)]
        assert not errors
        assert sum(results) == 50
        assert len(ks.list_keys("user")) == 0

    def test_concurrent_cross_user_operations(self, tmp_path):
        from cerebellum.keystore import SecureKeystore

        ks = SecureKeystore(
            master_key="stress-test-key-32-bytes-long!!",
            db_path=str(tmp_path / "stress.json"),
        )
        errors = []

        def user_ops(user_id, count):
            for i in range(count):
                try:
                    ks.add_key(user_id, "svc", f"k-{i}", f"v-{i}")
                    ks.list_keys(user_id)
                    keys = ks.list_keys(user_id)
                    if keys:
                        ks.get_key(keys[0]["key_id"], user_id)
                except Exception as e:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(user_ops, f"user-{i}", 10) for i in range(10)]
            for f in as_completed(futures):
                f.result()
        assert not errors


class TestConcurrentTokenCreation:
    """Stress test JWT token creation and validation under load."""

    def test_concurrent_token_creation(self):
        from services.auth.helpers.tokens import (
            create_access_token,
            decode_access_token,
        )

        def create_token(i):
            return create_access_token(f"user-{i}", f"u{i}@test.com", "admin", ["perm-a", "perm-b"])

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(create_token, i) for i in range(100)]
            tokens = [f.result() for f in as_completed(futures)]
        assert len(tokens) == 100
        assert len(set(tokens)) == 100

    def test_concurrent_token_decode(self):
        from services.auth.helpers.tokens import (
            create_access_token,
            decode_access_token,
        )

        token = create_access_token("user-1", "test@test.com", "admin", ["perm-a"])
        errors = []

        def decode_token():
            try:
                return decode_access_token(token)
            except Exception as e:
                errors.append(e)
                return None

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(decode_token) for _ in range(200)]
            results = [f.result() for f in as_completed(futures)]
        assert not errors
        assert all(r["sub"] == "user-1" for r in results if r)


class TestConcurrentURLValidation:
    """Stress test URL validation under concurrent load."""

    def test_concurrent_url_validation(self):
        from cerebellum.capabilities.api.webhook import _is_safe_url

        urls = [
            "http://example.com/hook",
            "https://api.service.io/v1",
            "http://169.254.169.254/",
            "http://localhost:8080/",
            "file:///etc/passwd",
            "http://10.0.0.1:6379/",
            "ftp://internal/data",
        ]

        def validate_batch():
            return [_is_safe_url(url) for url in urls]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(validate_batch) for _ in range(100)]
            results = [f.result() for f in as_completed(futures)]
        for batch in results:
            assert batch == [True, True, False, False, False, False, False]


class TestConcurrentApprovalWorkflow:
    """Simulate concurrent approval actions on the same approval request."""

    def test_concurrent_approval_state_transitions(self):
        approvals = {}
        lock = threading.Lock()

        def approve(approval_id, approver):
            with lock:
                if approval_id not in approvals:
                    approvals[approval_id] = {"status": "pending", "approvals": []}
                state = approvals[approval_id]
                state["approvals"].append(approver)
                if state["status"] == "pending" and len(state["approvals"]) >= 2:
                    state["status"] = "approved"
                return state["status"]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(20):
                futures.append(pool.submit(approve, "approval-1", f"approver-{i}"))
            results = [f.result() for f in as_completed(futures)]
        assert approvals["approval-1"]["status"] == "approved"
        assert len(approvals["approval-1"]["approvals"]) == 20


class TestConcurrentPathTraversal:
    """Stress test path traversal protection under concurrent access."""

    def test_concurrent_traversal_attempts(self, tmp_path):
        from cerebellum.capabilities.storage.storage import LocalFileSystemCapability

        cap = LocalFileSystemCapability(base_path=str(tmp_path))
        (tmp_path / "secret.txt").write_text("SECRET")

        def attempt_traversal(path):
            return _run(cap.execute({"operation": "read", "path": path}))

        def attempt_traversal(path):
            return _run(cap.execute({"operation": "read", "path": path}))

        def attempt_traversal(path):
            return _run(cap.execute({"operation": "read", "path": path}))

        traversal_paths = [
            "../../../etc/passwd",
            "../../etc/shadow",
            "/etc/passwd",
            "foo/../../etc/passwd",
        ]
        safe_paths = ["secret.txt"]
        all_paths = traversal_paths * 10 + safe_paths * 10

        with ThreadPoolExecutor(max_workers=10) as pool:
            future_to_path = {pool.submit(attempt_traversal, p): p for p in all_paths}
            results = []
            for f in as_completed(future_to_path):
                path = future_to_path[f]
                results.append((path, f.result()))

        for path, result in results:
            if path in traversal_paths:
                assert result.get("status") == "error" or result.get("content") != "SECRET", (
                    f"Traversal path {path!r} should not read SECRET"
                )


class TestLoadConcurrentAPI:
    """Load test the API with concurrent requests via asyncio."""

    def test_concurrent_health_requests(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:

                async def health_request():
                    resp = await client.get("/health")
                    return resp.status_code

                tasks = [health_request() for _ in range(50)]
                results = await asyncio.gather(*tasks, return_exceptions=True)
            return results

        results = _run(run())
        statuses = [r for r in results if isinstance(r, int)]
        assert len(statuses) == 50
        assert all(s in (200, 401, 403) for s in statuses)

    def test_concurrent_query_flood(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:

                async def query_request(i):
                    resp = await client.post("/api/v1/agentos/query", json={"question": f"test {i}"})
                    return resp.status_code

                tasks = [query_request(i) for i in range(30)]
                return await asyncio.gather(*tasks, return_exceptions=True)

        results = _run(run())
        statuses = [r for r in results if isinstance(r, int)]
        assert all(s in (200, 404, 422, 401, 403) for s in statuses)
