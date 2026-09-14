"""Knowledge-pack retrieval scoping by caller identity (authz plan, Phase 3).

Retrieval is no longer 'everything for a bare question string' — packs declare access
metadata and are filtered by the caller's tenant / roles. Public packs (no metadata)
stay universally visible for backward compatibility.
"""

from __future__ import annotations

from src.monkey_brain.runtime.agent_middleware import (
    _identity_from_state,
    _pack_permitted,
)

# ── identity extraction from execution state ─────────────────────────────────────


def test_identity_from_state_extracts_fields():
    ident = _identity_from_state({"user_id": "u1", "tenant": "acme", "roles": ["admin"]})
    assert ident == {"user_id": "u1", "tenant": "acme", "roles": ["admin"]}


def test_identity_from_state_alt_keys():
    ident = _identity_from_state({"__tenant__": "globex", "role_ids": ["r1"]})
    assert ident["tenant"] == "globex" and ident["roles"] == ["r1"]


def test_identity_from_state_none_when_empty():
    assert _identity_from_state(None) is None
    assert _identity_from_state({}) is None
    assert _identity_from_state({"unrelated": "x"}) is None


# ── pack access decisions ────────────────────────────────────────────────────────


def test_public_pack_visible_to_everyone():
    pub = {"name": "cli-reference"}  # no access metadata → public
    assert _pack_permitted(pub, None) is True  # even with no identity
    assert _pack_permitted(pub, {"tenant": "acme"}) is True


def test_restricted_pack_withheld_without_identity():
    restricted = {"name": "secret", "visibility": "restricted"}
    assert _pack_permitted(restricted, None) is False  # bare question gets nothing


def test_tenant_scoped_pack():
    pack = {"name": "acme-only", "tenant": "acme"}
    assert _pack_permitted(pack, {"tenant": "acme"}) is True
    assert _pack_permitted(pack, {"tenant": "globex"}) is False
    assert _pack_permitted(pack, None) is False


def test_role_scoped_pack():
    pack = {"name": "admins", "allowed_roles": ["admin", "auditor"]}
    assert _pack_permitted(pack, {"roles": ["admin"]}) is True
    assert _pack_permitted(pack, {"roles": ["viewer"]}) is False
    assert _pack_permitted(pack, {"roles": []}) is False


def test_tenant_and_role_both_required():
    pack = {"name": "acme-admins", "tenant": "acme", "allowed_roles": ["admin"]}
    assert _pack_permitted(pack, {"tenant": "acme", "roles": ["admin"]}) is True
    assert _pack_permitted(pack, {"tenant": "acme", "roles": ["viewer"]}) is False  # wrong role
    assert _pack_permitted(pack, {"tenant": "globex", "roles": ["admin"]}) is False  # wrong tenant


def test_callers_thread_identity():
    # the middleware call sites pass identity=_identity_from_state(state)
    import inspect
    from src.monkey_brain.runtime import agent_middleware

    src = inspect.getsource(agent_middleware)
    assert src.count("load_knowledge_context(question, identity=_identity_from_state(state))") == 3
