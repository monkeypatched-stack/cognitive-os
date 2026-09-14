from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Auth service Settings (domains/manufacturing/.../config.py) requires these
# secrets at import time. CI has no .env — provide deterministic test values
# before any test module imports services.auth or api.main.
os.environ.setdefault("ACCESS_TOKEN_SECRET", "unit-test-hmac-key-not-a-placeholder!!")
os.environ.setdefault("REFRESH_TOKEN_SECRET", "unit-test-hmac-key-not-a-placeholder!!")
os.environ.setdefault("API_GATEWAY_REQUIRED", "false")
# Unit tests that do not boot Redis/OPA use the explicit local-dev bypass.
# Production and tests/security/test_soc2_control_remediation.py unset this.
os.environ.setdefault("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")

# PlanetaryRuntime.build_execution_engine() resolves the "grocery" vertical at
# init — register it once for the whole test session.
try:  # pragma: no cover
    import src.monkey_brain.kernel.domains.grocery  # noqa: F401
except Exception:
    pass

_root = os.path.dirname(os.path.dirname(__file__))
for p in (
    "src",
    "packages/cerebellum",
    "packages/broca",
    "packages/soma-cli",
    "domains/manufacturing/knowledge",
):
    _full = os.path.join(_root, p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

# Pre-cache the REAL broca package (packages/broca/broca — a superset with agents/mesh/
# registry) so the redundant src/broca stub can never shadow it via later sys.path mutations
# or import ordering. Without this, `broca.mesh` / `broca.registry` intermittently fail to
# import depending on which test ran first.
try:  # pragma: no cover
    import broca  # noqa: F401

    if not hasattr(broca, "agents"):
        for _m in [k for k in list(sys.modules) if k == "broca" or k.startswith("broca.")]:
            del sys.modules[_m]
        _bp = os.path.join(_root, "packages", "broca")
        if _bp in sys.path:
            sys.path.remove(_bp)
        sys.path.insert(0, _bp)
        import broca  # noqa: F401
except Exception:
    pass


# Integration-tier suites that need a live server (AGENTOS_URL:8031) or a real LLM. Skipped
# by default so `pytest tests/` is deterministic; run them with RUN_INTEGRATION=1 against a
# started stack.
_INTEGRATION_PATHS = (
    "e2e/test_soma_compile",
    "e2e/test_software_engineering",
    "e2e/test_e2e",
    "e2e/cognitive_loop",
    "load/test_load_and_soak",
    "security/test_api_fuzz",
    "benchmarks/",
    "test_phase8_autonomous_actors.py",
    "test_phase9_event_driven_observation.py",
)


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_INTEGRATION"):
        return
    skip = pytest.mark.skip(reason="integration test (needs live server/LLM); set RUN_INTEGRATION=1")
    for item in items:
        path = str(getattr(item, "fspath", "")).replace("\\", "/")
        if any(seg in path for seg in _INTEGRATION_PATHS):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _hermetic_world_tensor_path(monkeypatch):
    """A developer's local .env can set MB_WORLD_TENSOR_PATH to a real,
    persisted file (kernel/compile/world_tensor.py). world_tensor.
    reset_for_test() only clears the in-memory singleton — the next
    get_world_tensor() call still reloads from that on-disk file, so a
    stale local .data/world_tensor.json leaks real transitions into what
    tests assume is a fresh world. Tests must be hermetic regardless of
    what's on a developer's disk, so this env var never applies here."""
    monkeypatch.delenv("MB_WORLD_TENSOR_PATH", raising=False)
    monkeypatch.delenv("AGENTOS_WORLD_SHARD_DIR", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_tenant_context():
    """Tenant / system-scope state lives in ContextVars, which persist across tests in the same
    thread. Reset before each test so one test's tenant can't leak into the next and mask a
    real isolation failure."""
    try:
        from services.common.tenant_scope import set_tenant, _unscoped

        set_tenant("")
        _unscoped.set(False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _flush_shared_redis():
    """PlanetaryRuntime, TimelineStore, RunStore, IdempotencyStore, and
    others all connect to the SAME real Redis (REDIS_HOST/REDIS_PORT,
    db=0 — the identical instance a live dev server uses) with no
    test/dev separation. _reset_timeline_store below only clears the
    process-local singleton REFERENCE; the next TimelineStore() still
    reconnects to and reads whatever is already in Redis. Same gap
    _hermetic_world_tensor_path (above) already documents for the world
    tensor file, just for Redis instead of disk.

    Confirmed as a real, live bug (Gate 11 pytest triage, 2026-08): a
    test constructing PlanetaryRuntime() got "Actor ... was not reached
    by the planetary hierarchy" because _load_geography() loaded
    left-over geography from an EARLIER test's PlanetaryRuntime lineage
    in the same Redis DB — the newly-registered actor's Space was real,
    but not reachable from that stale geography's Planet root. Flushing
    before every test (not just once per session) is required: within
    a single run, one test file's writes were observed leaking into a
    later file's fresh PlanetaryRuntime() (46 occurrences of this
    failure signature across the suite).

    Uses `redis-cli flushdb` as a SUBPROCESS rather than the redis-py
    library. This is not the first mechanism tried — three separate
    redis-py-based approaches (fresh connection per test, then a cached
    client + socket_timeout, then single_connection_client=True to
    bypass redis-py's connection pool) each hung the suite
    unpredictably, at a different test every time, with CPU time barely
    moving despite minutes of wall-clock time. Each was proven to be
    the actual cause by disabling it and watching the suite run clean
    past every point it had stalled at before — but the exact mechanism
    inside redis-py was never found. Shelling out avoids the question
    entirely: `redis-cli` is a separate, short-lived OS process per
    call (~7ms observed locally), so nothing about redis-py's client
    state, connection pool, or thread interactions can be involved
    no matter what was actually wrong with them.
    subprocess.run's own timeout (2s) still bounds this the same way
    socket_timeout was meant to for the redis-py version, in case
    redis-cli itself ever hangs for an unrelated reason.

    Deliberately flushes the WHOLE db (not just monkeybrain:*/timeline:*
    prefixes) — narrower flushing would need to be kept in sync with
    every new key prefix this session's stores use, and missing one
    reintroduces exactly this bug silently. This DOES mean: never run
    pytest against the same Redis a live dev server is using — it will
    wipe the dev server's persisted state. Every server restart script
    used this session already stops the server first for this reason.
    """
    try:
        import os
        import subprocess

        subprocess.run(
            [
                "redis-cli",
                "-h",
                os.getenv("REDIS_HOST", "localhost"),
                "-p",
                os.getenv("REDIS_PORT", "6379"),
                "flushdb",
            ],
            timeout=2,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_timeline_store():
    """TimelineStore (kernel/timeline/store.py) is a process-wide singleton
    — tests that construct bare BeliefState()/actor objects with no
    explicit actor_id all share the same "" actor_id key, so one test's
    GoalRecord/Presence/etc. writes would otherwise leak into the next
    test's assertions about a "fresh" actor. Same class of fix as
    _reset_tenant_context above."""
    try:
        from src.monkey_brain.kernel.timeline.store import TimelineStore

        TimelineStore.reset_for_testing()
    except Exception:
        pass
    yield


class _GenericPlanningBackend:
    """Test-only stand-in for a real LLM backend (kernel/execute/provider/
    model_backend.py::ModelBackend). Parses just enough of the prompt (the
    goal name) to return a plausible single-step plan — good enough for
    the many tests that construct CognitiveRuntime()/LLMPlanner() with no
    explicit planner and don't care about specific plan content, just that
    planning succeeds. Tests that DO care about plan content inject their
    own fake backend explicitly (see test_llm_planner.py /
    test_planning_basket.py) — this fixture never overrides an explicitly
    constructed LLMPlanner(backend=...)."""

    async def complete(self, prompt, system="", max_tokens=None, **kwargs):
        # Must be async: the real ModelBackend.complete() is (model_backend.py),
        # and kernel/pipeline/llm_planner.py's CircuitBreaker.acall() always
        # does `await func(*args, **kwargs)` -- a synchronous complete() here
        # returns the plan JSON string directly, and awaiting that string
        # raised "'str' object can't be awaited" for any test that reaches
        # a real planning tick through this fixture (confirmed live,
        # test_ec001_simple_purchase.py -- a plain actor.tick() call, no
        # custom backend injected, hit this on every run).
        import re

        match = re.search(r"Goal:\s*(\S+)", prompt)
        goal_name = match.group(1) if match else "goal"
        return json.dumps(
            {
                "steps": [
                    {
                        "action": f"achieve_{goal_name}",
                        "description": f"Achieve {goal_name}",
                        "expected_outcome": goal_name,
                        "cost": 0.1,
                        "confidence": 0.8,
                    }
                ],
                "summary": f"Plan to achieve {goal_name}",
                "confidence": 0.8,
            }
        )


@pytest.fixture(autouse=True)
def _default_test_llm_backend(monkeypatch):
    """Without this, every bare LLMPlanner() constructed anywhere in the
    test suite (directly, or via CognitiveRuntime()'s default
    planning_engine) would call a real LLM provider over the network.
    LLMPlanner.__init__ resolves get_backend() at call time (not a cached
    module-level import), so patching the attribute here is picked up by
    every LLMPlanner() constructed during the test session."""
    monkeypatch.setattr(
        "src.monkey_brain.kernel.execute.provider.model_backend.get_backend",
        lambda: _GenericPlanningBackend(),
    )
    yield


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_keystore(tmp_path):
    from cerebellum.keystore import SecureKeystore

    return SecureKeystore(
        master_key="test-master-key-for-testing-only-32b!",
        db_path=str(tmp_path / "keystore.json"),
    )


@pytest.fixture
def fake_settings():
    # Patch both the config module AND every module that has already bound
    # `settings` from it via `from services.common.config import settings`.
    with (
        patch("services.common.config.settings") as s,
        patch("services.auth.helpers.tokens.settings", s),
    ):
        s.ACCESS_TOKEN_SECRET = "test-access-secret"
        s.REFRESH_TOKEN_SECRET = "test-refresh-secret"
        s.ALGORITHM = "HS256"
        s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        s.REFRESH_TOKEN_EXPIRE_DAYS = 7
        # Real default (services/common/config.py) — lets tests that
        # exercise the jti-revocation path (services.auth.helpers.
        # revocation, lazily-constructed) reach an actual local Redis
        # instead of failing open on a MagicMock URL.
        s.REDIS_URL = "redis://localhost:6379"
        yield s


@pytest.fixture
def make_token(fake_settings):
    from services.auth.helpers.tokens import create_access_token

    return create_access_token
