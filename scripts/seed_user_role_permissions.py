#!/usr/bin/env python3
"""Seed 1 admin user → 1 admin role → all permissions.

Idempotent (upsert, not insert) — safe to re-run against an already-seeded
database; existing docs are refreshed in place rather than raising on a
duplicate _id.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))
# services.common.* physically lives under domains/manufacturing/knowledge —
# same sys.path patch src/monkey_brain/api/main.py applies at import time for
# the real app; this script needs it too since it runs standalone.
_domains_services = _repo_root / "domains" / "manufacturing" / "knowledge"
if _domains_services.exists():
    sys.path.insert(0, str(_domains_services))

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings
from services.common.neo4j_mirror import (
    mirror_document,
    safe_mirror,
    close_neo4j_mirror_driver,
)

from src.mock.organizations.permissions import INIT_PERMISSIONS
from src.mock.organizations.roles import INIT_ROLES

NOW = datetime.now(timezone.utc)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    # ── 1. Permissions ─────────────────────────────────────────────────────
    perm_docs = []
    for p in INIT_PERMISSIONS:
        doc = dict(p)
        doc["_id"] = doc["permission_id"]
        perm_docs.append(doc)

    for doc in perm_docs:
        await db["permissions"].update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
        await safe_mirror(mirror_document("permissions", doc, "seed"))
    print(f"  ✓ Permissions: {len(perm_docs)} upserted")

    # ── 2. Admin role (ROLE-001) with all permissions ─────────────────────
    admin_role = dict(INIT_ROLES[0])
    admin_role["_id"] = admin_role["role_id"]
    await db["roles"].update_one({"_id": admin_role["_id"]}, {"$set": admin_role}, upsert=True)
    await safe_mirror(mirror_document("roles", admin_role, "seed"))
    print(f"  ✓ Role: {admin_role['role_id']} ({admin_role['name']}) — {len(admin_role['permissions'])} permissions")

    # ── 3. Admin user ──────────────────────────────────────────────────────
    user = {
        "_id": "USER-001",
        "user_id": "USER-001",
        "employee_id": "EMP-001",
        "name": "admin",
        "department": "DEPT-001",
        "team": "TEAM-001",
        "email": "admin@example.com",
        "phone": "+91 02512890297",
        "is_active": True,
        "role": "admin",
        "role_id": "ROLE-001",
        "password": _hash("Admin@12345678"),
        "plant_id": "PLANT-TBL-IN-001",
        # Confirmed live: services/common/auth.py::get_current_user calls
        # set_tenant_from_claims(payload) for every authenticated request,
        # which puts the access token's own "tenant" claim (create_access_
        # token's tenant=user_tenant(user) — "default" for a user with no
        # tenant field) into context. services/common/tenant_scope.py's
        # wrap() then tenant-scopes every db["users"] query for the REST
        # of that request (get_by_id inside /mfa/enroll, /mfa/enable,
        # /me, ...) to {"tenant_id": "default", ...} — a user document
        # with no tenant_id field at all is invisible to that filter, so
        # every one of those routes 401s "User not found" despite a
        # perfectly valid token. /login itself is unauthenticated (no
        # tenant in context yet), so it alone was unaffected — this only
        # surfaced on the very next authenticated call. Must match
        # default_tenant()'s own value ("default" unless DEFAULT_TENANT
        # is overridden) exactly.
        "tenant_id": "default",
        "created_at": NOW,
        "updated_at": NOW,
    }
    await db["users"].update_one({"_id": user["_id"]}, {"$set": user}, upsert=True)
    await safe_mirror(mirror_document("users", user, "seed"))
    print(f"  ✓ User: {user['user_id']} ({user['name']}) — role: {user['role']}")

    # ── Verify ─────────────────────────────────────────────────────────────
    for coll in ["permissions", "roles", "users"]:
        count = await db[coll].count_documents({})
        print(f"  {coll}: {count} docs")

    await close_neo4j_mirror_driver()
    client.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(seed())
