"""End-to-end execution tests.

Requires all MonkeyBrain services to be running:
    monkeypatched svc start

Each test class covers one execution flow through the live API stack.
Tests skip automatically when the target service is not reachable.
"""

from __future__ import annotations

import os
import pytest
import httpx

# ── Service map ───────────────────────────────────────────────────────────────

AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8010")
AGENTOS_URL = os.getenv("AGENTOS_URL", "http://localhost:8031")
ASSETS_URL = os.getenv("ASSETS_URL", "http://localhost:8011")
ORDERS_URL = os.getenv("ORDERS_URL", "http://localhost:8018")
PROCESS_URL = os.getenv("PROCESS_URL", "http://localhost:8000")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8016")

E2E_USER = os.getenv("E2E_USER", "prashun@monkeypatched.com")
E2E_PASSWORD = os.getenv("E2E_PASSWORD", "Admin@12345678")

TIMEOUT = httpx.Timeout(10.0)


def _reachable(base_url: str) -> bool:
    try:
        r = httpx.get(f"{base_url}/health", timeout=3.0)
        return r.status_code < 500
    except Exception:
        return False


def _skip_if_down(url: str):
    return pytest.mark.skipif(
        not _reachable(url),
        reason=f"Service not reachable at {url}",
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def auth_token() -> str:
    """Obtain a real access token from the live auth service."""
    if not _reachable(AUTH_URL):
        pytest.skip(f"Auth service not reachable at {AUTH_URL}")
    resp = httpx.post(
        f"{AUTH_URL}/api/v1/auth/login",
        json={"email": E2E_USER, "password": E2E_PASSWORD},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    token = data.get("access_token") or data.get("token")
    assert token, f"No token in login response: {data}"
    return token


@pytest.fixture(scope="session")
def authed(auth_token) -> httpx.Client:
    """Authenticated httpx client."""
    return httpx.Client(
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=TIMEOUT,
    )


# ── Health checks ─────────────────────────────────────────────────────────────


class TestServiceHealth:
    """All core services must return a healthy response."""

    @pytest.mark.parametrize(
        "name,url",
        [
            ("auth", AUTH_URL),
            ("agentos", AGENTOS_URL),
            ("assets", ASSETS_URL),
            ("orders", ORDERS_URL),
            ("process_definition", PROCESS_URL),
            ("inventory", INVENTORY_URL),
        ],
    )
    def test_health(self, name, url):
        if not _reachable(url):
            pytest.skip(f"{name} not running at {url}")
        r = httpx.get(f"{url}/health", timeout=TIMEOUT)
        assert r.status_code == 200, f"{name} /health returned {r.status_code}"


# ── Auth flow ─────────────────────────────────────────────────────────────────


@_skip_if_down(AUTH_URL)
class TestAuthFlow:
    def test_login_returns_token(self):
        r = httpx.post(
            f"{AUTH_URL}/api/v1/auth/login",
            json={"email": E2E_USER, "password": E2E_PASSWORD},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body or "token" in body

    def test_me_endpoint_requires_auth(self):
        r = httpx.get(f"{AUTH_URL}/api/v1/me", timeout=TIMEOUT)
        assert r.status_code in (401, 403)

    def test_me_endpoint_with_token(self, authed):
        r = authed.get(f"{AUTH_URL}/api/v1/me")
        assert r.status_code == 200
        body = r.json()
        assert "email" in body or "id" in body or "username" in body


# ── AgentOS query flow ────────────────────────────────────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestAgentOSExecution:
    def test_agents_list(self, authed):
        r = authed.get(f"{AGENTOS_URL}/api/v1/agentos/agents")
        assert r.status_code == 200
        body = r.json()
        agents = body if isinstance(body, list) else body.get("agents", [])
        assert isinstance(agents, list)
        assert len(agents) > 0

    def test_query_endpoint_rejects_empty(self, authed):
        r = authed.post(f"{AGENTOS_URL}/api/v1/agentos/query", json={})
        assert r.status_code in (400, 422)

    def test_query_execution(self, authed):
        r = authed.post(
            f"{AGENTOS_URL}/api/v1/agentos/query",
            json={"question": "list all agents"},
        )
        assert r.status_code in (200, 202), f"query failed: {r.text}"

    def test_prompt_execution(self, authed):
        r = authed.post(
            f"{AGENTOS_URL}/api/v1/agentos/prompt",
            json={"question": "What is the system status?"},
        )
        assert r.status_code in (200, 202), f"prompt failed: {r.text}"


# ── Process definition flow ───────────────────────────────────────────────────


@_skip_if_down(PROCESS_URL)
class TestProcessDefinitionFlow:
    def test_list_process_definitions(self, authed):
        r = authed.get(f"{PROCESS_URL}/api/v1/process-definitions/")
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))

    def test_create_and_retrieve_process(self, authed):
        import uuid

        pd_id = f"e2e-test-{uuid.uuid4().hex[:8]}"
        payload = {
            "process_definition_id": pd_id,
            "name": "E2E Test Process",
            "description": "Created by e2e test suite",
            "steps": {
                "id": f"steps-{pd_id}",
                "process_definition_id": pd_id,
                "steps": [],
            },
        }
        create = authed.post(f"{PROCESS_URL}/api/v1/process-definitions/", json=payload)
        assert create.status_code in (200, 201), f"create failed: {create.text}"
        body = create.json()
        pid = body.get("process_definition_id")
        assert pid

        fetch = authed.get(f"{PROCESS_URL}/api/v1/process-definitions/{pid}")
        assert fetch.status_code == 200


# ── Inventory read flow ───────────────────────────────────────────────────────


@_skip_if_down(INVENTORY_URL)
class TestInventoryFlow:
    def test_list_inventory_items(self, authed):
        r = authed.get(f"{INVENTORY_URL}/api/v1/inventory-items/")
        assert r.status_code == 200

    def test_list_inventory_allocations(self, authed):
        r = authed.get(f"{INVENTORY_URL}/api/v1/inventory-allocations/")
        assert r.status_code in (200, 404)


# ── Cross-service flow: auth → agentos → process ─────────────────────────────


@pytest.mark.skipif(
    not (_reachable(AUTH_URL) and _reachable(AGENTOS_URL) and _reachable(PROCESS_URL)),
    reason="Cross-service test requires auth + agentos + process_definition",
)
class TestCrossServiceExecution:
    def test_full_execution_flow(self, authed):
        """Token from auth → query agentos → verify process definitions accessible."""
        agents = authed.get(f"{AGENTOS_URL}/api/v1/agentos/agents")
        assert agents.status_code == 200

        procs = authed.get(f"{PROCESS_URL}/api/v1/process-definitions/")
        assert procs.status_code == 200

        query = authed.post(
            f"{AGENTOS_URL}/api/v1/agentos/query",
            json={"question": "what processes are defined?"},
        )
        assert query.status_code in (200, 202)
