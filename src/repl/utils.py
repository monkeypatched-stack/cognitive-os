"""Utility commands."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from repl._helpers import (
    _SVC_PORTS,
    _MB_HOME as _MB_DIR,
    _brain_url,
    _brain_post,
    logger,
)
from repl.theme import console, print_banner


def logs_cmd(
    service: str = typer.Argument("", help="Service name (empty = list available logs)"),
    lines: int = typer.Option(50, "--lines", "-n", help="Lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow (tail -f)"),
):
    """Tail service logs.  [like journalctl]"""
    import subprocess

    log_dir = _MB_DIR / "logs"
    if not service:
        if not log_dir.exists():
            typer.echo("  No log directory found at ~/.monkeybrain/logs/")
            return
        typer.echo(f"  Available logs in {log_dir}:")
        for p in sorted(log_dir.glob("*.log")):
            typer.echo(f"    {p.stem}")
        return

    log = log_dir / f"{service}.log"
    if not log.exists():
        # fallback: delegate to install_agentos
        from install_agentos import main as install_main

        sys.argv = ["monkeybrain", "logs"] + ([service] if service else [])
        install_main()
        return

    cmd = ["tail", f"-{lines}"] + (["-f"] if follow else []) + [str(log)]
    subprocess.run(cmd, capture_output=False)


def mount_cmd(
    source: str = typer.Argument(
        "",
        help="Source to mount: 'charts', 'knowledge', 'model:<name>', or a somatic chart path",
    ),
    mount_type: str = typer.Option("", "--type", "-t", help="Mount type: charts|knowledge|model"),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Attach a knowledge source, chart set, or model into the brain.  [like mount]

    Examples:
        monkeypatched mount charts              # reload all somatic charts
        monkeypatched mount knowledge           # scan and report knowledge packs
        monkeypatched mount model:claude-opus-4-8  # switch active model backend
    """
    from pathlib import Path as _Path

    # Resolve type from source prefix if not given
    effective_type = mount_type
    if not effective_type:
        if source.startswith("model:"):
            effective_type = "model"
        elif source in ("charts", "somatic"):
            effective_type = "charts"
        elif source in ("knowledge", "packs"):
            effective_type = "knowledge"
        else:
            effective_type = "charts"  # default

    base = url or _brain_url()

    if effective_type == "charts":
        typer.echo("  Recompiling somatic charts...")
        _brain_post("/somatic/recompile", base, {}, json_output)

    elif effective_type == "knowledge":
        # Scan local knowledge_packs dir and report
        kp_dir = _Path("/Users/prashunjaveri/Code/monkeypatched/somatic/knowledge_packs")
        if not kp_dir.exists():
            typer.echo(f"  Knowledge pack directory not found: {kp_dir}", err=True)
            raise typer.Exit(1)
        packs = sorted(kp_dir.glob("*.yaml"))
        typer.echo(f"  Knowledge packs in {kp_dir}:")
        for p in packs:
            typer.echo(f"    {p.stem}")
        typer.echo(f"\n  {len(packs)} packs available. Use 'monkeypatched mount charts' to re-register into Runtime.")

    elif effective_type == "model":
        model_name = source.removeprefix("model:") if source.startswith("model:") else source
        typer.echo(f"  Configuring model backend: {model_name}")
        _brain_post(
            "/api/v1/agentos/prompt",
            base,
            {"question": f"switch model to {model_name}", "run_type": "codegen"},
            json_output,
        )

    else:
        typer.echo(
            f"  Unknown mount type: {effective_type}. Use charts|knowledge|model.",
            err=True,
        )
        raise typer.Exit(1)


def monitor_cmd(
    interval: float = typer.Option(5.0, "--interval", "-i", help="Refresh interval in seconds"),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    once: bool = typer.Option(False, "--once", help="Print once and exit (no loop)"),
):
    """Live system monitor — services, agents, and brain metrics.  [like top]

    Press Ctrl+C to exit.
    """
    import time, socket, httpx, os

    base = url or _brain_url()

    def _port_status(port: int) -> str:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return "UP"
        except (ConnectionRefusedError, OSError):
            return "DOWN"

    def _render():
        lines: list[str] = []
        lines.append(f"  MonkeyBrain Monitor — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  Brain: {base}  |  interval: {interval}s  |  Ctrl+C to quit")
        lines.append("")

        # Brain health
        try:
            r = httpx.get(f"{base}/health", timeout=3)
            health = r.json()
            lines.append(f"  BRAIN  status={health.get('status', '?')}  health={health.get('health', '?')}")
        except Exception:
            lines.append("  BRAIN  status=unreachable")

        # Brain metrics (abbreviated)
        try:
            r = httpx.get(f"{base}/observability", timeout=3)
            m = r.json()
            if isinstance(m, dict):
                for k, v in list(m.items())[:6]:
                    lines.append(f"    {k}: {v}")
        except Exception as e:
            logger.debug("Exception caught: %s", e)

        lines.append("")
        lines.append(f"  {'SERVICE':<22s} {'PORT':>6s}  STATUS")
        lines.append(f"  {'-' * 22} {'-' * 6}  {'-' * 6}")
        return lines

    def _render_services() -> None:
        for svc, port in _SVC_PORTS.items():
            status = _port_status(port)
            indicator = "●" if status == "UP" else "○"
            style = "success" if status == "UP" else "error"
            console.print(f"  [{style}]{indicator} {svc:<21s} :{port:<5d}  {status}[/{style}]")

    if once:
        for line in _render():
            typer.echo(line)
        _render_services()
        return

    try:
        while True:
            # Clear screen
            import subprocess as _sp

            _sp.run(["clear"] if os.name != "nt" else ["cls"], check=False)
            print_banner()
            for line in _render():
                typer.echo(line)
            _render_services()
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("\n  Monitor stopped.")


def ls_cmd(
    resource_type: str = typer.Option("", "--type", "-t", help="Resource type: chart|storage|knowledge|agent"),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """List MonkeyBrain resources.  [like ls]

    Without --type shows a summary of all resource types.

    Types:
        chart      — somatic charts loaded in Runtime
        storage    — local ~/.monkeybrain/data/ directories
        knowledge  — knowledge packs (somatic/knowledge_packs/*.yaml)
        agent      — registered agents (calls brain API)
    """
    from pathlib import Path as _Path

    base = url or _brain_url()

    if resource_type in ("chart", "charts", ""):
        typer.echo("  ── Somatic Charts ───────────────────────────────────────")
        try:
            import httpx

            r = httpx.get(f"{base}/somatic/charts", timeout=5)
            if json_output:
                typer.echo(r.text)
            else:
                d = r.json()
                typer.echo(f"  total_charts: {d.get('total_charts', '?')}")
                for k, v in d.items():
                    if k != "total_charts":
                        typer.echo(f"    {k}: {v}")
        except Exception:
            typer.echo("  (brain offline — reading local somatic dir)")
            somatic = _Path("/Users/prashunjaveri/Code/monkeypatched/somatic/charts")
            if somatic.exists():
                for p in sorted(somatic.rglob("values.yaml")):
                    typer.echo(f"    {p.relative_to(somatic)}")

    if resource_type in ("storage", ""):
        typer.echo("\n  ── Storage ──────────────────────────────────────────────")
        data_dir = _MB_DIR / "data"
        if data_dir.exists():
            for d in sorted(data_dir.iterdir()):
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                typer.echo(f"    {d.name:<20s}  {size // 1024:>8d} KB")
        else:
            typer.echo("  (no data dir — run 'monkeypatched svc install' first)")

    if resource_type in ("knowledge", ""):
        typer.echo("\n  ── Knowledge Packs ──────────────────────────────────────")
        kp_dir = _Path("/Users/prashunjaveri/Code/monkeypatched/somatic/knowledge_packs")
        if kp_dir.exists():
            for p in sorted(kp_dir.glob("*.yaml")):
                typer.echo(f"    {p.stem}")
        else:
            typer.echo("  (no knowledge_packs dir)")

    if resource_type in ("agent", "agents", ""):
        typer.echo("\n  ── Agents ───────────────────────────────────────────────")
        try:
            import httpx

            r = httpx.get(f"{base}/api/v1/agentos/agents", timeout=5)
            data = r.json() if r.status_code == 200 else []
            if isinstance(data, dict):
                agents = data.get("agents", [])
            elif isinstance(data, list):
                agents = data
            else:
                agents = []
            for a in agents:
                src = a.get("source", "local")
                typer.echo(f"    [{src:>6s}]  {a.get('agent_type', '?')}")
            if not agents and json_output:
                typer.echo(r.text)
        except Exception:
            typer.echo("  (brain offline)")


# ===========================================================================
# Top-level commands
# ===========================================================================


def serve_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Base port"),
):
    """Launch all MonkeyBrain microservices locally."""
    import subprocess

    cmd = [sys.executable, "main.py"]
    result = subprocess.run(cmd, capture_output=False)
    raise typer.Exit(result.returncode)


def clear_cmd():
    """Clear the terminal screen.  [like clear]"""
    import subprocess as _subprocess

    _subprocess.run(["clear"] if os.name != "nt" else ["cls"], check=False)


def start_cmd():
    """Open a new terminal window running the interactive monkeypatched shell.

    [like opening a fresh Claude Code session]
    """
    import platform
    import subprocess as _subprocess

    from repl.theme import print_error, print_info

    if platform.system() != "Darwin":
        print_error("`start` currently only supports macOS (Terminal.app).")
        raise typer.Exit(1)

    repo_root = Path(__file__).resolve().parents[2]
    python = sys.executable
    shell_cmd = f"cd {repo_root} && PYTHONPATH=src {python} -m repl"
    # AppleScript string literal — escape backslashes/quotes so paths with
    # spaces or embedded quotes don't break out of the "do script" argument.
    escaped = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Terminal" to do script "{escaped}"'

    result = _subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        print_error(f"Could not open a new terminal: {result.stderr.strip()}")
        raise typer.Exit(1)

    print_info("Opened a new terminal with the monkeypatched shell.")


def stop_cmd():
    """Stop all MonkeyBrain services and API processes."""
    import argparse
    from install_agentos import cmd_stop

    raise typer.Exit(cmd_stop(argparse.Namespace(skip=[])))


def restart_cmd():
    """Restart all MonkeyBrain services and API processes."""
    import argparse
    from install_agentos import cmd_stop
    from repl.theme import print_info

    cmd_stop(argparse.Namespace(skip=[]))
    print_info("Restarting MonkeyBrain services...")
    start_cmd()


def uninstall_cmd(
    all_: bool = typer.Option(
        False,
        "--all",
        help="Remove everything: MonkeyBrain install + all database services",
    ),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
):
    """Remove MonkeyBrain completely.

    Without --all: removes ~/.monkeybrain (venv, config, secrets, logs, data).
    With --all:    also stops and uninstalls MongoDB, Redis, NATS, InfluxDB via brew.
    """
    import argparse
    from install_agentos import cmd_uninstall

    args = argparse.Namespace(yes=yes, purge=all_)
    raise typer.Exit(cmd_uninstall(args))
