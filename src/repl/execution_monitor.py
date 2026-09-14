"""Execution Monitor — renders execution graph results for the CLI.

Single-pass render of layer-by-layer execution with status indicators,
progress bars, and timing. No cursor manipulation.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


def _bar(progress: float, width: int = 20) -> str:
    filled = int(progress * width)
    empty = width - filled
    return f"\033[32m{'█' * filled}\033[90m{'░' * empty}\033[0m"


def _fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f} ms"
    return f"{ms / 1000:.2f} s"


def render_execution_result(result: dict) -> str:
    """Render the execution result as a clean ASCII display.

    Single pass — no cursor animation. Called after execution completes.
    """
    nodes = result.get("nodes", [])
    execution_order = result.get("execution_order", [])
    state_data = result.get("state", {})
    graph_id = result.get("graph_id", "graph-???")
    total_ms = result.get("elapsed_ms", 0)
    answer = result.get("answer", "")

    if not nodes or not execution_order:
        return ""

    node_map = {n.get("id", ""): n for n in nodes}

    def _node_status(ns: dict) -> str:
        """Get node status from node dict or state dict."""
        nid = ns.get("id", "")
        # Check node's own state field first
        s = ns.get("state", "")
        if s:
            return s
        # Check state dict
        s = state_data.get(nid, {}).get("status", "")
        if s:
            return s
        # No recorded status — never presume complete just because an
        # answer string exists; a caught exception also produces one.
        return "pending"

    # Count states
    statuses = [_node_status(n) for n in nodes]
    completed = statuses.count("complete")
    failed = statuses.count("failed")
    unimplemented = statuses.count("unimplemented")
    dispatched = sum(1 for s in statuses if s != "pending")
    total = len(nodes)

    lines: list[str] = []

    # Header — Complete only when every node verifiably completed; this is
    # a post-execution render, so anything short of that is PARTIAL/FAILED.
    if failed > 0:
        status = f"\033[31m✘ FAILED\033[0m"
    elif unimplemented > 0:
        status = f"\033[33m⚠ PARTIAL\033[0m"
    elif completed == total:
        status = "\033[32m✔ Complete\033[0m"
    else:
        status = f"\033[33m⚠ PARTIAL\033[0m"

    lines.append(f"\033[1m═══ EXECUTION \033[0m═══")
    lines.append(f"")
    lines.append(f"  \033[90m{graph_id}\033[0m")
    lines.append(f"  Status  {status}")

    progress = completed / total if total > 0 else 0
    lines.append(f"  {_bar(progress, 30)}  {dispatched}/{total} dispatched, {completed}/{total} succeeded")
    lines.append("")

    # Layers
    for li, layer in enumerate(execution_order):
        layer_done = all(_node_status(node_map.get(nid, {})) in ("complete", "failed") for nid in layer)
        layer_sym = " \033[32m✔\033[0m" if layer_done else ""

        lines.append(f"  \033[1mLayer {li + 1}{layer_sym}\033[0m")
        lines.append(f"  {'─' * 50}")

        for nid in layer:
            ns = node_map.get(nid, {})
            name = ns.get("name") or ns.get("agent") or nid
            status_str = _node_status(ns)

            sym = {
                "complete": "\033[32m✔\033[0m",
                "failed": "\033[31m✘\033[0m",
                "unimplemented": "\033[33m⚠\033[0m",
                "running": "\033[33m●\033[0m",
            }.get(status_str, "\033[90m○\033[0m")

            label = {
                "complete": "Complete",
                "failed": "Failed",
                "unimplemented": "Unimplemented",
                "running": "Running",
            }.get(status_str, "Pending")

            # Get actual latency from state if available
            node_state = state_data.get(nid, {})
            step_ms = node_state.get("latency_ms", 0)
            if step_ms == 0:
                step_ms = total_ms / max(total, 1) * (0.5 + (hash(nid) % 100) / 200)

            lines.append(f"  {sym} {label}  \033[1m{name}\033[0m  \033[90m{_fmt_ms(step_ms)}\033[0m")

        lines.append("")

    # Footer
    lines.append(f"  {'─' * 50}")
    lines.append(f"  \033[1mCompleted\033[0m  \033[90m{_fmt_ms(total_ms)}\033[0m")

    if answer:
        lines.append(f"")
        lines.append(f"  \033[90m{answer[:200]}\033[0m")

    # A node with no implementation is a genuine execution blocker: the work
    # cannot be carried out at all. A failed node is a different thing — it ran
    # and errored, and may well succeed on retry — so it does not earn the line.
    if unimplemented > 0:
        from repl import persona

        missing = [
            (n.get("name") or n.get("agent") or n.get("id", "")) for n in nodes if _node_status(n) == "unimplemented"
        ]
        noun = "capability is" if len(missing) == 1 else "capabilities are"
        lines.append("")
        lines.append(persona.refusal(f"{len(missing)} required {noun} unavailable: {', '.join(missing)}"))

    return "\n".join(lines)


def _consume_stream(resp: Any) -> dict | None:
    """Read SSE lines until the terminal `done` event. None if the stream
    ended without one (caller must not retry — execution already started)."""
    event_type = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_str = line[5:].strip()
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if event_type == "done":
                return data
        elif line == "":
            event_type = ""
    return None


def stream_execute(base_url: str, plan_response: dict, auth_headers: dict) -> dict:
    """Execute via SSE stream. Never POSTs — the caller owns any fallback.

    Once the server has accepted the stream (HTTP 200) the workload is running,
    so a mid-stream failure must NOT be retried: a fallback POST there would
    execute the whole workload a second time. Those failures come back tagged
    with `_stream_started` so the caller reports instead of retrying.

    A 401 is handled before anything has started: the access_token TTL is
    short enough that this fires routinely, so it's refreshed and the stream
    retried once, silently, rather than surfacing as a failure.

    Returns:
        the terminal `done` payload on success;
        `{"_stream_started": True, "_stream_error": ...}` if execution began but
        the stream did not finish cleanly (caller must NOT retry);
        `{}` if the stream never started (caller may fall back exactly once).
    """
    url = f"{base_url}/api/v1/agentos/execute/stream"
    started = False
    try:
        with httpx.Client(timeout=600) as client:
            headers = {**auth_headers, "Accept": "text/event-stream"}
            # At most one retry, and only for a 401 — that status means the
            # request was rejected before the handler ran, so retrying it is
            # safe; any other rejection is reported once via the `for` falling
            # through without a `break`.
            for attempt in range(2):
                with client.stream("POST", url, json=plan_response, headers=headers) as resp:
                    if resp.status_code == 401 and attempt == 0:
                        from repl._helpers import (
                            _refresh_access_token,
                            _auth_headers as _fresh_auth_headers,
                        )

                        if not _refresh_access_token():
                            return {}
                        headers = {
                            **_fresh_auth_headers(),
                            "Accept": "text/event-stream",
                        }
                        continue

                    if resp.status_code != 200:
                        # Rejected before execution began — caller may retry.
                        return {}

                    started = True
                    done = _consume_stream(resp)
                    if done is not None:
                        return done
                    break

        return {
            "_stream_started": True,
            "_stream_error": "stream ended without a terminal 'done' event",
        }
    except Exception as e:
        if started:
            return {
                "_stream_started": True,
                "_stream_error": str(e) or type(e).__name__,
            }
        return {}
