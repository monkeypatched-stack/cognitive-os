"""Data-layer tenant isolation — tenant as a mandatory query predicate (authz plan P3).

The wrapper injects the request-scoped tenant into every query at one chokepoint, so all
.find() sites are scoped without being edited. Cross-tenant reads return empty; inserts
are stamped; no-tenant context is pass-through (system tasks).
"""

from __future__ import annotations

import asyncio

from services.common.tenant_scope import TENANT_FIELD, tenant_scope, wrap


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)[: (length or len(self._docs))]


class _FakeCollection:
    """Minimal async Mongo collection for isolation tests."""

    def __init__(self, name="c"):
        self.name = name
        self._docs: list[dict] = []

    @staticmethod
    def _match(doc, filt):
        return all(doc.get(k) == v for k, v in (filt or {}).items())

    def find(self, filter=None, *a, **k):
        return _Cursor([d for d in self._docs if self._match(d, filter)])

    async def find_one(self, filter=None, *a, **k):
        return next((d for d in self._docs if self._match(d, filter)), None)

    async def count_documents(self, filter=None, *a, **k):
        return sum(1 for d in self._docs if self._match(d, filter))

    async def insert_one(self, doc, *a, **k):
        self._docs.append(dict(doc))

    async def delete_many(self, filter, *a, **k):
        keep = [d for d in self._docs if not self._match(d, filter)]
        removed = len(self._docs) - len(keep)
        self._docs = keep
        return type("R", (), {"deleted_count": removed})()


def test_no_tenant_fails_closed_when_enforced(monkeypatch):
    # SECURITY: with enforcement on, a missing tenant must NOT hand back the raw collection
    # (that leaks all tenants). Explicit system tasks opt in via system_scope().
    from services.common.tenant_scope import system_scope, TenantScopedCollection

    monkeypatch.setenv("AGENTOS_TENANCY_ENFORCE", "1")
    c = _FakeCollection()
    assert isinstance(wrap(c), TenantScopedCollection)  # scoped (to a no-match sentinel)
    with system_scope():
        assert wrap(c) is c  # explicit system task → unchanged


def test_insert_stamps_tenant():
    async def go():
        c = _FakeCollection()
        with tenant_scope("acme"):
            await wrap(c).insert_one({"name": "widget"})
        assert c._docs[0][TENANT_FIELD] == "acme"

    asyncio.run(go())


def test_cross_tenant_reads_return_empty():
    async def go():
        c = _FakeCollection()
        with tenant_scope("acme"):
            await wrap(c).insert_one({"name": "widget"})
        with tenant_scope("globex"):
            await wrap(c).insert_one({"name": "gadget"})

        with tenant_scope("acme"):
            wa = wrap(c)
            docs = await wa.find({}).to_list()
            assert [d["name"] for d in docs] == ["widget"]  # only own docs
            assert await wa.count_documents({}) == 1
            assert await wa.find_one({"name": "gadget"}) is None  # cannot see globex's

        with tenant_scope("globex"):
            wg = wrap(c)
            assert [d["name"] for d in await wg.find({}).to_list()] == ["gadget"]
            assert await wg.find_one({"name": "widget"}) is None  # cross-tenant read → empty

    asyncio.run(go())


def test_caller_cannot_override_tenant_filter():
    async def go():
        c = _FakeCollection()
        with tenant_scope("acme"):
            await wrap(c).insert_one({"name": "secret"})
        with tenant_scope("globex"):
            # even explicitly asking for acme's tenant is overridden by the context tenant
            assert await wrap(c).find_one({TENANT_FIELD: "acme"}) is None

    asyncio.run(go())


def test_delete_is_tenant_scoped():
    async def go():
        c = _FakeCollection()
        with tenant_scope("acme"):
            await wrap(c).insert_one({"name": "x"})
        with tenant_scope("globex"):
            await wrap(c).insert_one({"name": "x"})
            r = await wrap(c).delete_many({"name": "x"})
            assert r.deleted_count == 1  # only globex's deleted
        with tenant_scope("acme"):
            assert await wrap(c).count_documents({}) == 1  # acme's survives

    asyncio.run(go())
