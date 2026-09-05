"""Gate 4 — unit tests for src/monkey_brain/api/idempotency.py (Gate 2/ADR-009).

Adapts the exact scenario this module was live-verified against during
Gate 2 (a real TestClient app, retry/conflict/no-key behavior) into a
permanent, isolated unit test — using the in-memory backend directly
(IDEMPOTENCY_STORE_BACKEND=memory) rather than a live server, and a bare
FastAPI app rather than the full MonkeyBrain app, so this has no external
dependencies at all.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.monkey_brain.api.idempotency import (
    IdempotencyStore, get_idempotency_store, idempotent, request_fingerprint,
)


@pytest.fixture(autouse=True)
def _memory_backend_env(monkeypatch):
    """Actor Runtime review, Phase 4: IDEMPOTENCY_STORE_BACKEND=memory
    used to be set via a bare, module-level `os.environ.setdefault(...)`
    -- set once at import time and NEVER reverted, since Python caches
    module imports for the whole pytest session. Combined with
    IdempotencyStore's own process-wide singleton (_reset_store() below
    only clears it at the START of each of THIS file's tests, never
    after the LAST one), this leaked a memory-backed singleton into every
    later test in the same session -- confirmed live: a real, fail-closed
    (COGNITIVEOS_ALLOW_INSECURE_DEV_MODE correctly unset) ensure_governed
    call in a LATER, unrelated test file got denied with "idempotency
    store missing" because the memory backend is deliberately forbidden
    in fail-closed mode (api/idempotency.py's own policy), and the
    leaked singleton never got a chance to reconstruct against that
    later test's own (correct) environment. monkeypatch.setenv here
    reverts automatically after each test, closing that leak."""
    monkeypatch.setenv("IDEMPOTENCY_STORE_BACKEND", "memory")
    yield
    IdempotencyStore._instance = None


def _fake_auth() -> str:
    return "test-user"


class _OrderBody(BaseModel):
    # Module-level, not nested in _make_app(): FastAPI resolves a route's
    # parameter types via typing.get_type_hints() against the wrapped
    # function's __globals__ — a Pydantic model defined inside a function
    # only lives in that function's local scope and is NOT resolvable
    # there, so a nested class here silently breaks dependency injection
    # (confirmed while writing this file: FastAPI treats `body` as a
    # missing query param instead of a JSON body). Matches the layout
    # every real route in this codebase already uses (models imported
    # from gateway_models.py, never defined inline).
    name: str


class _NonDictOut(BaseModel):
    n: int


def _make_app(calls: dict):
    app = FastAPI()

    @app.post("/orders")
    @idempotent("orders.create")
    async def create_order(body: _OrderBody, request: Request, user_id: str = Depends(_fake_auth)) -> dict:
        calls["n"] += 1
        return {"order_id": f"ORD-{calls['n']}", "name": body.name}

    return app


def _reset_store() -> None:
    """Each test needs a genuinely fresh store — IdempotencyStore is a
    process-wide singleton (mirrors RunStore's own singleton shape), so
    clear its instance between tests rather than relying on unique keys
    everywhere, matching how the module itself documents the singleton."""
    IdempotencyStore._instance = None


def test_retry_with_same_key_and_body_returns_cached_result_without_reexecuting():
    _reset_store()
    calls = {"n": 0}
    client = TestClient(_make_app(calls))

    r1 = client.post("/orders", json={"name": "a"}, headers={"Idempotency-Key": "k1"})
    r2 = client.post("/orders", json={"name": "a"}, headers={"Idempotency-Key": "k1"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert calls["n"] == 1


def test_same_key_with_different_body_is_rejected():
    _reset_store()
    calls = {"n": 0}
    client = TestClient(_make_app(calls))

    r1 = client.post("/orders", json={"name": "a"}, headers={"Idempotency-Key": "k2"})
    r2 = client.post("/orders", json={"name": "DIFFERENT"}, headers={"Idempotency-Key": "k2"})

    assert r1.status_code == 200
    assert r2.status_code == 409
    assert "different request" in r2.json()["detail"]
    assert calls["n"] == 1


def test_no_key_always_executes_fresh():
    _reset_store()
    calls = {"n": 0}
    client = TestClient(_make_app(calls))

    r1 = client.post("/orders", json={"name": "b"})
    r2 = client.post("/orders", json={"name": "b"})

    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert calls["n"] == 2


def test_different_keys_both_execute_independently():
    _reset_store()
    calls = {"n": 0}
    client = TestClient(_make_app(calls))

    r1 = client.post("/orders", json={"name": "c"}, headers={"Idempotency-Key": "k3"})
    r2 = client.post("/orders", json={"name": "c"}, headers={"Idempotency-Key": "k4"})

    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert calls["n"] == 2


def test_reserve_is_atomic_second_concurrent_claim_is_rejected():
    """The race condition fix specifically: two attempts to reserve the
    SAME key before either completes — only one may proceed."""
    _reset_store()
    store = get_idempotency_store()

    claimed_1, existing_1 = store.reserve("scope:k5", "hash-a")
    claimed_2, existing_2 = store.reserve("scope:k5", "hash-a")

    assert claimed_1 is True
    assert existing_1 is None
    assert claimed_2 is False
    assert existing_2 is not None
    assert existing_2.state == "in_progress"


def test_release_allows_a_subsequent_reservation():
    """A handler that raises must release its claim — matching the
    decorator's except-and-release behavior — so a genuine retry (or a
    second concurrent attempt after the first crashed) can proceed."""
    _reset_store()
    store = get_idempotency_store()

    claimed_1, _ = store.reserve("scope:k6", "hash-a")
    assert claimed_1 is True
    store.release("scope:k6")

    claimed_2, existing_2 = store.reserve("scope:k6", "hash-a")
    assert claimed_2 is True
    assert existing_2 is None


def test_complete_then_reserve_returns_the_completed_record():
    _reset_store()
    store = get_idempotency_store()

    store.reserve("scope:k7", "hash-a")
    store.complete("scope:k7", "hash-a", {"result": "ok"})

    claimed, existing = store.reserve("scope:k7", "hash-a")
    assert claimed is False
    assert existing is not None
    assert existing.state == "completed"
    assert existing.response_body == {"result": "ok"}


def test_request_fingerprint_is_stable_and_body_sensitive():
    h1 = request_fingerprint("POST", "/orders", {"name": "a"})
    h2 = request_fingerprint("POST", "/orders", {"name": "a"})
    h3 = request_fingerprint("POST", "/orders", {"name": "b"})

    assert h1 == h2
    assert h1 != h3


def test_handler_returning_pydantic_model_is_cached_and_replayed():
    """Production Hardening (API-level idempotency): a Pydantic
    response-model instance (e.g. PromptResponse, not a plain dict) now
    IS cached — the whole point of wiring @idempotent onto /prompt, which
    returns exactly this shape. A retry gets the first attempt's response
    back, reconstructed as the same concrete type, and the handler is
    never called a second time."""
    _reset_store()
    calls = {"n": 0}
    app = FastAPI()

    @app.post("/x")
    @idempotent("x.create")
    async def handler(body: _OrderBody, request: Request, user_id: str = Depends(_fake_auth)) -> _NonDictOut:
        calls["n"] += 1
        return _NonDictOut(n=calls["n"])

    client = TestClient(app)
    r1 = client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k8"})
    r2 = client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k8"})

    assert r1.json()["n"] == r2.json()["n"] == 1
    assert calls["n"] == 1


def test_handler_returning_something_uncacheable_releases_rather_than_caches():
    """A return value that's neither a dict nor a Pydantic model (e.g. a
    bare string FastAPI happens to accept) genuinely can't be safely
    replayed — the decorator releases instead of caching it, so a retry
    re-executes rather than serving something it can't reconstruct."""
    _reset_store()
    calls = {"n": 0}
    app = FastAPI()

    @app.post("/x")
    @idempotent("x.create")
    async def handler(body: _OrderBody, request: Request, user_id: str = Depends(_fake_auth)) -> str:
        calls["n"] += 1
        return f"order-{calls['n']}"

    client = TestClient(app)
    r1 = client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k9"})
    r2 = client.post("/x", json={"name": "a"}, headers={"Idempotency-Key": "k9"})

    assert r1.json() != r2.json()
    assert calls["n"] == 2


def test_body_param_named_something_other_than_body_is_still_found():
    """/prompt (and any other route) names its body param `payload`, not
    `body` — the decorator must still find and hash it, not silently skip
    idempotency for every route that doesn't use the literal name `body`."""
    _reset_store()
    calls = {"n": 0}
    app = FastAPI()

    @app.post("/y")
    @idempotent("y.create")
    async def handler(payload: _OrderBody, request: Request, user_id: str = Depends(_fake_auth)) -> dict:
        calls["n"] += 1
        return {"order_id": f"ORD-{calls['n']}", "name": payload.name}

    client = TestClient(app)
    r1 = client.post("/y", json={"name": "a"}, headers={"Idempotency-Key": "k10"})
    r2 = client.post("/y", json={"name": "a"}, headers={"Idempotency-Key": "k10"})
    r3 = client.post("/y", json={"name": "different"}, headers={"Idempotency-Key": "k10"})

    assert r1.json() == r2.json()
    assert calls["n"] == 1
    assert r3.status_code == 409
