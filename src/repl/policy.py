"""Policy management."""

from __future__ import annotations

import logging

import typer

from repl._helpers import _auth_url

logger = logging.getLogger("monkeypatched")


def policy_grant(
    subject: str = typer.Argument(..., help="Role ID or role name to grant to"),
    resource: str = typer.Argument(..., help="Resource name (e.g. 'workorders', 'inventory')"),
    action: str = typer.Argument(..., help="Action (read, write, delete, admin)"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Grant a permission on a resource to a role.  [like chmod]

    Example:
        monkeypatched acl grant operator workorders write
    """
    import httpx

    base = auth_url or _auth_url()

    # 1. Ensure the permission exists (create if not)
    perm_body = {"name": f"{resource}:{action}", "resource": resource, "action": action}
    try:
        pr = httpx.post(f"{base}/api/v1/permissions/", json=perm_body, timeout=15)
        perm_id = pr.json().get("id") or pr.json().get("_id")
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"  Permission upsert failed: {e}", err=True)
        raise typer.Exit(1)

    if not perm_id:
        # Try to find existing
        try:
            lr = httpx.get(f"{base}/api/v1/permissions/by-resource/{resource}", timeout=15)
            items = lr.json() if isinstance(lr.json(), list) else []
            match = next((p for p in items if p.get("action") == action), None)
            perm_id = match.get("id") or match.get("_id") if match else None
        except Exception as e:
            logger.debug("Exception caught: %s", e)

    if not perm_id:
        typer.echo("  Could not resolve permission ID", err=True)
        raise typer.Exit(1)

    # 2. Assign permission to role
    try:
        ar = httpx.post(f"{base}/api/v1/roles/{subject}/permissions/{perm_id}", timeout=15)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}.", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(ar.text)
    elif ar.status_code in (200, 201):
        typer.echo(f"  Granted {action} on {resource} → role '{subject}'")
    else:
        typer.echo(f"  Error {ar.status_code}: {ar.text}", err=True)
        raise typer.Exit(1)


def policy_revoke(
    subject: str = typer.Argument(..., help="Role ID"),
    permission_id: str = typer.Argument(..., help="Permission ID to revoke"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL"),
):
    """Revoke a permission from a role.  [like chmod -x]"""
    import httpx

    base = auth_url or _auth_url()
    try:
        r = httpx.delete(f"{base}/api/v1/roles/{subject}/permissions/{permission_id}", timeout=15)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}.", err=True)
        raise typer.Exit(1)

    if r.status_code in (200, 204):
        typer.echo(f"  Revoked permission {permission_id} from role '{subject}'")
    else:
        typer.echo(f"  Error {r.status_code}: {r.text}", err=True)
        raise typer.Exit(1)


def policy_list(
    resource: str = typer.Option("", "--resource", "-r", help="Filter by resource"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List all permissions.  [like ls -l (showing mode bits)]"""
    import httpx

    base = auth_url or _auth_url()
    path = f"/api/v1/permissions/by-resource/{resource}" if resource else "/api/v1/permissions/"
    try:
        r = httpx.get(f"{base}{path}", timeout=15)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}.", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(r.text)
        return

    try:
        items = r.json()
        if not isinstance(items, list):
            items = [items]
        typer.echo(f"  {'ID':<30s} {'RESOURCE':<20s} {'ACTION'}")
        typer.echo(f"  {'-' * 30} {'-' * 20} {'-' * 12}")
        for p in items:
            typer.echo(
                f"  {str(p.get('id', p.get('_id', '?'))):<30s} {p.get('resource', '?'):<20s} {p.get('action', '?')}"
            )
    except Exception:
        typer.echo(r.text)
