"""Process management."""

from __future__ import annotations


import typer

from repl._helpers import _SVC_PORTS, _brain_url, _pid_file, _is_pid_running, logger


def process_list(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List running MonkeyBrain processes — agents and microservices.  [like ps]"""
    import json as _json, socket

    rows: list[dict] = []

    # --- 1. Microservices: check each known port ---
    for svc_name, port in _SVC_PORTS.items():
        alive = False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                alive = True
        except (ConnectionRefusedError, OSError):
            pass  # not listening on this port — routine, not an error

        # check pid file too
        pf = _pid_file(svc_name)
        pid = None
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                if not _is_pid_running(pid):
                    pid = None
            except ValueError:
                pid = None

        if alive or pid:
            rows.append(
                {
                    "name": svc_name,
                    "type": "microservice",
                    "port": port,
                    "pid": pid,
                    "status": "running",
                }
            )

    # --- 2. Cognitive agents from MonkeyBrain API ---
    import httpx

    try:
        r = httpx.get(f"{url or _brain_url()}/api/v1/agentos/agents", timeout=5)
        data = r.json() if r.status_code == 200 else []
        agents = data.get("agents", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for agent in agents:
            rows.append(
                {
                    "name": agent.get("agent_type", "?"),
                    "type": "agent",
                    "port": None,
                    "pid": None,
                    "status": "registered",
                    "source": agent.get("source", "local"),
                }
            )
    except Exception as e:
        logger.debug("Agent registry query failed: %s", e)

    if json_output:
        typer.echo(_json.dumps(rows, indent=2))
        return

    if not rows:
        typer.echo("  No running processes found.")
        return

    typer.echo(f"  {'NAME':<24s} {'TYPE':<14s} {'PORT':>6s}  {'PID':>8s}  STATUS")
    typer.echo(f"  {'-' * 24} {'-' * 14} {'-' * 6}  {'-' * 8}  {'-' * 12}")
    for r in rows:
        port_s = f":{r['port']}" if r["port"] else "-"
        pid_s = str(r["pid"]) if r["pid"] else "-"
        src = f"  [{r.get('source', '')}]" if r["type"] == "agent" else ""
        typer.echo(f"  {r['name']:<24s} {r['type']:<14s} {port_s:>6s}  {pid_s:>8s}  {r['status']}{src}")


def process_stop(
    name: str = typer.Argument(..., help="Service or agent name to stop"),
):
    """Stop a running service process by name.  [like kill]"""
    import os, signal

    pf = _pid_file(name)
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pf.unlink(missing_ok=True)
            typer.echo(f"  Stopped {name} (PID {pid})")
            return
        except (ProcessLookupError, ValueError):
            pf.unlink(missing_ok=True)

    # fallback: brew / systemctl
    import subprocess, shutil

    if shutil.which("brew"):
        result = subprocess.run(["brew", "services", "stop", name], capture_output=True, text=True)
        if result.returncode == 0:
            typer.echo(f"  Stopped {name} via brew")
            return

    typer.echo(f"  No running process found for: {name}", err=True)
    raise typer.Exit(1)


# ===========================================================================
# identity sub-commands  (useradd / id)
# ===========================================================================
