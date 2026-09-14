"""Seed data for the `permissions` collection.

The permission catalog here is not invented — it's every `perm-*` string
actually enforced by `require_permission(...)` across src/monkey_brain/api/routes/*.py,
confirmed via a repo-wide grep so this stays a real, current mirror of what
the API checks rather than a guessed/aspirational list.
"""

from __future__ import annotations

_PERMISSION_IDS = [
    "perm-admin",
    "perm-execute-action",
    "perm-execute-actors",
    "perm-execute-codegen",
    "perm-execute-compare",
    "perm-execute-learn",
    "perm-execute-plan",
    "perm-execute-planet",
    "perm-execute-prompt",
    "perm-execute-query",
    "perm-execute-simulate",
    "perm-execute-societies",
    "perm-manage-actors",
    "perm-manage-agents",
    "perm-manage-cognitive",
    "perm-manage-keys",
    "perm-manage-memberships",
    "perm-manage-societies",
    "perm-manage-somatic",
    "perm-manage-world",
    "perm-query",
    "perm-reset-workload",
    "perm-view-actors",
    "perm-view-agents",
    "perm-view-codegen",
    "perm-view-cognitive",
    "perm-view-compare",
    "perm-view-discovery",
    "perm-view-keys",
    "perm-view-learn",
    "perm-view-memberships",
    "perm-view-observability",
    "perm-view-planet",
    "perm-view-policy",
    "perm-view-runtime",
    "perm-view-simulate",
    "perm-view-societies",
    "perm-view-somatic",
    "perm-view-stability",
    "perm-view-state",
    "perm-view-transitions",
    "perm-view-world",
]


def _describe(permission_id: str) -> tuple[str, str, str]:
    """Split "perm-<action>-<resource>" into (name, resource, action)."""
    body = permission_id.removeprefix("perm-")
    action, _, resource = body.partition("-")
    resource = resource or action
    return body, resource, action


INIT_PERMISSIONS: list[dict] = []
for _pid in _PERMISSION_IDS:
    _name, _resource, _action = _describe(_pid)
    INIT_PERMISSIONS.append(
        {
            "permission_id": _pid,
            "name": _name,
            "resource": _resource,
            "action": _action,
            "description": f"Grants {_pid}",
            "is_active": True,
        }
    )
