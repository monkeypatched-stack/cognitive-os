"""Broken access control — every src/monkey_brain/api/routes/ endpoint
must sit behind a real auth dependency.

Companion to test_route_authorization.py, which already proves this
same principle for domains/manufacturing/knowledge/services/ — but
that test's own technique (does this FILE mention a guard ANYWHERE)
is file-level, not per-route, and would not have caught the real gap
this test exists for: api/routes/actor_profile.py's
get_account/update_account/list_sessions had zero auth dependency at
all, while the SAME FILE'S login/logout/otp routes correctly used
none (pre-auth, by design) and get_profile/update_profile also
correctly have none (explicitly documented stubs, no real state) — a
file-level "any guard anywhere" check would have been fooled by
login()'s own use of services.auth.helpers.tokens.create_access_token
appearing in the same file. This scans each ROUTE's own function
signature (via AST, not a text regex) for a Depends(...) parameter
naming a real guard.

No global auth middleware exists in this codebase (confirmed:
api/main.py has no such add_middleware call, and no APIRouter(...) in
api/routes/ is constructed with dependencies=[...]) — every route's
guard, if any, is a Depends(...) default on its own function
parameters. That makes this scan authoritative, not a heuristic.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ROUTES_DIR = ROOT / "src" / "monkey_brain" / "api" / "routes"

_GUARD_NAMES = {
    "require_permission",
    "get_current_user",
    "require_self_or_permission",
    "require_opa",
    "require_admin",
    "require_razorpay_webhook_auth",
}

# Routes verified by hand to be genuinely pre-auth or infra, not a gap:
#   - login/logout/otp: you cannot hold a token before authenticating.
#   - profile GET/PUT: explicitly documented stubs (actor_profile.py's own
#     module docstring) that touch no credentials, sessions, or real state.
#   - version/metrics/health: infra discovery, no actor/tenant data, same
#     precedent as test_route_authorization.py's own PUBLIC_ALLOWLIST for
#     the manufacturing domain (health/capabilities endpoints).
#   - policy.py's two routes: intentionally anonymous-callable by design
#     (get_principal is a token-introspection debug endpoint; evaluate_policy's
#     own docstring: "no token present -> anonymous -> typically denied for
#     protected resources" -- the endpoint IS the enforcement point, not
#     something a blanket guard in front of it would improve).
_PUBLIC_ALLOWLIST = {
    ("actor_profile.py", "login"),
    ("actor_profile.py", "logout"),
    ("actor_profile.py", "request_otp"),
    ("actor_profile.py", "verify_otp"),
    ("actor_profile.py", "get_profile"),
    ("actor_profile.py", "update_profile"),
    ("admin.py", "get_version"),
    ("metrics.py", "metrics"),
    ("prompt.py", "prompt_health"),
    ("query.py", "query_health"),
    ("policy.py", "get_principal"),
    ("policy.py", "evaluate_policy"),
}


def _decorator_is_route(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("get", "post", "put", "patch", "delete")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in ("router", "root_router")
    )


def _call_names_in(node: ast.AST) -> set[str]:
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        if isinstance(n, ast.Call):
            target = n.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _route_functions():
    for path in sorted(ROUTES_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_decorator_is_route(d) for d in node.decorator_list):
                continue
            yield path.name, node


def _has_guard(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    all_defaults = list(func.args.defaults) + [d for d in func.args.kw_defaults if d is not None]
    for default in all_defaults:
        # Depends(require_permission(...)) or Depends(get_current_user)
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "Depends":
            if _call_names_in(default) & _GUARD_NAMES:
                return True
    return False


def test_no_monkeybrain_route_exposes_an_endpoint_without_an_auth_guard():
    offenders = []
    for filename, func in _route_functions():
        if (filename, func.name) in _PUBLIC_ALLOWLIST:
            continue
        if _has_guard(func):
            continue
        offenders.append(f"{filename}::{func.name}")
    assert not offenders, (
        "UNAUTHENTICATED ROUTES (no Depends(require_permission(...)) / "
        "get_current_user / require_self_or_permission on the route "
        "function itself):\n  " + "\n  ".join(sorted(offenders))
    )


# Doot audit BYPASS-03 fix: these 5 actor.py routes used to sit behind
# only require_permission("perm-view-actors") — a coarse permission with
# no self-or-consent check at all — unlike their sibling single-actor
# routes (/actors/{id}/beliefs, /memory, /goals) that already used
# require_self_or_permission. Each exposes private cognition (full
# conversation transcript, semantic memory, cognitive-state, goal/belief
# history) for a SPECIFIC named actor_id in the path, so
# require_self_or_permission's user==target_id comparison applies
# directly here (unlike societies.py's multi-actor fan-out route, tested
# separately below with its own filtering logic).
_PRIVATE_COGNITION_ROUTES = {
    ("actors.py", "get_actor_goal_timeline"),
    ("actors.py", "get_actor_belief_timeline"),
    ("actors.py", "get_execution_conversation"),
    ("actors.py", "get_execution_semantic_memory"),
    ("actors.py", "get_actor_cognitive_state"),
}


def _guard_call_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The specific guard function named inside this route's Depends(...)
    default, e.g. "require_permission" or "require_self_or_permission" —
    unlike _has_guard (any guard), this identifies WHICH one."""
    all_defaults = list(func.args.defaults) + [d for d in func.args.kw_defaults if d is not None]
    for default in all_defaults:
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "Depends":
            names = _call_names_in(default) & _GUARD_NAMES
            if names:
                return next(iter(names))
    return None


@pytest.mark.parametrize("filename,func_name", sorted(_PRIVATE_COGNITION_ROUTES))
def test_private_cognition_routes_use_self_or_permission_not_bare_permission(filename, func_name):
    matches = [func for fn, func in _route_functions() if fn == filename and func.name == func_name]
    assert matches, f"{filename}::{func_name} not found — route renamed or removed?"
    guard = _guard_call_name(matches[0])
    assert guard == "require_self_or_permission", (
        f"{filename}::{func_name} must use require_self_or_permission (self-or-authorized), "
        f"got {guard!r} — a bare require_permission lets any perm-view-actors holder read "
        f"ANY actor's private conversation/semantic-memory/cognitive-state with no self-check"
    )


@pytest.mark.parametrize("filename,func_name", sorted(_PUBLIC_ALLOWLIST))
def test_allowlist_entries_still_name_a_real_route(filename, func_name):
    """A stale allowlist entry (the function was renamed or the file
    moved) would silently stop exempting anything — harmless — but
    also silently stop testing anything, which is not: it means a
    typo could sit unnoticed indefinitely. Fails loudly instead."""
    matches = [func for fn, func in _route_functions() if fn == filename and func.name == func_name]
    assert matches, f"{filename}::{func_name} is on the public allowlist but no longer exists — remove it"


# ─────────────────────────────────────────────────────────────
# require_self_or_permission itself — the AST scan above only proves a
# guard is PRESENT on each route; this proves it actually enforces
# correctly, using real signed JWTs (same fake_settings/create_access_
# token fixtures test_auth_bypass.py already establishes for this exact
# purpose), not the guard's own source code.
# ─────────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, path_params: dict, planetary_runtime: Any = None) -> None:
        self.path_params = path_params
        self.state = type("State", (), {})()
        self.app = type(
            "App",
            (),
            {"state": type("AppState", (), {"planetary_runtime": planetary_runtime})()},
        )()


class TestRequireSelfOrPermission:
    @pytest.mark.asyncio
    async def test_the_actor_themselves_is_always_allowed(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_self_or_permission

        # permissions=[] -- exactly how actor_profile.py::login() mints a
        # real actor's own token (see that route's own comment).
        token = create_access_token("alice", "alice@example.com", "actor", permissions=[])
        check = require_self_or_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({"actor_id": "alice"}),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "alice"

    @pytest.mark.asyncio
    async def test_a_different_actor_without_the_permission_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_self_or_permission

        token = create_access_token("bob", "bob@example.com", "actor", permissions=[])
        check = require_self_or_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({"actor_id": "alice"}),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_a_different_caller_WITH_the_permission_is_allowed(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_self_or_permission

        token = create_access_token(
            "admin-1",
            "admin@example.com",
            "operator",
            permissions=["perm-manage-actors"],
        )
        check = require_self_or_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({"actor_id": "alice"}),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "admin-1"

    @pytest.mark.asyncio
    async def test_unauthenticated_caller_is_rejected(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from src.monkey_brain.api.dependencies import require_self_or_permission

        check = require_self_or_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({"actor_id": "alice"}),
                x_user_id=None,
                authorization=None,
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bare_x_user_id_claiming_to_be_the_target_is_rejected_under_enforced_auth(
        self, fake_settings, monkeypatch
    ):
        """CRITICAL regression: a completely unauthenticated caller — no
        Bearer token, no password, no cryptographic proof of anything —
        must NOT be able to act as another actor merely by echoing that
        actor's id back as an X-User-ID header, once auth is enforced.
        Confirmed live before this fix: PUT /actors/{id}/account with
        only X-User-ID: <victim id> (no Authorization header at all)
        successfully overwrote a real actor's real password — a full
        account-takeover primitive. X-User-ID is a self-reported,
        unverified claim; require_permission() already only trusts it in
        dev mode (auth_on is False) — require_self_or_permission() must
        hold the exact same line, not grant a self-claim exception."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from src.monkey_brain.api.dependencies import require_self_or_permission

        check = require_self_or_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({"actor_id": "alice"}),
                x_user_id="alice",
                authorization=None,
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bare_x_user_id_self_claim_is_still_allowed_in_dev_mode(self, fake_settings, monkeypatch):
        """The dev-mode convenience (AGENTOS_AUTH_REQUIRED=false) this
        path exists for must keep working — only the enforced-auth case
        is the vulnerability."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        from src.monkey_brain.api.dependencies import require_self_or_permission

        check = require_self_or_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({"actor_id": "alice"}),
            x_user_id="alice",
            authorization=None,
        )
        assert user == "alice"

    @pytest.mark.asyncio
    async def test_a_different_id_param_name_is_respected(self, fake_settings, monkeypatch):
        """knowledge_graph.py's routes use person_id, not actor_id — the
        guard must compare against WHICHEVER path param it's configured
        for, not a hardcoded name."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_self_or_permission

        token = create_access_token("alice", "alice@example.com", "actor", permissions=[])
        check = require_self_or_permission("perm-manage-actors", id_param="person_id")
        user = await check(
            request=_FakeRequest({"person_id": "alice"}),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "alice"


class TestAuthorizeActingFor:
    """dependencies.authorize_acting_for — the Doot audit BYPASS-02 fix.

    api/routes/orders.py's create_order/pay_for_order/cancel_order_route/
    confirm_receipt_route/request_return all accept a body.actor_id that
    used to be trusted outright behind only perm-manage-actors — any
    caller holding that one coarse permission could silently act as ANY
    actor_id, including placing/paying for orders no real actor
    authorized. This is the exact function orders.py now calls (see that
    file's own module docstring) right before touching any capability —
    these 6 cases are the audit's own required regression matrix, covering
    both the order-creation and the payment call site (the SAME function
    protects both, so proving it here proves both wirings; the two are
    named per-scenario below to keep the audit's own numbering traceable).

    request.state.jwt_permissions is exactly what require_permission()
    (the Depends() every orders.py route already declares) sets on a real
    request from a verified JWT's permissions claim — using it directly
    here is what require_permission's own Bearer-JWT branch does, not a
    parallel identity mechanism.
    """

    @pytest.mark.asyncio
    async def test_1_actor_acts_for_self_is_allowed(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from src.monkey_brain.api.dependencies import authorize_acting_for

        request = _FakeRequest({})
        request.state.jwt_permissions = set()
        await authorize_acting_for(request, user_id="alice", target_actor_id="alice")  # must not raise

    @pytest.mark.asyncio
    async def test_2_actor_attempts_another_actor_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from fastapi import HTTPException
        from src.monkey_brain.api.dependencies import authorize_acting_for

        request = _FakeRequest({})
        request.state.jwt_permissions = set()
        with pytest.raises(HTTPException) as exc:
            await authorize_acting_for(request, user_id="bob", target_actor_id="alice")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_3_authorized_act_on_behalf_order_creation_is_allowed(self, fake_settings, monkeypatch):
        """Order-creation scenario: an admin/agent caller explicitly
        granted ACT_ON_BEHALF_PERMISSION may place an order for alice."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from src.monkey_brain.api.dependencies import (
            ACT_ON_BEHALF_PERMISSION,
            authorize_acting_for,
        )

        request = _FakeRequest({})
        request.state.jwt_permissions = {ACT_ON_BEHALF_PERMISSION}
        await authorize_acting_for(request, user_id="admin-1", target_actor_id="alice")  # must not raise

    @pytest.mark.asyncio
    async def test_4_admin_without_act_on_behalf_is_denied(self, fake_settings, monkeypatch):
        """The core invariant: perm-manage-actors (what every route in
        orders.py already requires just to reach this check) must NOT by
        itself grant act-on-behalf authority — only the distinct
        ACT_ON_BEHALF_PERMISSION does."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from fastapi import HTTPException
        from src.monkey_brain.api.dependencies import authorize_acting_for

        request = _FakeRequest({})
        request.state.jwt_permissions = {"perm-manage-actors"}
        with pytest.raises(HTTPException) as exc:
            await authorize_acting_for(request, user_id="admin-1", target_actor_id="alice")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_5_authorized_act_on_behalf_payment_is_allowed(self, fake_settings, monkeypatch):
        """Payment scenario (pay_for_order's own call site): same
        function, same ACT_ON_BEHALF_PERMISSION, proven independently for
        the specifically P0-flagged payment path."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from src.monkey_brain.api.dependencies import (
            ACT_ON_BEHALF_PERMISSION,
            authorize_acting_for,
        )

        request = _FakeRequest({})
        request.state.jwt_permissions = {ACT_ON_BEHALF_PERMISSION}
        await authorize_acting_for(request, user_id="admin-1", target_actor_id="alice")  # must not raise

    @pytest.mark.asyncio
    async def test_6_unauthorized_payment_for_arbitrary_actor_id_is_denied(self, fake_settings, monkeypatch):
        """The literal exploit BYPASS-02 described: an admin-permissioned
        caller with NO act-on-behalf authority forcing a payment against
        an arbitrary actor_id must be denied before any capability runs."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from fastapi import HTTPException
        from src.monkey_brain.api.dependencies import authorize_acting_for

        request = _FakeRequest({})
        request.state.jwt_permissions = {"perm-manage-actors"}
        with pytest.raises(HTTPException) as exc:
            await authorize_acting_for(request, user_id="admin-1", target_actor_id="some-arbitrary-actor")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_dev_mode_is_unchanged_permissive(self, fake_settings, monkeypatch):
        """AGENTOS_AUTH_REQUIRED=false must behave exactly as permissive
        as every other auth dependency in this module already is in dev
        mode — this fix only tightens the enforced-auth path."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        from src.monkey_brain.api.dependencies import authorize_acting_for

        request = _FakeRequest({})
        request.state.jwt_permissions = set()
        await authorize_acting_for(request, user_id="bob", target_actor_id="alice")  # must not raise


class _FakeDelegationRegistry:
    """Real DelegationRegistry semantics (kernel/society/delegation.py),
    reimplemented minimally rather than imported, so this test doesn't
    need a real SocietyRuntime/membership stack — only grant/revoke/
    effective_delegated_permissions' observable behavior matters here."""

    def __init__(self) -> None:
        self._grants: dict[str, tuple[str, ...]] = {}
        self._revoked: set[str] = set()
        self._expiry: dict[str, float] = {}

    def grant(
        self,
        delegate_actor_id: str,
        permissions: tuple[str, ...],
        valid_until: float | None = None,
    ) -> None:
        self._grants[delegate_actor_id] = permissions
        if valid_until is not None:
            self._expiry[delegate_actor_id] = valid_until

    def revoke(self, delegate_actor_id: str) -> None:
        self._revoked.add(delegate_actor_id)

    def effective_delegated_permissions(self, delegate_actor_id: str) -> tuple[str, ...]:
        import time

        if delegate_actor_id in self._revoked:
            return ()
        expiry = self._expiry.get(delegate_actor_id)
        if expiry is not None and time.time() > expiry:
            return ()
        return self._grants.get(delegate_actor_id, ())


class _FakeSocietyRuntime:
    def __init__(self, registry: _FakeDelegationRegistry) -> None:
        self.delegation_registry = registry


class _FakePlanetaryRuntime:
    def __init__(self, societies: dict[str, list[_FakeSocietyRuntime]]) -> None:
        self._societies = societies

    def _societies_for(self, actor_id: str):
        return tuple(self._societies.get(actor_id, ()))


class TestDelegationEnforcement:
    """Doot audit P1-4 fix: DelegationRegistry.effective_delegated_
    permissions() previously had zero callers at any real authorization
    chokepoint — a real, valid delegation grant would simply never be
    consulted by require_permission/require_self_or_permission. These
    prove it now is, through the SAME single authorization model (no
    second engine — _effective_delegated_permissions only ever WIDENS
    what require_permission already checks, and only when the base
    permission check has already failed)."""

    @pytest.mark.asyncio
    async def test_delegated_permission_is_honored(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission

        registry = _FakeDelegationRegistry()
        registry.grant("agent-1", ("perm-manage-actors",))
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(registry)]})

        # Token itself carries no permissions at all -- authority comes
        # entirely from the delegation grant.
        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({}, planetary_runtime=pr),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "agent-1"

    @pytest.mark.asyncio
    async def test_permission_outside_delegation_scope_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission

        registry = _FakeDelegationRegistry()
        registry.grant("agent-1", ("perm-view-world",))  # a DIFFERENT permission
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(registry)]})

        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({}, planetary_runtime=pr),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_delegation_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission

        registry = _FakeDelegationRegistry()
        registry.grant("agent-1", ("perm-manage-actors",), valid_until=1.0)  # already expired
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(registry)]})

        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({}, planetary_runtime=pr),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_revoked_delegation_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission

        registry = _FakeDelegationRegistry()
        registry.grant("agent-1", ("perm-manage-actors",))
        registry.revoke("agent-1")
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(registry)]})

        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({}, planetary_runtime=pr),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_delegation_is_attributable_to_the_principal(self, fake_settings, monkeypatch):
        """The resolved identity is still the real delegate (agent-1),
        never the delegator — audit/attribution must see who actually
        acted, with the delegation grant as the reason they were allowed
        to."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission

        registry = _FakeDelegationRegistry()
        registry.grant("agent-1", ("perm-manage-actors",))
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(registry)]})

        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({}, planetary_runtime=pr),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "agent-1"

    @pytest.mark.asyncio
    async def test_real_delegation_registry_lookup_is_actually_exercised(self, fake_settings, monkeypatch):
        """Not just the fake — proves _effective_delegated_permissions
        correctly drives the REAL DelegationRegistry class."""
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.tokens import create_access_token
        from src.monkey_brain.api.dependencies import require_permission
        from src.monkey_brain.kernel.society.delegation import DelegationRegistry

        real_registry = DelegationRegistry()
        real_registry.grant(
            membership_id="membership-alice",
            delegate_actor_id="agent-1",
            permissions=("perm-manage-actors",),
            reason="test",
        )
        pr = _FakePlanetaryRuntime({"agent-1": [_FakeSocietyRuntime(real_registry)]})

        token = create_access_token("agent-1", "agent-1@example.com", "actor", permissions=[])
        check = require_permission("perm-manage-actors")
        user = await check(
            request=_FakeRequest({}, planetary_runtime=pr),
            x_user_id=None,
            authorization=f"Bearer {token}",
        )
        assert user == "agent-1"

        real_registry.revoke(real_registry.list_for_membership("membership-alice")[0].delegation_id)
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({}, planetary_runtime=pr),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 403


class TestRequirePermissionHonorsRevocation:
    """Doot audit P1-5 fix, monkeybrain-API side: require_permission/
    require_self_or_permission (src/monkey_brain/api/dependencies.py) go
    through services.auth.helpers.tokens.decode_access_token directly,
    NOT services/common/auth.py::get_current_user — the human-token
    revocation fix built for that other chokepoint would not have
    covered this one at all without checking is_jti_revoked here too.
    Uses the real jti blocklist (services/auth/helpers/revocation.py),
    same as tests/security/test_access_token_revocation.py."""

    @pytest.mark.asyncio
    async def test_revoked_token_is_denied_by_require_permission(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.revocation import block_jti
        from services.auth.helpers.tokens import (
            create_access_token,
            decode_access_token,
        )
        from src.monkey_brain.api.dependencies import require_permission

        token = create_access_token("alice", "alice@example.com", "actor", permissions=["perm-manage-actors"])
        jti = decode_access_token(token).get("jti")
        assert jti, "create_access_token must embed a jti for revocation to be checkable"

        check = require_permission("perm-manage-actors")
        # Valid before revocation.
        user = await check(request=_FakeRequest({}), x_user_id=None, authorization=f"Bearer {token}")
        assert user == "alice"

        await block_jti(jti)
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({}),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_revoked_token_is_denied_by_require_self_or_permission_even_for_self(
        self, fake_settings, monkeypatch
    ):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        monkeypatch.setenv("AGENTOS_API_KEY", "")
        from fastapi import HTTPException
        from services.auth.helpers.revocation import block_jti
        from services.auth.helpers.tokens import (
            create_access_token,
            decode_access_token,
        )
        from src.monkey_brain.api.dependencies import require_self_or_permission

        token = create_access_token("alice", "alice@example.com", "actor", permissions=[])
        jti = decode_access_token(token).get("jti")

        await block_jti(jti)
        check = require_self_or_permission("perm-manage-actors")
        with pytest.raises(HTTPException) as exc:
            await check(
                request=_FakeRequest({"actor_id": "alice"}),
                x_user_id=None,
                authorization=f"Bearer {token}",
            )
        assert exc.value.status_code == 401


class _FakeBeliefState:
    beliefs = ()


class _FakeActorState:
    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id
        self.belief_state = _FakeBeliefState()


class _FakeMembershipRegistry:
    def __init__(self, member_ids: set[str]) -> None:
        self._member_ids = member_ids

    def actors_for_society(self, society_id: str):
        return set(self._member_ids)


class _FakeBeliefsSocietyRuntime:
    def __init__(self, member_ids: set[str]) -> None:
        self._members = member_ids

    def all_actors(self):
        return [_FakeActorState(a) for a in self._members]


class _FakeSocietyPlanetaryRuntime:
    def __init__(self, member_ids: set[str]) -> None:
        self.membership_registry = _FakeMembershipRegistry(member_ids)
        self._sr = _FakeBeliefsSocietyRuntime(member_ids)

    def get_society_runtime(self, society_id: str):
        return self._sr


class TestSocietyBeliefsPrivacyBoundary:
    """Doot audit BYPASS-03 fix, the multi-actor case: GET /societies/{id}/
    beliefs used to be gated ONLY by require_permission("perm-view-societies"),
    exposing every member's full belief state to any caller holding that
    one permission, no self-check at all. Unlike the single-actor routes
    (tested above), this fans out across multiple actors, so the fix is
    response-level filtering rather than a straight require_self_or_
    permission id-param comparison: an operator (perm-view-societies)
    still sees everyone; a member with no elevated permission sees only
    their own entry; a non-member without the permission is denied."""

    @pytest.mark.asyncio
    async def test_operator_permission_sees_every_member(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from src.monkey_brain.api.routes.societies import get_society_beliefs

        pr = _FakeSocietyPlanetaryRuntime({"alice", "bob"})
        request = _FakeRequest({}, planetary_runtime=pr)
        request.state.jwt_permissions = {"perm-view-societies"}
        result = await get_society_beliefs("soc-1", request, user_id="operator-1")
        assert {a["actor_id"] for a in result.actors} == {"alice", "bob"}

    @pytest.mark.asyncio
    async def test_plain_member_sees_only_self(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from src.monkey_brain.api.routes.societies import get_society_beliefs

        pr = _FakeSocietyPlanetaryRuntime({"alice", "bob"})
        request = _FakeRequest({}, planetary_runtime=pr)
        request.state.jwt_permissions = set()
        result = await get_society_beliefs("soc-1", request, user_id="alice")
        assert {a["actor_id"] for a in result.actors} == {"alice"}

    @pytest.mark.asyncio
    async def test_non_member_without_permission_is_denied(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
        from fastapi import HTTPException
        from src.monkey_brain.api.routes.societies import get_society_beliefs

        pr = _FakeSocietyPlanetaryRuntime({"alice", "bob"})
        request = _FakeRequest({}, planetary_runtime=pr)
        request.state.jwt_permissions = set()
        with pytest.raises(HTTPException) as exc:
            await get_society_beliefs("soc-1", request, user_id="mallory")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_dev_mode_stays_permissive(self, fake_settings, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        from src.monkey_brain.api.routes.societies import get_society_beliefs

        pr = _FakeSocietyPlanetaryRuntime({"alice", "bob"})
        request = _FakeRequest({}, planetary_runtime=pr)
        request.state.jwt_permissions = set()
        result = await get_society_beliefs("soc-1", request, user_id="anyone")
        assert {a["actor_id"] for a in result.actors} == {"alice", "bob"}
