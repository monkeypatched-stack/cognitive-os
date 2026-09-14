"""Negative authorization — an under-scoped principal is denied THROUGHOUT the stack,
not just at the API edge (authz plan P4).

For one under-scoped principal (wrong tenant, no execute scope, no matching role) we
assert denial at each layer independently: execution, knowledge, world model, and data.
Plus execution-layer default-deny, mirroring the edge's fail-closed behaviour so a
regression that opens a lower layer surfaces in CI.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from src.monkey_brain.kernel.plan.goals.goal import GoalType
from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor


def _goal(name="create_widget", gt=GoalType.CREATE):
    return SimpleNamespace(name=name, goal_type=gt)


def _ctx(user="under-scoped-user"):
    return SimpleNamespace(user_id=user, run_id="r1")


def _authorize(goal, ctx):
    return asyncio.run(GoalExecutor._authorize(goal, ctx))


# ── execution layer: default-deny / fail-closed ─────────────────────────────────


def test_execution_default_deny_when_enforced_without_opa(monkeypatch):
    """Enforcement on + no OPA configured → DENIED (default_allow=False). This is the
    execution-layer mirror of the edge's fail-closed default."""
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    monkeypatch.delenv("OPA_URL", raising=False)
    result = _authorize(_goal(), _ctx())
    assert result is not None  # denied, not silently allowed


def test_execution_requires_explicit_allow(monkeypatch):
    """Only an explicit policy allow lets execution through — anything else denies."""
    fake = types.ModuleType("services.common.opa")

    async def _deny(policy_path, input_data, *, default_allow=True):
        return False  # policy does not allow this principal

    fake.evaluate = _deny
    for name in ("services", "services.common", "services.common.opa"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "services.common.opa", fake)
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    assert _authorize(_goal(), _ctx()) == "authorization denied by policy"


def test_execution_inert_by_default(monkeypatch):
    """With enforcement off (default), the execution hook permits — the edge is the gate."""
    monkeypatch.delenv("AGENTOS_OPA_ENFORCE", raising=False)
    assert _authorize(_goal(), _ctx()) is None


# ── knowledge layer: under-scoped principal denied restricted packs ─────────────


def test_knowledge_under_scoped_denied():
    from src.monkey_brain.runtime.agent_middleware import _pack_permitted

    restricted = {"name": "acme-admin-kb", "tenant": "acme", "allowed_roles": ["admin"]}
    under_scoped = {"tenant": "globex", "roles": ["viewer"]}  # wrong tenant + role
    assert _pack_permitted(restricted, under_scoped) is False
    assert _pack_permitted(restricted, None) is False  # no identity → denied
    # and a public pack is still allowed — denial is scoped, not blanket
    assert _pack_permitted({"name": "public"}, under_scoped) is True


# ── world-model layer: cross-tenant view is empty ───────────────────────────────


def test_world_layer_cross_tenant_empty():
    from src.monkey_brain.kernel.compile import Context, TenantWorld

    w = TenantWorld()
    w.observe("acme", "Order", "Ship", domain="logistics")
    under_scoped = Context(tenant_id="globex")
    assert list(w.view(under_scoped)) == []  # sees nothing of acme's
    assert w.view(under_scoped).successors("Order") == []


# ── data layer: cross-tenant query returns empty ────────────────────────────────


def test_data_layer_cross_tenant_empty():
    from services.common.tenant_scope import tenant_scope, wrap

    class _Cursor:
        def __init__(self, d):
            self._d = d

        async def to_list(self, n=None):
            return list(self._d)

    class _Coll:
        def __init__(self):
            self.name = "c"
            self._docs = []

        def _m(self, doc, f):
            return all(doc.get(k) == v for k, v in (f or {}).items())

        def find(self, f=None, *a, **k):
            return _Cursor([d for d in self._docs if self._m(d, f)])

        async def find_one(self, f=None, *a, **k):
            return next((d for d in self._docs if self._m(d, f)), None)

        async def insert_one(self, d, *a, **k):
            self._docs.append(dict(d))

    async def go():
        c = _Coll()
        with tenant_scope("acme"):
            await wrap(c).insert_one({"name": "order-1"})
        with tenant_scope("globex"):  # under-scoped reader
            assert await wrap(c).find({}).to_list() == []
            assert await wrap(c).find_one({"name": "order-1"}) is None

    asyncio.run(go())
