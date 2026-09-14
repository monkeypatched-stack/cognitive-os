"""Tenant-scope isolation — fail-closed regression tests for the three review findings.

T-A  no tenant in context must NOT return the raw collection (fail closed)
T-B  a caller cannot write into another tenant by supplying tenant_id
T-C  unscoped data methods (bulk_write, raw batches, …) are refused
"""

from __future__ import annotations

import asyncio

import pytest

from services.common import tenant_scope as ts
from services.common.tenant_scope import (
    wrap,
    tenant_scope,
    system_scope,
    set_tenant,
    TenantScopedCollection,
    TENANT_FIELD,
)


class FakeCollection:
    """Records the filter/doc actually sent to the driver."""

    name = "things"

    def __init__(self):
        self.calls = []

    def find(self, filter=None, *a, **k):
        self.calls.append(("find", filter))
        return iter([])

    async def insert_one(self, document, *a, **k):
        self.calls.append(("insert_one", document))
        return None

    async def count_documents(self, filter=None, *a, **k):
        self.calls.append(("count", filter))
        return 0

    def bulk_write(self, *a, **k):  # should never be reached through the wrapper
        self.calls.append(("bulk_write", None))
        return None


def _reset():
    set_tenant("")
    # clear any leaked unscoped flag
    try:
        ts._unscoped.set(False)
    except Exception:
        pass


# ── T-A: fail closed when no tenant ──────────────────────────────────────────────


def test_no_tenant_fails_closed_when_enforced(monkeypatch):
    monkeypatch.setenv("AGENTOS_TENANCY_ENFORCE", "1")
    _reset()
    col = FakeCollection()
    scoped = wrap(col)
    assert isinstance(scoped, TenantScopedCollection)  # NOT the raw collection
    list(scoped.find({"k": 1}))
    kind, sent = col.calls[-1]
    assert sent[TENANT_FIELD] == ts._NO_TENANT_SENTINEL  # scoped to a no-match sentinel


def test_no_tenant_passes_through_when_not_enforced(monkeypatch):
    # Current (unwired) state: passthrough — but the code logs loudly that isolation is OFF.
    # Documented explicitly so the insecure default can never be mistaken for isolation.
    monkeypatch.delenv("AGENTOS_TENANCY_ENFORCE", raising=False)
    _reset()
    col = FakeCollection()
    assert wrap(col) is col


def test_system_scope_allows_unscoped_access():
    _reset()
    col = FakeCollection()
    with system_scope():
        assert wrap(col) is col  # explicit opt-in → raw


def test_tenant_in_context_scopes_reads():
    _reset()
    col = FakeCollection()
    with tenant_scope("acme"):
        list(wrap(col).find({"k": 1}))
    assert col.calls[-1][1][TENANT_FIELD] == "acme"


# ── T-B: caller cannot spoof the tenant on writes ────────────────────────────────


def test_insert_forces_context_tenant_over_caller_value():
    _reset()
    col = FakeCollection()

    async def scenario():
        with tenant_scope("acme"):
            await wrap(col).insert_one({"x": 1, TENANT_FIELD: "victim"})

    asyncio.run(scenario())
    _, doc = col.calls[-1]
    assert doc[TENANT_FIELD] == "acme"  # spoofed 'victim' overridden


# ── T-C: unscoped methods are refused ────────────────────────────────────────────


def test_bulk_write_is_refused():
    _reset()
    with pytest.raises(AttributeError):
        asyncio.run(wrap_with_tenant().bulk_write([]))  # refused on call


def test_raw_batches_is_refused():
    _reset()
    with pytest.raises(AttributeError):
        _ = wrap_with_tenant().find_raw_batches  # refused on attribute access


def wrap_with_tenant():
    set_tenant("acme")
    return TenantScopedCollection(FakeCollection(), "acme")
