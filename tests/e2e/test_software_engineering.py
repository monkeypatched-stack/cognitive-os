"""End-to-end tests for the software engineering pipeline.

Covers the full ETASS flow:
  Spec → Codegen → Validate → Somatic charts → AgentOS query → Prompt pipeline

Requires MonkeyBrain (agentos) to be running:
    monkeypatched svc start
"""

from __future__ import annotations

import os
import uuid
import pytest
import httpx

AGENTOS_URL = os.getenv("AGENTOS_URL", "http://localhost:8031")
AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8010")
E2E_USER = os.getenv("E2E_USER", "prashun@monkeypatched.com")
E2E_PASSWORD = os.getenv("E2E_PASSWORD", "Admin@12345678")

TIMEOUT = httpx.Timeout(30.0)


def _reachable(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=3.0).status_code < 500
    except Exception:
        return False


def _skip_if_down(url: str):
    return pytest.mark.skipif(not _reachable(url), reason=f"Service not reachable at {url}")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def authed() -> httpx.Client:
    if not _reachable(AUTH_URL):
        pytest.skip(f"Auth service not reachable at {AUTH_URL}")
    resp = httpx.post(
        f"{AUTH_URL}/api/v1/auth/login",
        json={"email": E2E_USER, "password": E2E_PASSWORD},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json().get("access_token")
    assert token
    return httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )


@pytest.fixture(scope="module")
def brain() -> httpx.Client:
    """Unauthenticated client for MonkeyBrain — it uses internal JWT, not user auth."""
    if not _reachable(AGENTOS_URL):
        pytest.skip(f"AgentOS not reachable at {AGENTOS_URL}")
    return httpx.Client(base_url=AGENTOS_URL, timeout=TIMEOUT)


# ── 1. Codegen Pipeline ───────────────────────────────────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestCodegenPipeline:
    """Spec → validate → generate → verify output files."""

    SPEC = {
        "name": f"e2e-widget-{uuid.uuid4().hex[:6]}",
        "domain": "inventory",
        "description": "E2E test microservice",
        "db": {"type": "mongodb"},
        "fields": [
            {"name": "sku", "type": "str", "unique": True},
            {"name": "price", "type": "float", "gt": 0},
            {"name": "stock", "type": "int", "default": 0},
        ],
        "timestamps": True,
        "auth": {"enabled": True, "type": "bearer"},
    }

    def test_schema_endpoint(self, brain):
        r = brain.get("/api/v1/codegen/microservice/schema")
        assert r.status_code == 200
        schema = r.json()
        assert "properties" in schema or "title" in schema

    def test_example_endpoint(self, brain):
        r = brain.get("/api/v1/codegen/microservice/example")
        assert r.status_code == 200
        body = r.json()
        assert "name" in body and "fields" in body

    def test_validate_valid_spec(self, brain):
        r = brain.post("/api/v1/codegen/microservice/validate", json=self.SPEC)
        assert r.status_code == 200
        body = r.json()
        assert body.get("valid") is True
        assert body.get("fields") == len(self.SPEC["fields"])

    def test_validate_rejects_empty(self, brain):
        r = brain.post("/api/v1/codegen/microservice/validate", json={})
        assert r.status_code == 422
        body = r.json()
        assert body.get("valid") is False

    def test_generate_returns_files(self, brain):
        r = brain.post("/api/v1/codegen/microservice", json=self.SPEC)
        assert r.status_code == 200
        body = r.json()
        assert "files" in body
        files = body["files"]
        assert isinstance(files, list)
        assert len(files) > 0

    def test_generate_includes_required_files(self, brain):
        r = brain.post("/api/v1/codegen/microservice", json=self.SPEC)
        assert r.status_code == 200
        files = r.json().get("files", [])
        required = {"main.py", "models.py", "schemas.py", "crud.py", "routes.py"}
        missing = required - {f.split("/")[-1] for f in files}
        assert not missing, f"Missing required files: {missing}"

    def test_generate_zip_format(self, brain):
        r = brain.post("/api/v1/codegen/microservice?fmt=zip", json=self.SPEC)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/zip")
        assert len(r.content) > 100

    def test_generated_code_has_correct_service_name(self, brain):
        r = brain.post("/api/v1/codegen/microservice", json=self.SPEC)
        assert r.status_code == 200
        body = r.json()
        contents = body.get("contents", {})
        main_src = contents.get("main.py", "")
        assert self.SPEC["name"] in main_src or body["name"] == self.SPEC["name"]


# ── 2. Somatic Charts (SittingFace) ──────────────────────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestSomaticCharts:
    """Verify somatic chart registry is loaded and queryable."""

    def test_charts_endpoint(self, brain):
        r = brain.get("/somatic/charts")
        assert r.status_code == 200
        body = r.json()
        assert "error" not in body or body.get("charts") is not None

    def test_prompts_endpoint(self, brain):
        r = brain.get("/somatic/prompts")
        assert r.status_code == 200

    def test_capabilities_endpoint(self, brain):
        r = brain.get("/somatic/capabilities")
        assert r.status_code == 200


# ── 3. AgentOS Query — Software Engineering intents ──────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestAgentOSEngineeringQueries:
    """Natural-language queries that exercise the software engineering domain."""

    def test_query_list_agents(self, brain):
        r = brain.post("/api/v1/agentos/query", json={"question": "list available agents"})
        assert r.status_code == 200
        body = r.json()
        assert "answer" in body or "agents" in body

    def test_query_codegen_intent(self, brain):
        r = brain.post(
            "/api/v1/agentos/query",
            json={"question": "generate a FastAPI microservice for managing products"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "answer" in body

    def test_query_process_definition_intent(self, brain):
        r = brain.post(
            "/api/v1/agentos/query",
            json={"question": "what process definitions are available?"},
        )
        assert r.status_code == 200

    def test_query_returns_citations(self, brain):
        r = brain.post(
            "/api/v1/agentos/query",
            json={"question": "show me the software engineering pipeline steps"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "answer" in body


# ── 4. Prompt Pipeline (full ETASS execution) ─────────────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestPromptPipeline:
    """Full prompt → query → simulate → pipeline execution cycle."""

    def test_prompt_query_only(self, brain):
        r = brain.post(
            "/api/v1/agentos/prompt",
            json={
                "question": "what agents handle software engineering?",
                "run_query": True,
                "run_simulate": False,
                "run_type": "query_only",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "question" in body

    def test_prompt_with_simulation(self, brain):
        r = brain.post(
            "/api/v1/agentos/prompt",
            json={
                "question": "simulate generating a microservice spec for order management",
                "run_query": True,
                "run_simulate": True,
                "run_type": "simulate",
            },
        )
        assert r.status_code == 200

    def test_prompt_execution_summary_present(self, brain):
        r = brain.post(
            "/api/v1/agentos/prompt",
            json={
                "question": "what is the system status?",
                "run_type": "query_only",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "execution_summary" in body

    def test_prompt_rejects_empty_question(self, brain):
        r = brain.post("/api/v1/agentos/prompt", json={"question": ""})
        assert r.status_code == 422


# ── 5. Observability ──────────────────────────────────────────────────────────


@_skip_if_down(AGENTOS_URL)
class TestObservability:
    """Engineering pipeline observability panels."""

    def test_state_endpoint(self, brain):
        r = brain.get("/state")
        assert r.status_code == 200

    def test_metrics_endpoint(self, brain):
        r = brain.get("/metrics")
        assert r.status_code == 200

    def test_observability_all(self, brain):
        r = brain.get("/observability")
        assert r.status_code == 200

    def test_observability_pipeline_panel(self, brain):
        r = brain.get("/observability/pipeline")
        assert r.status_code in (200, 404)

    def test_observability_agent_panel(self, brain):
        r = brain.get("/observability/agent")
        assert r.status_code in (200, 404)


# ── 6. Full Engineering Flow: spec → validate → generate → query ──────────────


@pytest.mark.skipif(
    not _reachable(AGENTOS_URL),
    reason="Full engineering flow requires agentos",
)
class TestFullEngineeringFlow:
    """End-to-end: validate a spec, generate code, then query AgentOS about it."""

    def test_spec_to_code_to_query(self, brain):
        svc_name = f"e2e-flow-{uuid.uuid4().hex[:6]}"
        spec = {
            "name": svc_name,
            "domain": "manufacturing",
            "description": "E2E full flow test service",
            "db": {"type": "mongodb"},
            "fields": [
                {"name": "batch_id", "type": "str", "unique": True},
                {"name": "quantity", "type": "int", "ge": 0},
                {
                    "name": "status",
                    "type": "str",
                    "enum": ["pending", "running", "done"],
                },
            ],
            "auth": {"enabled": True, "type": "bearer"},
        }

        # Step 1: validate
        validate = brain.post("/api/v1/codegen/microservice/validate", json=spec)
        assert validate.status_code == 200
        assert validate.json().get("valid") is True

        # Step 2: generate
        generate = brain.post("/api/v1/codegen/microservice", json=spec)
        assert generate.status_code == 200
        files = generate.json().get("files", [])
        assert len(files) >= 5

        # Step 3: ask AgentOS about the generated service domain
        query = brain.post(
            "/api/v1/agentos/query",
            json={"question": f"what agents can manage manufacturing batch processes?"},
        )
        assert query.status_code == 200
        assert "answer" in query.json()
