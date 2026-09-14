"""Seed data for the `roles` collection.

Single bootstrap admin role carrying every permission in INIT_PERMISSIONS —
matches scripts/seed_user_role_permissions.py's own stated intent ("1 admin
user -> 1 admin role -> all permissions").
"""

from __future__ import annotations

from src.mock.organizations.permissions import _PERMISSION_IDS

INIT_ROLES: list[dict] = [
    {
        "role_id": "ROLE-001",
        "name": "admin",
        "permissions": list(_PERMISSION_IDS),
        "parent_role_ids": [],
        "is_active": True,
    },
]
