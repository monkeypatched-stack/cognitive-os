"""SDLC pipeline commands — drive CodeGenRuntime's full SDLC ExecutionGraph
(Specification -> Knowledge Acquisition -> Requirements -> Architecture ->
Design -> Service Generation (DDD + compliance fan-out) -> Review -> Testing
-> Packaging -> Git Integration -> Release) via the /api/v1/agentos/sdlc/*
routes (src/monkey_brain/api/routes/sdlc.py).
"""

from __future__ import annotations

import json as _json
import time

import httpx
import typer

from repl._helpers import _brain_url, _brain_get, _brain_post, _auth_headers
from repl.theme import console, print_error

# POST /sdlc/run now returns as soon as the run is *started* (run_id +
# state="running") — the pipeline itself keeps going server-side via a
# background task (routes/sdlc.py's _tick_in_background), so this is just
# for the initial kickoff request, not the whole run.
_SDLC_START_TIMEOUT = 120.0  # compile_intent() itself calls the LLM to compile the question into an IntentIR
_SDLC_POLL_INTERVAL = 3.0
_SDLC_MAX_WAIT = 1800.0  # 30 minutes — many sequential LLM calls across stages


def run_sdlc_pipeline(
    question: str,
    compliance: list[str] | None = None,
    execution_mode: str = "serial",
    url: str = "",
) -> dict:
    """Start the SDLC pipeline and poll it to completion, printing live
    progress — the shared call both `sdlc run` and `discover --approve`
    use, so approving a discovered specification actually starts the
    pipeline instead of just printing "auto-approved" and stopping there.

    Unlike a single long-blocking POST (which gave no feedback for however
    many minutes the whole pipeline took, and could time out client-side
    before the server finished), this returns run_id immediately and polls
    GET /sdlc/{run_id} every few seconds, showing node-complete counts and
    which node is currently running.
    """
    base = url or _brain_url()
    body: dict = {"question": question, "execution_mode": execution_mode}
    if compliance:
        body["compliance_domains"] = compliance

    resp = httpx.post(
        f"{base}/api/v1/agentos/sdlc/run",
        json=body,
        timeout=_SDLC_START_TIMEOUT,
        headers=_auth_headers(),
    )
    data = resp.json()
    run_id = data.get("run_id")
    if not run_id:
        return data

    deadline = time.monotonic() + _SDLC_MAX_WAIT
    with console.status("[muted]Starting SDLC pipeline...[/muted]") as status:
        while time.monotonic() < deadline:
            time.sleep(_SDLC_POLL_INTERVAL)
            r = httpx.get(
                f"{base}/api/v1/agentos/sdlc/{run_id}",
                timeout=10.0,
                headers=_auth_headers(),
            )
            data = r.json()
            if data.get("state") != "running":
                return data

            summary = data.get("graph_summary", {})
            states = summary.get("states", {})
            running_node = next(
                (n["id"] for n in data.get("nodes", []) if n.get("state") == "running"),
                None,
            )
            progress = f"{states.get('complete', 0)}/{summary.get('total_nodes', '?')} nodes complete"
            if running_node:
                progress += f" — now on: {running_node}"
            status.update(f"[muted]Running SDLC pipeline — {progress}...[/muted]")

    return data


def sdlc_run(
    question: str = typer.Argument(..., help="Natural-language software specification/intent."),
    compliance: list[str] = typer.Option(
        [],
        "--compliance",
        "-c",
        help="Compliance domains to fan out over (e.g. soc2, gdpr, iso27001). Repeatable.",
    ),
    execution_mode: str = typer.Option("serial", "--mode", "-m", help="Node execution mode: serial or parallel."),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Run the full SDLC pipeline end-to-end for a specification.

    Starts a CodeGenRuntime Runtime Process and walks the whole
    Specification -> Release graph. Returns once the run reaches a
    non-RUNNING state — COMPLETED, FAILED, or WAITING on a human-approval
    gate (use `sdlc status`/`sdlc approve` to continue from there). This can
    take several minutes — every stage is a real LLM call, run serially.

    Examples:
      sdlc run "Build a work-order tracking service"
      sdlc run "Build a customer service" --compliance soc2 --compliance gdpr
    """
    try:
        data = run_sdlc_pipeline(question, compliance, execution_mode, url)
    except Exception as exc:
        base = url or _brain_url()
        print_error(f"Error reaching {base}/api/v1/agentos/sdlc/run: {exc}")
        raise typer.Exit(1)

    typer.echo(_json.dumps(data, indent=2))


def sdlc_status(
    run_id: str = typer.Argument(..., help="Run ID returned by `sdlc run`."),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Poll an SDLC run's current state — RUNNING/WAITING/COMPLETED/FAILED."""
    base = url or _brain_url()
    _brain_get(f"/api/v1/agentos/sdlc/{run_id}", base, json_output)


def sdlc_approve(
    run_id: str = typer.Argument(..., help="Run ID to approve/reject."),
    reject: bool = typer.Option(False, "--reject", help="Reject instead of approve."),
    note: str = typer.Option("", "--note", "-n", help="Approval/rejection note."),
    url: str = typer.Option("", "--url", "-u", help="MonkeyBrain URL"),
    json_output: bool = typer.Option(False, "--json", help="Raw JSON output"),
):
    """Approve or reject a pending human-approval gate and resume the run."""
    base = url or _brain_url()
    body = {"decision": "reject" if reject else "approve", "note": note}
    _brain_post(f"/api/v1/agentos/sdlc/{run_id}/approve", base, body, json_output)


# ── Per-stage inspection ───────────────────────────────────────────────────
# Node IDs from src/monkey_brain/kernel/codegen_runtime.py's build_sdlc_graph().
# These commands don't run a stage in isolation (the backend has no such
# endpoint) — they filter `sdlc status`'s per-node detail down to the nodes
# belonging to one stage, so you can inspect that stage's state/result
# without wading through the full 30+ node dump.
_STAGE_MATCHERS: dict[str, "callable"] = {
    "discovery": lambda nid: nid == "sdlc:specification_discovery",
    "specification": lambda nid: nid == "sdlc:specification",
    "requirements": lambda nid: nid == "sdlc:requirements",
    "architecture": lambda nid: nid == "sdlc:architecture",
    "design": lambda nid: nid == "sdlc:design",
    "implementation": lambda nid: nid.startswith("sdlc:implementation:"),
    "review": lambda nid: nid.startswith("sdlc:review"),
    "testing": lambda nid: nid.startswith("sdlc:testing:"),
    "packaging": lambda nid: nid == "sdlc:packaging",
    "release": lambda nid: nid in ("sdlc:release", "sdlc:release:pull_request", "sdlc:deploy"),
}


def _show_stage(stage: str, run_id: str, url: str, json_output: bool) -> None:
    # Not using _brain_get here — it always echoes the full raw response,
    # which would print the whole (unfiltered) status dump before this
    # function gets a chance to print just the matching stage's nodes.
    base = url or _brain_url()
    try:
        resp = httpx.get(f"{base}/api/v1/agentos/sdlc/{run_id}", timeout=10, headers=_auth_headers())
        data = resp.json()
    except Exception as exc:
        print_error(f"Error reaching {base}/api/v1/agentos/sdlc/{run_id}: {exc}")
        raise typer.Exit(1)

    nodes = [n for n in data.get("nodes", []) if _STAGE_MATCHERS[stage](n["id"])]

    if json_output:
        typer.echo(_json.dumps(nodes, indent=2))
        return

    if not nodes:
        print_error(
            f"No {stage!r} nodes found for run_id={run_id!r} (wrong run_id, or the run hasn't reached this stage yet)."
        )
        raise typer.Exit(1)

    for n in nodes:
        typer.echo(f"\n{n['id']}  [{n['type']}]  state={n['state']}")
        if n.get("result") is not None:
            typer.echo(_json.dumps(n["result"], indent=2, default=str))


def sdlc_discovery(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the specification-discovery stage's node(s) for a run."""
    _show_stage("discovery", run_id, url, json_output)


def sdlc_specification(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the specification (prompt-compile) stage's node(s) for a run."""
    _show_stage("specification", run_id, url, json_output)


def sdlc_requirements(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the requirements stage's node(s) for a run."""
    _show_stage("requirements", run_id, url, json_output)


def sdlc_architecture(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the architecture-decision stage's node(s) for a run."""
    _show_stage("architecture", run_id, url, json_output)


def sdlc_design(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the design stage's node(s) for a run."""
    _show_stage("design", run_id, url, json_output)


def sdlc_implementation(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the implementation stage's nodes (service_gen + DDD constructs + compliance fan-out) for a run."""
    _show_stage("implementation", run_id, url, json_output)


def sdlc_review(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the code-review + governance-review stage's node(s) for a run."""
    _show_stage("review", run_id, url, json_output)


def sdlc_testing(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the testing stage's nodes (authoring/unit/service/integration) for a run."""
    _show_stage("testing", run_id, url, json_output)


def sdlc_packaging(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the artifact-packaging stage's node(s) for a run."""
    _show_stage("packaging", run_id, url, json_output)


def sdlc_release(
    run_id: str = typer.Argument(...),
    url: str = typer.Option("", "--url", "-u"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show the git-integration/release stage's nodes (PR, deploy, release) for a run."""
    _show_stage("release", run_id, url, json_output)
