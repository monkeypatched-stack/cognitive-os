"""Login / logout / API key commands — gates the rest of the CLI (see the
root app callback in __init__.py) behind a session stored at
~/.monkeybrain/session.json.
"""

from __future__ import annotations

import getpass

import httpx
import typer

from repl._helpers import _auth_url, _load_session, _save_session, _clear_session
from repl.theme import print_error, print_success


def _fetch_identity(base: str, access_token: str) -> dict:
    """GET /api/v1/me with the given token. Returns {} on any failure."""
    try:
        r = httpx.get(
            f"{base}/api/v1/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def login_cmd(
    email: str = typer.Option("", "--email", "-e", help="Email address (prompted if omitted and no --api-key)"),
    password: str = typer.Option("", "--password", "-p", help="Password (prompted if omitted and no --api-key)"),
    api_key: str = typer.Option(
        "",
        "--api-key",
        "-k",
        help="Log in directly with a previously issued API key (access token), skipping email/password",
    ),
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL (or AUTH_SERVICE_URL)"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Log in — required before any other command works.  [like login]"""
    base = auth_url or _auth_url()

    if api_key:
        identity = _fetch_identity(base, api_key)
        if not identity:
            print_error("Invalid or expired API key.")
            raise typer.Exit(1)
        _save_session(
            {
                "access_token": api_key,
                "refresh_token": "",
                "email": identity.get("email", ""),
            }
        )
        if json_output:
            import json as _json

            typer.echo(_json.dumps(identity, indent=2))
        else:
            print_success(f"Logged in as {identity.get('email', '?')} (via API key)")
        return

    if not email:
        email = typer.prompt("  Email")
    if not password:
        password = getpass.getpass("  Password: ")

    try:
        r = httpx.post(
            f"{base}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=15,
        )
    except httpx.ConnectError:
        print_error(f"Cannot connect to auth service at {base}. Is it running?")
        raise typer.Exit(1)

    if r.status_code != 200:
        print_error(f"Login failed ({r.status_code}): {r.text}")
        raise typer.Exit(1)

    data = r.json()

    if data.get("mfa_required"):
        code = typer.prompt("  MFA code")
        r = httpx.post(
            f"{base}/api/v1/auth/mfa/verify",
            json={"mfa_challenge_token": data["mfa_challenge_token"], "code": code},
            timeout=15,
        )
        if r.status_code != 200:
            print_error(f"MFA verification failed ({r.status_code}): {r.text}")
            raise typer.Exit(1)
        data = r.json()

    access_token = data.get("access_token")
    if not access_token:
        print_error(f"Login succeeded but no access_token was returned: {data}")
        raise typer.Exit(1)

    _save_session(
        {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", ""),
            "email": email,
        }
    )

    if json_output:
        import json as _json

        typer.echo(_json.dumps(data, indent=2))
    else:
        print_success(f"Logged in as {email}")


def logout_cmd(
    auth_url: str = typer.Option("", "--auth-url", help="Auth service URL (or AUTH_SERVICE_URL)"),
):
    """Log out and clear the local session.  [like logout]"""
    session = _load_session()
    refresh_token = session.get("refresh_token")
    base = auth_url or _auth_url()

    if refresh_token:
        try:
            httpx.post(
                f"{base}/api/v1/auth/logout",
                cookies={"refresh_token": refresh_token},
                timeout=10,
            )
        except Exception:
            pass  # best-effort — always clear the local session below

    _clear_session()
    print_success("Logged out")


def api_key_cmd(
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show the current session's API key (bearer token) — reuse it with
    `monkeypatched login --api-key <key>` to authenticate another shell.
    [like cat ~/.ssh/id_rsa.pub]
    """
    session = _load_session()
    token = session.get("access_token")
    if not token:
        print_error("Not logged in. Run `monkeypatched login` first.")
        raise typer.Exit(1)

    if json_output:
        import json as _json

        typer.echo(_json.dumps({"api_key": token, "email": session.get("email", "")}, indent=2))
    else:
        typer.echo(token)
