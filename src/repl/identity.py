"""Identity management."""

from __future__ import annotations


import typer

from repl._helpers import _auth_url


def identity_create(
    username: str = typer.Argument(..., help="Username"),
    email: str = typer.Option(..., "--email", "-e", prompt=True, help="Email address"),
    role: str = typer.Option("viewer", "--role", "-r", help="Initial role name"),
    department: str = typer.Option("", "--department", "-d", help="Department"),
    password: str = typer.Option("", "--password", "-p", help="Password (prompted if omitted)"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL (or AUTH_SERVICE_URL)"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Create a new user identity.  [like useradd]"""
    import httpx, getpass

    if not password:
        password = getpass.getpass("  Password: ")

    body = {
        "username": username,
        "email": email,
        "password": password,
        "role": role,
    }
    if department:
        body["department"] = department

    base = auth_url or _auth_url()
    try:
        r = httpx.post(f"{base}/api/v1/users/", json=body, timeout=15)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}. Is it running?", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(r.text)
    else:
        try:
            d = r.json()
            if r.status_code in (200, 201):
                typer.echo(f"  Created user '{username}' (id: {d.get('id', d.get('_id', '?'))})")
            else:
                typer.echo(f"  Error {r.status_code}: {d}", err=True)
                raise typer.Exit(1)
        except Exception:
            typer.echo(r.text)


def identity_list(
    department: str = typer.Option("", "--department", "-d", help="Filter by department"),
    team: str = typer.Option("", "--team", "-t", help="Filter by team"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List users.  [like getent passwd]"""
    import httpx

    base = auth_url or _auth_url()
    if department:
        path = f"/api/v1/users/by-department/{department}"
    elif team:
        path = f"/api/v1/users/by-team/{team}"
    else:
        path = "/api/v1/users/"

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
            items = items.get("items", items.get("users", [items]))
        typer.echo(f"  {'USERNAME':<20s} {'EMAIL':<30s} {'DEPARTMENT'}")
        typer.echo(f"  {'-' * 20} {'-' * 30} {'-' * 20}")
        for u in items:
            typer.echo(f"  {u.get('username', '?'):<20s} {u.get('email', '?'):<30s} {u.get('department', '')}")
    except Exception:
        typer.echo(r.text)


def identity_delete(
    user_id: str = typer.Argument(..., help="User ID to delete"),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL"),
):
    """Delete a user identity.  [like userdel]"""
    import httpx

    base = auth_url or _auth_url()
    try:
        r = httpx.delete(f"{base}/api/v1/users/{user_id}", timeout=15)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to auth service at {base}.", err=True)
        raise typer.Exit(1)

    if r.status_code in (200, 204):
        typer.echo(f"  Deleted user {user_id}")
    else:
        typer.echo(f"  Error {r.status_code}: {r.text}", err=True)
        raise typer.Exit(1)


# ===========================================================================
# policy sub-commands  (chmod / chown)
# ===========================================================================
