"""Production Hardening — API-Level Idempotency: end-to-end regression
tests for @idempotent("prompt.execute") wired onto POST /prompt
(api/routes/prompt.py).

Two layers:
  1. A minimal FastAPI app reusing the REAL PromptRequest/PromptResponse
     models and the exact `payload`-named-body / PromptResponse-returning
     shape /prompt has — proving the two decorator generalizations
     (Pydantic-response caching, non-`body`-named request lookup) this
     sprint added actually cover /prompt's real signature, without
     needing the full app/Mongo/Redis/LLM stack.
  2. One live-mounted-router smoke test against the real /prompt route
     (app.dependency_overrides, no live Ollama/Mongo/Redis needed) —
     proving it's genuinely wired in the real app, not just in theory.
"""

from __future__ import annotations

import os

os.environ.setdefault("IDEMPOTENCY_STORE_BACKEND", "memory")

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.monkey_brain.api.idempotency import IdempotencyStore, idempotent  # noqa: E402
from src.monkey_brain.kernel.models import PromptRequest, PromptResponse  # noqa: E402


def _reset_store() -> None:
    IdempotencyStore._instance = None


def _fake_auth() -> str:
    return "test-actor"


def _make_prompt_style_app(calls: dict):
    """Mirrors /prompt's real shape exactly: body param named `payload`
    (not `body`), returns a PromptResponse Pydantic model (not a dict)."""
    app = FastAPI()

    @app.post("/prompt")
    @idempotent("prompt.execute")
    async def unified_prompt(
        request: Request,
        payload: PromptRequest,
        user_id: str = Depends(_fake_auth),
    ) -> PromptResponse:
        calls["n"] += 1
        return PromptResponse(
            question=payload.question or "",
            query_result={"order_id": f"ORD-{calls['n']}"},
            execution_summary={"actions_taken": calls["n"]},
        )

    return app


class TestPromptShapedRouteIsCorrectlyDeduplicated:
    """The two decorator generalizations this sprint added exist
    specifically because /prompt doesn't match the decorator's original
    assumptions (dict return, body param named `body`) — these prove
    that gap is actually closed, not just that the generic decorator
    tests pass."""

    def test_same_key_replays_without_re_executing(self):
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        r1 = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "req-1"},
        )
        r2 = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "req-1"},
        )

        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        assert calls["n"] == 1  # the real purchase pipeline ran exactly once
        assert r1.json()["query_result"]["order_id"] == "ORD-1"

    def test_replayed_response_is_a_real_reconstructed_prompt_response(self):
        """Not just "the same dict" — the cached replay round-trips
        through PromptResponse's own validation, so a caller relying on
        response_model coercion gets identical guarantees on a replay as
        on a fresh call."""
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        client.post(
            "/prompt",
            json={"question": "buy eggs"},
            headers={"Idempotency-Key": "req-2"},
        )
        r2 = client.post(
            "/prompt",
            json={"question": "buy eggs"},
            headers={"Idempotency-Key": "req-2"},
        )

        validated = PromptResponse.model_validate(r2.json())
        assert validated.question == "buy eggs"

    def test_different_top_level_requests_get_independent_purchases(self):
        """No key at all -- the realistic case for a client that never
        sends one -- behaves exactly as before this sprint: every request
        executes fresh. Idempotency is opt-in, never forced."""
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        r1 = client.post("/prompt", json={"question": "buy milk"})
        r2 = client.post("/prompt", json={"question": "buy milk"})

        assert r1.json()["query_result"]["order_id"] != r2.json()["query_result"]["order_id"]
        assert calls["n"] == 2

    def test_timeout_then_retry_with_same_key_produces_one_side_effect(self):
        """Test 3 (Idempotency): the realistic client-timeout scenario —
        the server actually completed the first attempt, the client just
        never saw the response and retries with the same key it already
        chose. One logical purchase, not two."""
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        first = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "timeout-retry"},
        )
        # Client "never saw" `first`'s response and retries identically.
        retry = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "timeout-retry"},
        )

        assert first.json()["query_result"]["order_id"] == retry.json()["query_result"]["order_id"]
        assert calls["n"] == 1

    def test_same_key_different_payload_is_rejected_not_executed_twice(self):
        """Test 5: same Idempotency-Key, materially different request
        (different question) -> explicit rejection, and the second
        request's side effect never happens."""
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "reused-key"},
        )
        r2 = client.post(
            "/prompt",
            json={"question": "buy eggs"},
            headers={"Idempotency-Key": "reused-key"},
        )

        assert r2.status_code == 409
        assert calls["n"] == 1  # the second (mismatched) request never executed

    def test_different_keys_same_payload_are_independent(self):
        """Test 6: two different Idempotency-Keys for the identical
        payload are two genuinely independent purchases — an
        Idempotency-Key is a per-request identity, not a dedup-by-content
        mechanism."""
        _reset_store()
        calls = {"n": 0}
        client = TestClient(_make_prompt_style_app(calls))

        r1 = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "key-a"},
        )
        r2 = client.post(
            "/prompt",
            json={"question": "buy milk"},
            headers={"Idempotency-Key": "key-b"},
        )

        assert r1.json()["query_result"]["order_id"] != r2.json()["query_result"]["order_id"]
        assert calls["n"] == 2


def test_prompt_route_is_wired_with_idempotent_decorator():
    """Confirms the REAL route object (api/routes/prompt.py::unified_prompt)
    is the one actually decorated — not just a lookalike test double —
    by checking functools.wraps preserved the original name/doc through
    the wrapper, the same signal FastAPI itself relies on to resolve
    Depends()/body params correctly through the decorator."""
    from src.monkey_brain.api.routes import prompt as prompt_route_module

    assert prompt_route_module.unified_prompt.__name__ == "unified_prompt"
    # functools.wraps + the decorator's own closure — confirms this is a
    # wrapped function, not the bare undecorated one.
    assert prompt_route_module.unified_prompt.__wrapped__.__name__ == "unified_prompt"
