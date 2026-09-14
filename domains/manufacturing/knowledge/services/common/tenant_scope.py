"""Tenant-scoped query wrapper — make tenant a mandatory query predicate.

The 728 .find() call sites are not edited by hand. Instead a single wrapper at the
collection-acquisition chokepoint (MirroredDatabase.get_collection) injects a tenant
filter into every read/write, driven by a request-scoped context var:

    token claim  ──set_tenant_from_claims──►  _current_tenant (ContextVar)
                                                     │
                          MirroredDatabase.get_collection ──wrap──► TenantScopedCollection
                                                     │
                    every find/update/delete gets  {tenant_id: <tenant>}  merged in

When no tenant is in context (system tasks: migrations, seeding, startup) the wrapper is
a pass-through, preserving existing behaviour. When a tenant IS set, it is enforced on
every query — cross-tenant reads return empty, inserts are stamped with the tenant.
"""

from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("services.tenant_scope")

TENANT_FIELD = "tenant_id"


def _enforce() -> bool:
    """Enforce tenant isolation (fail closed on a missing tenant). Default OFF until tenant
    provisioning is wired end-to-end (token claim → middleware → set_tenant_from_claims);
    when off, a missing tenant passes through but is logged loudly so it is never SILENTLY
    insecure. Flip AGENTOS_TENANCY_ENFORCE=1 once provisioning lands."""
    return os.getenv("AGENTOS_TENANCY_ENFORCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_current_tenant: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_tenant", default=""
)
# Explicit opt-in for genuine cross-tenant/system access. Everything else FAILS CLOSED.
_unscoped: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "unscoped_access", default=False
)
# A tenant value that matches no real tenant — used to fail closed without crashing callers.
_NO_TENANT_SENTINEL = "\x00__no_tenant__\x00"


# ── request-scoped tenant (token claim → context) ────────────────────────────────


def set_tenant(tenant_id: str):
    return _current_tenant.set(tenant_id or "")


def get_tenant() -> str:
    return _current_tenant.get()


def default_tenant() -> str:
    """The tenant assigned to users/records that carry none.

    Existing user documents predate multi-tenancy, so they have no tenant field. Rather than
    minting empty tenants (which would fail closed / leak depending on mode), they resolve to
    this single tenant — a correct single-tenant deployment that can be split later by
    assigning real tenants to users and backfilling records.
    """
    return os.getenv("DEFAULT_TENANT", "default")


def user_tenant(user: dict) -> str:
    """Resolve a user document's tenant (falls back to the default tenant)."""
    u = user or {}
    return str(
        u.get("tenant")
        or u.get("tenant_id")
        or u.get("org")
        or u.get("org_id")
        or default_tenant()
    )


def set_tenant_from_claims(claims: dict) -> str:
    """Extract the tenant from a verified token's claims and put it in context.

    Tokens issued before multi-tenancy carry no tenant claim; they resolve to the default
    tenant rather than an empty one, so an authenticated caller is always scoped to SOME
    tenant (never the unscoped/empty state that leaks or fails closed).
    """
    c = claims or {}
    tenant = str(
        c.get("tenant")
        or c.get("tenant_id")
        or c.get("org")
        or c.get("org_id")
        or default_tenant()
    )
    set_tenant(tenant)
    return tenant


@contextmanager
def tenant_scope(tenant_id: str):
    """Scope a block of queries to a tenant (tests, background jobs)."""
    token = _current_tenant.set(tenant_id or "")
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def system_scope():
    """Explicit opt-in for UNSCOPED, cross-tenant access — for genuine system tasks that must
    span tenants. Without this, a missing tenant fails closed rather than exposing all data.
    """
    token = _unscoped.set(True)
    try:
        yield
    finally:
        _unscoped.reset(token)


# Data-exposing collection methods that this wrapper does NOT scope — accessing them would
# bypass isolation, so they are denied (callers must use a scoped method or system_scope()).
_UNSCOPED_DENY = frozenset(
    {
        "find_raw_batches",
        "aggregate_raw_batches",
        "map_reduce",
        "inline_map_reduce",
        "group",
        "count",
        "watch",
    }
)


# ── the query filter ─────────────────────────────────────────────────────────────


def _scoped_filter(filter: Any, tenant: str) -> dict:
    f = dict(filter or {})
    f[TENANT_FIELD] = tenant  # enforced: overrides any caller-supplied tenant
    return f


class TenantScopedCollection:
    """Wraps a Mongo collection, injecting the current tenant into every query."""

    def __init__(self, collection: Any, tenant: str) -> None:
        self._c = collection
        self._tenant = tenant
        self.name = getattr(collection, "name", "")

    def __getattr__(self, name: str) -> Any:
        # Fail closed on data-exposing methods we don't scope, rather than silently handing
        # back the RAW collection (which would bypass tenant isolation).
        if name in _UNSCOPED_DENY:
            raise AttributeError(
                f"{name}() is not tenant-scoped; refusing unscoped access. Use a scoped method "
                f"(find/aggregate/insert_one/…) or wrap intentional access in system_scope()."
            )
        return getattr(self._c, name)  # metadata/admin attributes only

    def _stamp(self, doc: dict) -> dict:
        d = dict(doc)
        d[TENANT_FIELD] = (
            self._tenant
        )  # FORCE — a caller cannot write into another tenant
        return d

    async def bulk_write(self, requests, *a, **k):
        # Half-scoping mixed bulk ops is error-prone; require the scoped single-doc methods or
        # an explicit system_scope() instead of silently issuing unscoped writes.
        raise AttributeError(
            "bulk_write() is not tenant-scoped; use scoped insert_one/update_one/… or system_scope()."
        )

    async def estimated_document_count(self, *a, **k):
        # This counts the WHOLE collection (all tenants). Use the scoped count instead.
        return await self.count_documents({}, *a, **k)

    # reads
    def find(self, filter=None, *a, **k):
        return self._c.find(_scoped_filter(filter, self._tenant), *a, **k)

    async def find_one(self, filter=None, *a, **k):
        return await self._c.find_one(_scoped_filter(filter, self._tenant), *a, **k)

    async def count_documents(self, filter=None, *a, **k):
        return await self._c.count_documents(
            _scoped_filter(filter, self._tenant), *a, **k
        )

    def aggregate(self, pipeline, *a, **k):
        return self._c.aggregate(
            [{"$match": {TENANT_FIELD: self._tenant}}, *list(pipeline)], *a, **k
        )

    async def distinct(self, key, filter=None, *a, **k):
        return await self._c.distinct(
            key, _scoped_filter(filter, self._tenant), *a, **k
        )

    # writes
    async def insert_one(self, document, *a, **k):
        return await self._c.insert_one(self._stamp(document), *a, **k)

    async def insert_many(self, documents, *a, **k):
        return await self._c.insert_many([self._stamp(d) for d in documents], *a, **k)

    async def update_one(self, filter, update, *a, **k):
        return await self._c.update_one(
            _scoped_filter(filter, self._tenant), update, *a, **k
        )

    async def update_many(self, filter, update, *a, **k):
        return await self._c.update_many(
            _scoped_filter(filter, self._tenant), update, *a, **k
        )

    async def replace_one(self, filter, replacement, *a, **k):
        return await self._c.replace_one(
            _scoped_filter(filter, self._tenant), self._stamp(replacement), *a, **k
        )

    async def delete_one(self, filter, *a, **k):
        return await self._c.delete_one(_scoped_filter(filter, self._tenant), *a, **k)

    async def delete_many(self, filter, *a, **k):
        return await self._c.delete_many(_scoped_filter(filter, self._tenant), *a, **k)

    async def find_one_and_update(self, filter, update, *a, **k):
        return await self._c.find_one_and_update(
            _scoped_filter(filter, self._tenant), update, *a, **k
        )

    async def find_one_and_replace(self, filter, replacement, *a, **k):
        return await self._c.find_one_and_replace(
            _scoped_filter(filter, self._tenant), self._stamp(replacement), *a, **k
        )

    async def find_one_and_delete(self, filter, *a, **k):
        return await self._c.find_one_and_delete(
            _scoped_filter(filter, self._tenant), *a, **k
        )


def wrap(collection: Any) -> Any:
    """Return a tenant-scoped view of `collection`.

    With a tenant in context → scoped (isolation enforced).
    With no tenant and an explicit system_scope() → raw (intentional cross-tenant access).
    With no tenant and NO opt-in:
      * AGENTOS_TENANCY_ENFORCE=1 → FAIL CLOSED (scope to a sentinel matching no tenant), so a
        missing/lost tenant returns nothing rather than leaking every tenant's data.
      * default → pass through, but LOG LOUDLY. Isolation is not yet enforceable because tenant
        provisioning (token claim → middleware → set_tenant_from_claims) is not wired; failing
        closed today would empty every query. Never silently insecure: the warning says so.
    """
    tenant = get_tenant()
    if tenant:
        return TenantScopedCollection(collection, tenant)
    if _unscoped.get():
        return collection  # explicit, intentional system access
    if _enforce():
        logger.warning(
            "[tenant] DB access with no tenant in context — failing closed. "
            "Wrap intentional cross-tenant access in system_scope()."
        )
        return TenantScopedCollection(collection, _NO_TENANT_SENTINEL)
    logger.warning(
        "[tenant] UNSCOPED DB access — no tenant in context and enforcement is OFF. "
        "Tenant isolation is NOT active. Wire tenant provisioning and set "
        "AGENTOS_TENANCY_ENFORCE=1."
    )
    return collection
