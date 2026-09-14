"""Brain commands."""

from __future__ import annotations


import typer

from repl._helpers import _brain_get, _brain_post, _brain_url


def brain_validate(
    spec: str = typer.Argument(..., help="MicroserviceSpec as JSON string or @path/to/file.json"),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Validate a MicroserviceSpec without generating code.  [make check]"""
    import json as _json

    if spec.startswith("@"):
        from pathlib import Path as _Path

        try:
            body = _json.loads(_Path(spec[1:]).read_text())
        except (FileNotFoundError, _json.JSONDecodeError) as e:
            typer.echo(f"  Cannot read spec: {e}", err=True)
            raise typer.Exit(1)
    else:
        try:
            body = _json.loads(spec)
        except _json.JSONDecodeError:
            typer.echo("  spec must be valid JSON or @file.json", err=True)
            raise typer.Exit(1)
    _brain_post("/api/v1/codegen/microservice/validate", url or _brain_url(), body, json_output)


# ── brain benchmark  [like: perf / hyperfine] ────────────────────────────────


def brain_benchmark(
    question: str = typer.Argument(..., help="Question to run through full Bellman cycle"),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Run a full sim→query→loss Bellman cycle and report timing + loss.  [perf]"""
    import time as _time

    t0 = _time.perf_counter()
    _brain_post(
        "/api/v1/agentos/test-bellman-cycle",
        url or _brain_url(),
        {"question": question},
        json_output,
    )
    elapsed = _time.perf_counter() - t0
    if not json_output:
        typer.echo(f"\n  benchmark: {elapsed:.3f}s total")


# ── brain memory  [like: /proc/meminfo] ──────────────────────────────────────


def brain_memory(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show loaded somatic charts, prompts, and knowledge packs.  [/proc/meminfo]"""
    import httpx, json as _json
    from pathlib import Path as _Path

    base = url or _brain_url()

    result: dict = {}

    try:
        charts_r = httpx.get(f"{base}/somatic/charts", timeout=8)
        prompts_r = httpx.get(f"{base}/somatic/prompts", timeout=8)
        caps_r = httpx.get(f"{base}/somatic/capabilities", timeout=8)
        result["charts"] = charts_r.json() if charts_r.status_code == 200 else {}
        result["prompts"] = prompts_r.json() if prompts_r.status_code == 200 else {}
        result["capabilities"] = caps_r.json() if caps_r.status_code == 200 else {}
    except httpx.ConnectError:
        result["error"] = f"brain offline at {base}"

    kp_dir = _Path("/Users/prashunjaveri/Code/monkeypatched/somatic/knowledge_packs")
    result["knowledge_packs"] = [p.stem for p in sorted(kp_dir.glob("*.yaml"))] if kp_dir.exists() else []

    if json_output:
        typer.echo(_json.dumps(result, indent=2))
    else:
        typer.echo(f"  charts loaded : {result.get('charts', {}).get('total_charts', '?')}")
        prompts = result.get("prompts", {})
        typer.echo(f"  prompts       : {len(prompts.get('prompts', []))}")
        caps = result.get("capabilities", {})
        typer.echo(f"  capabilities  : {len(caps.get('capabilities', []))}")
        typer.echo(f"  knowledge packs: {len(result['knowledge_packs'])}")
        if result["knowledge_packs"]:
            for kp in result["knowledge_packs"]:
                typer.echo(f"    {kp}")


# ── brain world  [like: /proc/self/status] ────────────────────────────────────


def brain_world(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show the current world-model state (runtime + policy + persistence).  [/proc/self/status]"""
    _brain_get("/state", url or _brain_url(), json_output)


# ── brain policy  [like: /etc/security/] ─────────────────────────────────────


def brain_policy(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    reset: bool = typer.Option(False, "--reset", help="Reset healing cooldown before showing policy"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show RL policy — Bellman action-value table and transition graph.  [/etc/security/]"""
    if reset:
        _brain_post("/api/v1/agentos/prompt/reset", url or _brain_url(), {}, json_output=False)
    _brain_get("/api/v1/agentos/transitions", url or _brain_url(), json_output)


# ── brain identity  [like: whoami / id] ──────────────────────────────────────


def brain_identity(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show prompt-pipeline identity — health, stability, and cooldown state.  [whoami / id]"""
    import httpx, json as _json

    base = url or _brain_url()
    try:
        health_r = httpx.get(f"{base}/api/v1/agentos/prompt/health", timeout=8)
        stability_r = httpx.get(f"{base}/api/v1/agentos/prompt/stability", timeout=8)
    except httpx.ConnectError:
        typer.echo(f"  Cannot connect to MonkeyBrain at {base}.", err=True)
        raise typer.Exit(1)
    result = {
        "health": health_r.json() if health_r.status_code == 200 else {},
        "stability": stability_r.json() if stability_r.status_code == 200 else {},
    }
    if json_output:
        typer.echo(_json.dumps(result))
    else:
        typer.echo(_json.dumps(result, indent=2))


# ── brain logs  [like: dmesg / journalctl] ───────────────────────────────────


def brain_logs(
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Show prompt-pipeline health log — errors and healing status.  [dmesg]"""
    _brain_get("/api/v1/agentos/prompt/health", url or _brain_url(), json_output)
