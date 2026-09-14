"""monkeypatched v1.0.5 — Cognitive Operating System CLI

OS analogy: monkeypatched IS the OS shell for MonkeyBrain.

  ── Cognitive Kernel ─────────────────────────────────────
  monkeypatched health          [ping]           GET /health
  monkeypatched status          [systemctl status] GET /state
  monkeypatched version         [uname -r]
  monkeypatched query           [exec r/o]        POST /agentos/query
  monkeypatched prompt          [exec script]     POST /agentos/prompt
  monkeypatched simulate        [strace/dry-run]  POST /agentos/simulate
  monkeypatched compare         [diff]            POST /agentos/compare
  monkeypatched agents          [ps]              GET /agentos/agents
  monkeypatched capabilities    [ls /bin]         GET /agentos/capabilities
  monkeypatched workflows       [jobs]            GET /agentos/transitions
  monkeypatched pipelines       [jobs -l]         GET /state + /metrics
  monkeypatched codegen         [make / cc]       POST /codegen/microservice
  monkeypatched validate        [make check]      POST /codegen/validate
  monkeypatched benchmark       [perf]            POST /agentos/test-bellman-cycle
  monkeypatched memory          [/proc/meminfo]   somatic charts + knowledge
  monkeypatched world           [/proc/status]    GET /state (world model)
  monkeypatched transitions     [/etc/security/]  RL policy transitions
  monkeypatched whoami          [whoami]          GET /agentos/prompt/health
  monkeypatched dmesg           [dmesg]           prompt pipeline error log
  monkeypatched mount           [mount]           reload charts/knowledge
  monkeypatched monitor         [top]             live service dashboard
  monkeypatched ls              [ls]              list brain resources
  monkeypatched logs            [journalctl]      tail service logs

  ── Infrastructure sub-apps ──────────────────────────────
  monkeypatched svc             [systemctl]       install|start|stop|status|configure|seed|logs|uninstall
  monkeypatched store           [lvm/fdisk]       install|start|stop|restart|status|list|logs|shell
  monkeypatched mq              [pipe/ipc]        nats|publish|monitor|kafka|cdc|sensors|approvals
  monkeypatched make            [make/cc]         build|lint|run|prompts|capabilities|reviews|...
  monkeypatched pkg             [apt/brew]        publish|pull|list|search|info|remove|serve|...
  monkeypatched init            [init.d/launchd]  run|pipeline
  monkeypatched proc            [ps / kill]       list|stop
  monkeypatched id              [useradd/id]      create|list|delete
  monkeypatched acl             [chmod/acl]       grant|revoke|list
  monkeypatched cron            [crontab]         list|add|remove|run
  monkeypatched serve           [init.d start]    launch all microservices
  monkeypatched stop            [shutdown]        stop all services and API processes
  monkeypatched uninstall       [rm -rf]          remove MonkeyBrain (--all removes databases too)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

__version__ = "1.0.5"

# Canonical repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure repo paths are importable
for _p in (
    str(_REPO_ROOT),
    str(_REPO_ROOT / "src"),
    str(_REPO_ROOT / "packages" / "broca"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Import command modules ─────────────────────────────────────────────────
from repl.services import (
    service_install,
    service_start,
    service_stop,
    service_status,
    service_configure,
    service_seed,
    service_logs,
    service_uninstall,
)
from repl.tests import test_cmd, test_report_cmd
from repl.soma import (
    soma_callback,
    soma_compile,
    soma_api,
    soma_review,
    soma_codegen,
    soma_govern,
    soma_fixit,
    soma_comply,
    soma_ddd_check,
    soma_seed,
    soma_serve,
    soma_client,
    soma_chart_from_client,
    soma_compile_agent,
    soma_create_agent,
    soma_test_service,
    soma_plan,
    soma_execute_plan,
    soma_run,
    soma_discover,
)
from repl.brain import (
    brain_validate,
    brain_benchmark,
    brain_memory,
    brain_world,
    brain_policy,
    brain_identity,
    brain_logs,
)
from repl.processes import process_list, process_stop
from repl.identity import identity_create, identity_list, identity_delete
from repl.policy import policy_grant, policy_revoke, policy_list
from repl.scheduler import (
    scheduler_list,
    scheduler_add,
    scheduler_remove,
    scheduler_run,
)
from repl.utils import (
    logs_cmd,
    mount_cmd,
    monitor_cmd,
    ls_cmd,
    serve_cmd,
    stop_cmd,
    uninstall_cmd,
    start_cmd,
    restart_cmd,
    clear_cmd,
)
from repl.login import login_cmd, logout_cmd, api_key_cmd
from repl.sdlc import (
    sdlc_run,
    sdlc_status,
    sdlc_approve,
    sdlc_discovery,
    sdlc_specification,
    sdlc_requirements,
    sdlc_architecture,
    sdlc_design,
    sdlc_implementation,
    sdlc_review,
    sdlc_testing,
    sdlc_packaging,
    sdlc_release,
)
from repl._helpers import _is_logged_in
from repl.theme import print_error

# ── Typer App ──────────────────────────────────────────────────────────────
# rich_markup_mode=None everywhere below: command docstrings use literal
# bracketed unix-analogy annotations (e.g. "[like ps]", "[/proc/meminfo]") —
# without this, Rich's markup parser tries to interpret those brackets as
# style tags and raises MarkupError on any --help call whose docstring
# contains one that isn't validly paired.
app = typer.Typer(
    name="monkeypatched",
    help="MonkeyBrain Cognitive Operating System CLI",
    no_args_is_help=True,
    rich_markup_mode=None,
)

# ── Service sub-app ────────────────────────────────────────────────────────
svc_app = typer.Typer(help="Service management", rich_markup_mode=None, no_args_is_help=True)
svc_app.command("install")(service_install)
svc_app.command("start")(service_start)
svc_app.command("stop")(service_stop)
svc_app.command("status")(service_status)
svc_app.command("configure")(service_configure)
svc_app.command("seed")(service_seed)
svc_app.command("logs")(service_logs)
svc_app.command("uninstall")(service_uninstall)
app.add_typer(svc_app, name="svc")

# ── Make sub-app (build/lint/run/prompts/capabilities/reviews) ─────────────
# Registered as "make" (not "soma") to match the Linux-style mapping this
# module's docstring already documents: [make/cc] build|lint|run|prompts|...
make_app = typer.Typer(
    help="Build, compile, review, and run SOMA charts/services",
    callback=soma_callback,
    rich_markup_mode=None,
    no_args_is_help=True,
)
make_app.command("compile")(soma_compile)
make_app.command("api")(soma_api)
make_app.command("review")(soma_review)
make_app.command("codegen")(soma_codegen)
make_app.command("govern")(soma_govern)
make_app.command("fixit")(soma_fixit)
make_app.command("comply")(soma_comply)
make_app.command("ddd-check")(soma_ddd_check)
make_app.command("seed")(soma_seed)
make_app.command("serve")(soma_serve)
make_app.command("client")(soma_client)
make_app.command("chart-from-client")(soma_chart_from_client)
make_app.command("compile-agent")(soma_compile_agent)
make_app.command("create-agent")(soma_create_agent)
make_app.command("test-service")(soma_test_service)
make_app.command("plan")(soma_plan)
make_app.command("execute-plan")(soma_execute_plan)
make_app.command("run")(soma_run)
app.add_typer(make_app, name="make")

# ── Discover — top-level, not under "make": it drives the cognitive pipeline
# (spec → plan → execute → observe → learn), it does not build a chart.
app.command("discover")(soma_discover)

# ── Brain introspection — flattened to top-level (see docstring: validate
# [make check], benchmark [perf], memory [/proc/meminfo], world [/proc/status],
# transitions [/etc/security/], dmesg [dmesg]) rather than nested under a
# "brain" group, matching every other top-level Linux-analogue command.
app.command("validate")(brain_validate)
app.command("benchmark")(brain_benchmark)
app.command("memory")(brain_memory)
app.command("world")(brain_world)
app.command("transitions")(brain_policy)
app.command("identity")(brain_identity)
app.command("dmesg")(brain_logs)

# ── Process sub-app ────────────────────────────────────────────────────────
proc_app = typer.Typer(help="Process management", rich_markup_mode=None, no_args_is_help=True)
proc_app.command("list")(process_list)
proc_app.command("stop")(process_stop)
app.add_typer(proc_app, name="proc")

# ── Identity sub-app ───────────────────────────────────────────────────────
id_app = typer.Typer(help="Identity management", rich_markup_mode=None, no_args_is_help=True)
id_app.command("create")(identity_create)
id_app.command("list")(identity_list)
id_app.command("delete")(identity_delete)
app.add_typer(id_app, name="id")

# ── Policy sub-app ─────────────────────────────────────────────────────────
acl_app = typer.Typer(help="Policy management", rich_markup_mode=None, no_args_is_help=True)
acl_app.command("grant")(policy_grant)
acl_app.command("revoke")(policy_revoke)
acl_app.command("list")(policy_list)
app.add_typer(acl_app, name="acl")

# ── Scheduler sub-app ──────────────────────────────────────────────────────
cron_app = typer.Typer(help="Job scheduler", rich_markup_mode=None, no_args_is_help=True)
cron_app.command("list")(scheduler_list)
cron_app.command("add")(scheduler_add)
cron_app.command("remove")(scheduler_remove)
cron_app.command("run")(scheduler_run)
app.add_typer(cron_app, name="cron")

# ── SDLC sub-app ───────────────────────────────────────────────────────────
sdlc_app = typer.Typer(
    help="Full SDLC pipeline: spec -> requirements -> ... -> release",
    rich_markup_mode=None,
    no_args_is_help=True,
)
sdlc_app.command("run")(sdlc_run)
sdlc_app.command("status")(sdlc_status)
sdlc_app.command("approve")(sdlc_approve)
sdlc_app.command("discovery")(sdlc_discovery)
sdlc_app.command("specification")(sdlc_specification)
sdlc_app.command("requirements")(sdlc_requirements)
sdlc_app.command("architecture")(sdlc_architecture)
sdlc_app.command("design")(sdlc_design)
sdlc_app.command("implementation")(sdlc_implementation)
sdlc_app.command("review")(sdlc_review)
sdlc_app.command("testing")(sdlc_testing)
sdlc_app.command("packaging")(sdlc_packaging)
sdlc_app.command("release")(sdlc_release)
app.add_typer(sdlc_app, name="sdlc")

# ── Top-level commands ─────────────────────────────────────────────────────


def install_cmd(
    auto_install: bool = typer.Option(False, "--auto-install", help="Auto-install missing database services"),
    skip_ollama: bool = typer.Option(False, "--skip-ollama", help="Skip Ollama (local LLM)"),
):
    """Full installation of MonkeyBrain — databases, Ollama, config, and CLI."""
    import argparse, os
    from install_agentos import cmd_install

    args = argparse.Namespace(
        auto_install=auto_install,
        skip=["ollama"] if skip_ollama else [],
        skip_mongo=False,
        skip_redis=False,
        skip_neo4j=False,
        skip_influxdb=False,
        skip_elasticsearch=False,
        skip_nats=False,
        skip_ollama=skip_ollama,
        skip_api=False,
        mongo_url=None,
        mongo_port=27017,
        db_name="demo",
        redis_url=None,
        redis_port=6379,
        neo4j_uri=None,
        neo4j_port=7687,
        neo4j_user="neo4j",
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        influxdb_url=None,
        influxdb_port=8181,
        influxdb_org="indus",
        influxdb_bucket="events",
        influxdb_token=os.getenv("INFLUXDB_TOKEN", ""),
        elasticsearch_url=None,
        elasticsearch_port=9200,
        nats_url=None,
        nats_port=4222,
        ollama_url=None,
        ollama_port=11434,
        ollama_model="gemma3:latest",
        port=8031,
    )
    cmd_install(args)


app.command("install")(install_cmd)
app.command("test")(test_cmd)
app.command("test-report")(test_report_cmd)
app.command("logs")(logs_cmd)
app.command("mount")(mount_cmd)
app.command("monitor")(monitor_cmd)
app.command("ls")(ls_cmd)
app.command("serve")(serve_cmd)
app.command("stop")(stop_cmd)
app.command("restart")(restart_cmd)
app.command("uninstall")(uninstall_cmd)
app.command("login")(login_cmd)
app.command("logout")(logout_cmd)
app.command("api-key")(api_key_cmd)
app.command("start")(start_cmd)
app.command("clear")(clear_cmd)

# Bootstrap commands that must work from a plain shell, before the REPL
# exists at all — everything else only runs inside `monkeypatched start`'s
# interactive shell (monkeypatched/repl.py sets MONKEYPATCHED_IN_REPL=1
# before dispatching each typed command through this same `app`).
_STANDALONE_ALLOWED_COMMANDS = {"start", "install", "uninstall"}

# Inside the REPL, these work without a login session; everything else
# needs `login` first.
_LOGIN_EXEMPT_COMMANDS = {"login", "logout", "clear", "make"}


@app.callback()
def _require_login(ctx: typer.Context) -> None:
    """Root callback — runs before every command (and every sub-app command,
    since Click/Typer always invokes the root group's callback first).

    Two gates: (1) most commands only run inside the REPL, not as one-shot
    shell invocations — `start`/`install`/`uninstall` are the exceptions,
    since they're what get you into the REPL in the first place; (2) inside
    the REPL, everything but _LOGIN_EXEMPT_COMMANDS needs an active session.
    """
    cmd = ctx.invoked_subcommand
    if cmd in _STANDALONE_ALLOWED_COMMANDS:
        return

    if os.environ.get("MONKEYPATCHED_IN_REPL") != "1":
        print_error("Commands only work inside the interactive shell. Run `monkeypatch` to open it.")
        raise typer.Exit(1)

    if cmd in _LOGIN_EXEMPT_COMMANDS:
        return
    if not _is_logged_in():
        print_error("Not logged in. Run `login` first.")
        raise typer.Exit(1)


def main() -> int:
    """Entry point for the monkeypatched CLI."""
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
