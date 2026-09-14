"""ClientCapabilityAgent — chart-driven agent that wraps an API client capability.

Loaded from a somatic capability chart (values.yaml produced by ClientCharterAgent).
At runtime it:
  1. Tries _find_capability("<service>-client") from the Runtime capability registry.
  2. Falls back to direct httpx calls using the chart's endpoint + auth + operations.

Instantiated by 'monkeypatched make create-agent <service>'; registered dynamically
into the Broca registry so it is discoverable for the rest of the process lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.client_capability_agent")


class ClientCapabilityAgent(BaseETASSAgent):
    """Chart-driven agent wrapping an API client capability.

    agent_type and description are dynamically derived from the chart so each
    registered instance is uniquely addressable (e.g. "todo-client-agent").
    """

    # Requires positional args at construction — cannot be pre-instantiated by _bootstrap().
    _no_autoregister: bool = True

    # These are overridden per-instance in __init__; the class-level values
    # are fallbacks so the base class's introspection doesn't break.
    agent_type: str = "client_capability_agent"
    description: str = "Chart-driven API client capability agent"

    def __init__(self, service_slug: str, chart_path: Path, runtime=None):
        super().__init__(runtime=runtime)
        self.service_slug = service_slug
        self.chart_path = Path(chart_path)

        # Per-instance type so multiple services can coexist in the registry
        self.agent_type = f"{service_slug}-client-agent"
        self.description = f"Chart-driven agent wrapping {service_slug} API client capability"

        self._operations: dict[str, dict] = {}
        self._base_url = "http://localhost:8090"
        self._auth_type = "bearer"
        self._auth_header = "Authorization"
        self._auth_template = "Bearer {token}"
        self._timeout = 30
        self._max_retries = 3
        self._retry_on: list[int] = [429, 500, 502, 503]

        self._load_chart()

    # ── Chart loading ─────────────────────────────────────────────────────────

    def _load_chart(self) -> None:
        if not self.chart_path.exists():
            logger.warning("[%s] chart not found: %s", self.agent_type, self.chart_path)
            return
        try:
            import yaml

            values = yaml.safe_load(self.chart_path.read_text()) or {}
        except Exception as e:
            logger.error("[%s] failed to load chart: %s", self.agent_type, e)
            return

        endpoint = values.get("endpoint", {})
        self._base_url = endpoint.get("base_url", self._base_url).rstrip("/")
        self._timeout = endpoint.get("timeout_seconds", self._timeout)

        auth = values.get("auth", {})
        self._auth_type = auth.get("type", "bearer")
        self._auth_header = auth.get("header_name", "Authorization")
        self._auth_template = auth.get("format_template", "Bearer {token}")

        rl = values.get("rate_limit", {})
        self._max_retries = rl.get("max_retries", 3)
        self._retry_on = rl.get("retry_on_status", [429, 500, 502, 503])

        for op in values.get("operations", []):
            name = op.get("name", "")
            if name:
                self._operations[name] = op

        logger.info(
            "[%s] loaded %d operations from chart (%s)",
            self.agent_type,
            len(self._operations),
            self.chart_path.name,
        )

    # ── Agent entry point ─────────────────────────────────────────────────────

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        operation = context.get("operation") or context.get("action") or ""
        token = context.get("token") or os.environ.get(f"{self.service_slug.upper().replace('-', '_')}_API_TOKEN", "")

        # ── 1. Try registered ICapability first ───────────────────────────────
        cap = self._find_capability(f"{self.service_slug}-client")
        if cap:
            logger.info("[%s] delegating to registered capability", self.agent_type)
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                result = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                self._reward(True, 0.9)
                return self._result(
                    payload=result,
                    observations=[f"capability {self.service_slug}-client executed {operation}"],
                )
            except Exception as e:
                logger.warning(
                    "[%s] capability failed (%s) — falling back to HTTP",
                    self.agent_type,
                    e,
                )

        # ── 2. Fall back to direct HTTP via chart operations ──────────────────
        if not operation:
            self._reward(True, 0.7)
            return self._result(
                payload={"available_operations": list(self._operations.keys())},
                observations=["no operation specified — returning available operations"],
            )

        if operation not in self._operations:
            self._reward(False, 0.2)
            return self._result(
                payload={
                    "error": f"unknown operation: {operation}",
                    "available": list(self._operations.keys()),
                },
                observations=[f"operation '{operation}' not in chart"],
            )

        op_spec = self._operations[operation]
        result, ok = await self._http_call(op_spec, context, token)
        self._reward(ok, 0.8 if ok else 0.3)
        return self._result(
            payload=result,
            observations=[f"HTTP {op_spec.get('method', 'GET')} {op_spec.get('path', '')} → {'ok' if ok else 'error'}"],
        )

    # ── HTTP execution ────────────────────────────────────────────────────────

    async def _http_call(self, op_spec: dict, context: dict, token: str) -> tuple[dict, bool]:
        import httpx

        method = op_spec.get("method", "GET").upper()
        path = op_spec.get("path", "/")

        # Resolve path parameters from context
        try:
            path = path.format(**context)
        except KeyError:
            pass

        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}
        if op_spec.get("auth_required") and token:
            headers[self._auth_header] = self._auth_template.replace("{token}", token)

        body = context.get("body") or context.get("data") or None
        params = context.get("params") or context.get("query") or None

        attempt = 0
        last_err: dict = {}
        while attempt <= self._max_retries:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=float(self._timeout)) as client:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        json=body if method in ("POST", "PUT", "PATCH") else None,
                        params=params if method == "GET" else None,
                    )
                    if resp.status_code in self._retry_on and attempt <= self._max_retries:
                        wait = 2 ** (attempt - 1)
                        await asyncio.sleep(wait)
                        continue
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"text": resp.text}
                    if resp.is_success:
                        return {"status": resp.status_code, "data": data}, True
                    return {"status": resp.status_code, "error": data}, False
            except Exception as e:
                last_err = {"error": repr(e)}
                if attempt <= self._max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))

        return last_err, False

    # ── Introspection ─────────────────────────────────────────────────────────

    def operations(self) -> list[str]:
        return list(self._operations.keys())

    def spec(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "service": self.service_slug,
            "base_url": self._base_url,
            "auth_type": self._auth_type,
            "operations": self._operations,
        }
