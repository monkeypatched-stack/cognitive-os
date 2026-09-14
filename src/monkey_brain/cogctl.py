"""cogctl — the CognitiveOS declarative control CLI (Final Architectural
Convergence, Phase 6).

    cogctl apply -f actor.yaml
    cogctl create actor --name buyer-123 [--node-class edge] [...]
    cogctl get actors
    cogctl describe actor buyer-123
    cogctl logs actor buyer-123
    cogctl restart actor buyer-123
    cogctl stop actor buyer-123
    cogctl delete actor buyer-123

cogctl is a PURE HTTP CLIENT. It never imports PlanetaryRuntime, never
constructs a CognitiveActor, never starts a process, never touches
Redis/Mongo/NATS directly. Every command is exactly one (or a small,
fixed number of) HTTP request(s) against the Control API
(api/routes/actors.py) — the same API surface any other authenticated
caller uses. This is the literal enforcement of this task's own
invariant:

    cogctl
       -> Control API
       -> Actor Registry
       -> Scheduler
       -> Lifecycle Controller
       -> Actor Runtime

cogctl sits at the top of that chain and nowhere else in it.

Configuration (env vars, matching this repo's convention elsewhere):
    COGCTL_API_URL   — base URL, default http://localhost:8000/api/v1/agentos (Kong gateway)
    COGCTL_USER_ID    — sent as X-User-ID (dev-mode auth)
    COGCTL_API_KEY    — sent as `Authorization: Bearer <key>` (production auth)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

DEFAULT_API_URL = "http://localhost:8000/api/v1/agentos"


def _base_url() -> str:
    return os.getenv("COGCTL_API_URL", DEFAULT_API_URL).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("COGCTL_API_KEY", "")
    user_id = os.getenv("COGCTL_USER_ID", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif user_id:
        headers["X-User-ID"] = user_id
    return headers


class CogctlError(RuntimeError):
    pass


def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    import httpx

    url = f"{_base_url()}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=_headers(), json=json_body)
    except httpx.RequestError as exc:
        raise CogctlError(f"could not reach Control API at {url}: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise CogctlError(f"{method} {path} -> {resp.status_code}: {detail}")
    if not resp.content:
        return {}
    return resp.json()


# ── apply ────────────────────────────────────────────────────────────────


def cmd_apply(args: argparse.Namespace) -> int:
    import yaml

    with open(args.file, "r") as f:
        text = f.read()
    if args.file.endswith((".yaml", ".yml")):
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    result = _request("POST", "/actors/apply", json_body=doc)
    verb = "created" if result.get("created") else "updated"
    print(f"actor.cognitiveos/{result.get('actor_id')} {verb}")
    _print_json(result)
    return 0


def cmd_create_actor(args: argparse.Namespace) -> int:
    spec: dict[str, Any] = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": args.name},
        "spec": {
            "artifact": "cognitiveos-actor",
            "version": args.artifact_version or "",
            "placement": {
                "node_class": args.node_class or "",
                "required_capabilities": args.required_capability or [],
                "preferred_region": args.region or "",
                "claim_node": args.claim_node or "",
            },
            "resources": {"capacity": args.capacity},
            "configuration": {
                "goals": args.goal or [],
                "objective": args.objective or "",
                "tenant_id": args.tenant_id,
            },
        },
    }
    result = _request("POST", "/actors/apply", json_body=spec)
    verb = "created" if result.get("created") else "updated"
    print(f"actor.cognitiveos/{result.get('actor_id')} {verb}")
    return 0


# ── get ──────────────────────────────────────────────────────────────────


def cmd_get_actors(args: argparse.Namespace) -> int:
    result = _request("GET", "/actors/registry")
    entries = result if isinstance(result, list) else result.get("actors", result)
    if args.output == "json":
        _print_json(entries)
        return 0
    rows = entries if isinstance(entries, list) else []
    header = ("ACTOR_ID", "NAME", "STATUS", "NODE", "ARTIFACT", "RUNTIME", "UPDATED_AT")
    widths = [len(h) for h in header]
    table_rows = []
    for e in rows:
        row = (
            str(e.get("actor_id", "")),
            str(e.get("name", "")),
            str(e.get("status", "")),
            str(e.get("node_id", "")),
            str(e.get("artifact_version", "") or "-"),
            str(e.get("runtime_version", "") or "-"),
            str(e.get("updated_at", "")),
        )
        table_rows.append(row)
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    _print_table(header, table_rows, widths)
    return 0


# ── describe ─────────────────────────────────────────────────────────────


def cmd_describe_actor(args: argparse.Namespace) -> int:
    lifecycle = _request("GET", f"/actors/{args.actor_id}/lifecycle")
    placement = _request("GET", f"/actors/{args.actor_id}/placement")
    combined = {
        "actor_id": args.actor_id,
        "lifecycle": lifecycle,
        "placement": placement,
    }
    if args.output == "json":
        _print_json(combined)
        return 0
    print(f"Actor:        {args.actor_id}")
    print(f"Desired:      {lifecycle.get('desired_state')}")
    observed = lifecycle.get("observed", {})
    print(
        f"Observed:     status={observed.get('status')} node={observed.get('node_id')} "
        f"resident_here={observed.get('resident_here')} stale={observed.get('is_stale')}"
    )
    print(f"Desired node: {placement.get('desired_node_id')}")
    print(f"Requirements: {placement.get('requirements')}")
    history = lifecycle.get("history", [])
    if history:
        print(f"Recent lifecycle events ({len(history)}):")
        for entry in history[-10:]:
            print(f"  {entry}")
    return 0


def cmd_logs_actor(args: argparse.Namespace) -> int:
    """cogctl logs actor <id> — surfaces the Actor's LIFECYCLE event
    history (start/stop/suspend/resume/recover/migrate transitions), not
    application-level stdout. This is an honest distinction, not a
    placeholder: no centralized log-aggregation pipeline exists in this
    architecture yet (each Actor Runtime process's own stdout/stderr,
    captured however the deployment mechanism captures it — a K8s
    `kubectl logs` on that Pod, a systemd journal entry, a local file —
    remains the source of application-level log lines). See
    docs/COGNITIVEOS_FINAL_ARCHITECTURE.md's Observability section."""
    lifecycle = _request("GET", f"/actors/{args.actor_id}/lifecycle")
    history = lifecycle.get("history", [])
    if not history:
        print(f"(no lifecycle events recorded for {args.actor_id})")
        print(
            "Note: this shows lifecycle transitions, not application stdout — "
            "see the Actor Runtime process's own logs for that."
        )
        return 0
    for entry in history:
        print(json.dumps(entry))
    return 0


# ── lifecycle verbs ─────────────────────────────────────────────────────


def cmd_restart_actor(args: argparse.Namespace) -> int:
    result = _request("POST", f"/actors/{args.actor_id}/restart")
    print(
        f"actor.cognitiveos/{args.actor_id} restarted "
        f"(suspend={result.get('suspend', {}).get('action')}, resume={result.get('resume', {}).get('action')})"
    )
    return 0


def cmd_stop_actor(args: argparse.Namespace) -> int:
    result = _request(
        "POST",
        f"/actors/{args.actor_id}/lifecycle",
        json_body={
            "desired_state": "suspended",
            "reason": "cogctl stop",
        },
    )
    print(f"actor.cognitiveos/{args.actor_id} stopped (action={result.get('action_taken')})")
    return 0


def cmd_delete_actor(args: argparse.Namespace) -> int:
    result = _request(
        "POST",
        f"/actors/{args.actor_id}/lifecycle",
        json_body={
            "desired_state": "terminated",
            "reason": "cogctl delete",
        },
    )
    print(f"actor.cognitiveos/{args.actor_id} deleted (action={result.get('action_taken')})")
    return 0


# ── output helpers ──────────────────────────────────────────────────────


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_table(header: tuple[str, ...], rows: list[tuple[str, ...]], widths: list[int]) -> None:
    def fmt(cols: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cols, widths))

    print(fmt(header))
    for row in rows:
        print(fmt(row))
    if not rows:
        print("(no actors found)")


# ── argument parsing ─────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cogctl", description="CognitiveOS declarative control CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_apply = sub.add_parser("apply", help="Apply a declarative ActorSpecification (create or update)")
    p_apply.add_argument("-f", "--file", required=True, help="Path to a YAML/JSON ActorSpecification")
    p_apply.set_defaults(func=cmd_apply)

    p_create = sub.add_parser("create", help="Imperatively create a resource")
    create_sub = p_create.add_subparsers(dest="resource", required=True)
    p_create_actor = create_sub.add_parser("actor")
    p_create_actor.add_argument("--name", required=True)
    p_create_actor.add_argument("--node-class", default="")
    p_create_actor.add_argument("--artifact-version", default="")
    p_create_actor.add_argument("--claim-node", default="")
    p_create_actor.add_argument("--region", default="")
    p_create_actor.add_argument("--capacity", type=int, default=1)
    p_create_actor.add_argument("--tenant-id", default="default")
    p_create_actor.add_argument("--goal", action="append", default=[])
    p_create_actor.add_argument("--objective", default="")
    p_create_actor.add_argument("--required-capability", action="append", default=[])
    p_create_actor.set_defaults(func=cmd_create_actor)

    p_get = sub.add_parser("get", help="List resources")
    get_sub = p_get.add_subparsers(dest="resource", required=True)
    p_get_actors = get_sub.add_parser("actors")
    p_get_actors.add_argument("-o", "--output", choices=["table", "json"], default="table")
    p_get_actors.set_defaults(func=cmd_get_actors)

    p_describe = sub.add_parser("describe", help="Describe one resource")
    describe_sub = p_describe.add_subparsers(dest="resource", required=True)
    p_describe_actor = describe_sub.add_parser("actor")
    p_describe_actor.add_argument("actor_id")
    p_describe_actor.add_argument("-o", "--output", choices=["text", "json"], default="text")
    p_describe_actor.set_defaults(func=cmd_describe_actor)

    p_logs = sub.add_parser("logs", help="Show an Actor's lifecycle event history")
    logs_sub = p_logs.add_subparsers(dest="resource", required=True)
    p_logs_actor = logs_sub.add_parser("actor")
    p_logs_actor.add_argument("actor_id")
    p_logs_actor.set_defaults(func=cmd_logs_actor)

    p_restart = sub.add_parser("restart", help="Restart (suspend then resume) an Actor")
    restart_sub = p_restart.add_subparsers(dest="resource", required=True)
    p_restart_actor = restart_sub.add_parser("actor")
    p_restart_actor.add_argument("actor_id")
    p_restart_actor.set_defaults(func=cmd_restart_actor)

    p_stop = sub.add_parser("stop", help="Set an Actor's desired state to SUSPENDED")
    stop_sub = p_stop.add_subparsers(dest="resource", required=True)
    p_stop_actor = stop_sub.add_parser("actor")
    p_stop_actor.add_argument("actor_id")
    p_stop_actor.set_defaults(func=cmd_stop_actor)

    p_delete = sub.add_parser("delete", help="Set an Actor's desired state to TERMINATED")
    delete_sub = p_delete.add_subparsers(dest="resource", required=True)
    p_delete_actor = delete_sub.add_parser("actor")
    p_delete_actor.add_argument("actor_id")
    p_delete_actor.set_defaults(func=cmd_delete_actor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CogctlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
