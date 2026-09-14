"""All routers share ONE authentication dependency — which provisions the tenant.

The per-router get_current_user copies validated tokens but never set the tenant, so those
routes ran untenanted. They are now consolidated onto services.common.auth.get_current_user.
"""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "services.auth.helpers.me",
    "services.pm.routers.sop",
    "services.process_definitions.routers.process_pre_checks",
    "services.process_definitions.routers.process_constraints",
    "services.process_definitions.routers.process_corrections",
    "services.process_definitions.routers.process_steps",
    "services.process_definitions.routers.process_post_checks",
    "services.process_definitions.routers.process_definition",
]


def test_no_duplicate_get_current_user_definitions():
    """Every router must use the shared dependency, not its own copy."""
    import subprocess

    out = subprocess.run(
        ["grep", "-rn", "def get_current_user", "domains/"],
        capture_output=True,
        text=True,
    ).stdout
    defs = [l for l in out.splitlines() if "__pycache__" not in l]
    assert len(defs) == 1, f"expected a single definition, found:\n" + "\n".join(defs)
    assert "common/auth.py" in defs[0]


@pytest.mark.parametrize("mod", MODULES)
def test_router_uses_the_shared_dependency(mod):
    from services.common.auth import get_current_user as shared

    m = importlib.import_module(mod)
    assert getattr(m, "get_current_user") is shared  # same object = same chokepoint


@pytest.mark.asyncio
async def test_shared_dependency_sets_tenant(fake_settings, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    from fastapi.security import HTTPAuthorizationCredentials
    from services.auth.helpers.tokens import create_access_token
    from services.common.auth import get_current_user
    from services.common.tenant_scope import get_tenant, set_tenant

    set_tenant("")
    token = create_access_token("u1", "u@x.com", "user", [], tenant="acme")
    user = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert get_tenant() == "acme"  # every consolidated route is tenanted
    assert user["sub"] == "u1" and user["user_id"] == "u1"  # superset keeps old consumers OK
