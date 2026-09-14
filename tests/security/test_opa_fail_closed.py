"""OPA must fail CLOSED when configured-but-unavailable (Doot audit
P1-6). Exercises the real implementation
(packages/cerebellum/cerebellum/capabilities/security/opa_client.py::
evaluate_full) — services/common/opa.py is a thin re-export of this same
function when the cerebellum package is importable (confirmed true in
this environment), so testing this module is testing what actually runs.

Before this fix, "OPA_URL unset" and "OPA_URL set but the request
itself failed" shared one `default_allow` fallback (default True) —
meaning a protected action behind require_opa() would silently be
ALLOWED the moment OPA went down, timed out, or returned garbage. That
is now split: unset stays fail-open (an explicit deployment choice,
unchanged), but configured-and-failing fails CLOSED.
"""

from __future__ import annotations

import pytest


class _FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient —
    no respx/httpx-mock dependency in this repo's test environment."""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        raise_exc: Exception | None = None,
        **_kw,
    ) -> None:
        self._response = response
        self._raise_exc = raise_exc

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def post(self, url: str, json: dict) -> _FakeResponse:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


def _patch_client(
    monkeypatch,
    module,
    *,
    response: _FakeResponse | None = None,
    raise_exc: Exception | None = None,
) -> None:
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(response=response, raise_exc=raise_exc)

    monkeypatch.setattr(module.httpx, "AsyncClient", _factory)


@pytest.fixture()
def opa_module(monkeypatch):
    import cerebellum.capabilities.security.opa_client as m

    monkeypatch.setattr(m, "_OPA_URL", "http://opa.internal:8181")
    return m


class TestOpaFailClosed:
    @pytest.mark.asyncio
    async def test_1_opa_allow(self, opa_module, monkeypatch):
        _patch_client(monkeypatch, opa_module, response=_FakeResponse(200, {"result": True}))
        result = await opa_module.evaluate_full("agentos/allow", {})
        assert result["allowed"] is True
        assert result["source"] == "opa"

    @pytest.mark.asyncio
    async def test_2_opa_deny(self, opa_module, monkeypatch):
        _patch_client(monkeypatch, opa_module, response=_FakeResponse(200, {"result": False}))
        result = await opa_module.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False
        assert result["source"] == "opa"

    @pytest.mark.asyncio
    async def test_3_opa_unreachable_fails_closed(self, opa_module, monkeypatch):
        _patch_client(monkeypatch, opa_module, raise_exc=ConnectionError("connection refused"))
        result = await opa_module.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False, "OPA configured but unreachable must DENY, not silently allow"
        assert result["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_4_opa_timeout_fails_closed(self, opa_module, monkeypatch):
        import httpx

        _patch_client(monkeypatch, opa_module, raise_exc=httpx.TimeoutException("timed out"))
        result = await opa_module.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False, "OPA configured but timing out must DENY, not silently allow"
        assert result["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_5_opa_malformed_response_fails_closed(self, opa_module, monkeypatch):
        # Neither a bool nor a dict "result" -- and a non-200 status, the
        # other real "something went wrong" shape this client must not
        # silently treat as a policy answer.
        _patch_client(monkeypatch, opa_module, response=_FakeResponse(500, {"error": "internal"}))
        result = await opa_module.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False
        assert result["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_opa_url_unset_fails_closed_by_default(self, monkeypatch):
        import cerebellum.capabilities.security.opa_client as m

        monkeypatch.setattr(m, "_OPA_URL", "")
        result = await m.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False
        assert result["source"] == "skip"

    @pytest.mark.asyncio
    async def test_opa_url_unset_stays_fail_open_explicit_dev_mode(self, monkeypatch):
        import cerebellum.capabilities.security.opa_client as m

        monkeypatch.setattr(m, "_OPA_URL", "")
        result = await m.evaluate_full("agentos/allow", {}, default_allow=True)
        assert result["allowed"] is True
        assert result["source"] == "skip"

    @pytest.mark.asyncio
    async def test_explicit_opt_out_still_allows_documented_low_risk_fallback(self, opa_module, monkeypatch):
        """fail_closed_on_error=False remains available for a caller
        that explicitly, deliberately wants the old behavior for a
        specific low-risk operation -- proving the escape hatch exists
        without any current caller silently relying on it."""
        _patch_client(monkeypatch, opa_module, raise_exc=ConnectionError("down"))
        result = await opa_module.evaluate_full("agentos/allow", {}, default_allow=True, fail_closed_on_error=False)
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_require_opa_denies_when_configured_and_unreachable(self, opa_module, monkeypatch):
        """End-to-end through the real FastAPI dependency (services/
        common/opa.py::require_opa), not just evaluate_full directly."""
        from fastapi import HTTPException, Request
        from services.common.opa import require_opa

        _patch_client(monkeypatch, opa_module, raise_exc=ConnectionError("down"))

        check = require_opa("agentos/allow", action="test", resource="test")
        scope = {"type": "http", "headers": [], "method": "GET", "path": "/"}
        request = Request(scope)
        with pytest.raises(HTTPException) as exc:
            await check(request)
        assert exc.value.status_code == 403
