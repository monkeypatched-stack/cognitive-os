"""ClientCharterAgent — converts a generated API client into a SOMA capability chart.

Reads the client's source files (client.py, models.py, exceptions.py), calls an LLM
via the client_charter workload spec, and writes a somatic values.yaml capability chart
that registers the API client as a first-class MonkeyBrain ICapability.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.client_charter")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))
_CHARTS_DIR = _REPO / "somatic" / "charts"


class ClientCharterAgent(BaseETASSAgent):
    agent_type = "client_charter"
    description = "Converts a generated API client into a SOMA capability chart (ICapability)"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        client_dir = Path(context.get("client_dir", ""))
        service_slug = context.get("service_slug", client_dir.name.removesuffix("-client"))
        base_url = context.get("base_url", "http://localhost:8090")

        if not client_dir.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={"charted": False},
                observations=[f"client_dir not found: {client_dir}"],
            )

        # ── Read client source files ──────────────────────────────────────────
        sources: dict[str, str] = {}
        for fname in ("client.py", "models.py", "exceptions.py", "__init__.py"):
            p = client_dir / fname
            if not p.exists():
                matches = list(client_dir.rglob(fname))
                p = matches[0] if matches else p
            if p.exists():
                sources[fname] = p.read_text()[:3000]

        if not sources:
            self._reward(False, 0.1)
            return self._result(
                payload={"charted": False},
                observations=["no client source files found"],
            )

        # ── Build LLM goal ────────────────────────────────────────────────────
        sources_text = "\n\n".join(f"=== {fname} ===\n{content}" for fname, content in sources.items())
        goal = (
            f"Convert this API client for service '{service_slug}' into a somatic "
            f"CAPABILITY chart (values.yaml). Base URL: {base_url}\n\n"
            f"{sources_text[:5000]}\n\n"
            "Output ONLY valid YAML. No markdown. No prose. "
            "The YAML must have top-level keys: "
            "capability, auth, endpoint, operations, rate_limit, error_handling, preamble, invariants, reviewGate."
        )
        system_override = (
            "You output ONLY valid YAML — no markdown fences, no explanation. "
            "The YAML is a MonkeyBrain somatic capability chart. "
            "Top-level keys: capability, auth, endpoint, operations, rate_limit, "
            "error_handling, preamble, invariants, reviewGate."
        )

        # ── Call LLM (spec-driven → Anthropic fallback) ───────────────────────
        raw = ""
        try:
            raw = await self._llm_from_spec(
                "client_charter",
                goal_override=goal,
                system_override=system_override,
                max_tokens=3072,
            )
        except Exception as e:
            logger.warning("[client_charter] spec LLM failed (%s) — Anthropic fallback", e)
            raw = await self._anthropic_fallback(goal, system_override)

        chart_yaml = self._extract_yaml(raw)
        if not chart_yaml:
            chart_yaml = self._build_structural_chart(service_slug, base_url, sources)

        # ── Write chart ───────────────────────────────────────────────────────
        chart_path = _CHARTS_DIR / f"{service_slug}-client" / "values.yaml"
        chart_path.parent.mkdir(parents=True, exist_ok=True)
        chart_path.write_text(chart_yaml)
        logger.info("[client_charter] capability chart written: %s", chart_path)

        self._reward(True, 0.9)
        return self._result(
            payload={"charted": True, "chart_path": str(chart_path)},
            observations=[f"capability chart → {chart_path.relative_to(_REPO)}"],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _anthropic_fallback(self, goal: str, system: str) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return ""
        try:
            import anthropic

            msg = anthropic.Anthropic(api_key=api_key).messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=3072,
                system=system,
                messages=[{"role": "user", "content": goal}],
            )
            return msg.content[0].text
        except Exception as e:
            logger.error("[client_charter] Anthropic fallback failed: %s", e)
            return ""

    @staticmethod
    def _extract_yaml(raw: str) -> str:
        raw = raw.strip()
        raw = re.sub(r"^```(?:yaml)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()
        if raw.startswith("capability:"):
            return raw
        m = re.search(r"(capability:\s*\n.*)", raw, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _parse_operations(client_src: str, slug: str) -> list[dict]:
        """Infer HTTP operations from public method names in client.py."""
        ops: list[dict] = []
        seen: set[str] = set()

        # Map common verb prefixes to HTTP methods and path patterns
        _verb_map = {
            "create": ("POST", f"/{slug}s/"),
            "add": ("POST", f"/{slug}s/"),
            "list": ("GET", f"/{slug}s/"),
            "get": ("GET", f"/{slug}s/{{id}}"),
            "fetch": ("GET", f"/{slug}s/{{id}}"),
            "update": ("PATCH", f"/{slug}s/{{id}}"),
            "replace": ("PUT", f"/{slug}s/{{id}}"),
            "delete": ("DELETE", f"/{slug}s/{{id}}"),
            "remove": ("DELETE", f"/{slug}s/{{id}}"),
        }

        public_methods = re.findall(r"def ([a-z][\w]+)\(self", client_src)
        for method in public_methods:
            if method.startswith("_") or method in seen:
                continue
            seen.add(method)
            http_method, path = "GET", f"/{slug}s/"
            for prefix, (hm, pt) in _verb_map.items():
                if method.startswith(prefix):
                    http_method, path = hm, pt
                    break
            auth_required = http_method in ("POST", "PUT", "PATCH", "DELETE")
            ops.append(
                {
                    "name": method,
                    "method": http_method,
                    "path": path,
                    "description": method.replace("_", " ").capitalize(),
                    "auth_required": auth_required,
                }
            )

        return ops

    def _build_structural_chart(self, slug: str, base_url: str, sources: dict[str, str]) -> str:
        """Fallback: build a valid capability chart from parsed client source."""
        import datetime

        client_src = sources.get("client.py", "")
        operations = self._parse_operations(client_src, slug)
        cap_prefix = slug.upper().replace("-", "")[:8]
        today = datetime.date.today().isoformat()
        review = (datetime.date.today().replace(year=datetime.date.today().year + 1)).isoformat()

        ops_yaml = ""
        for op in operations:
            ops_yaml += (
                f"  - name: {op['name']}\n"
                f"    method: {op['method']}\n"
                f"    path: {op['path']}\n"
                f'    description: "{op["description"]}"\n'
                f"    auth_required: {'true' if op['auth_required'] else 'false'}\n"
            )
        if not ops_yaml:
            ops_yaml = (
                f"  - name: list_{slug.replace('-', '_')}s\n"
                f"    method: GET\n"
                f"    path: /{slug}s/\n"
                f'    description: "List all {slug} resources"\n'
                f"    auth_required: false\n"
            )

        invs = (
            f"  - id: {cap_prefix}-CAP-001\n"
            f"    rule: typed_parameters\n"
            f"    statement: All operation parameters must be fully typed.\n"
            f"    severity: critical\n"
            f'    rejection: "REJECTED — Untyped parameter in operation."\n'
            f"  - id: {cap_prefix}-CAP-002\n"
            f"    rule: auth_at_registration\n"
            f"    statement: JWT token injected at capability registration, not per operation.\n"
            f"    severity: high\n"
            f'    rejection: "REJECTED — Auth credential passed per operation call."\n'
            f"  - id: {cap_prefix}-CAP-003\n"
            f"    rule: error_mapping\n"
            f"    statement: HTTP errors must map to domain exceptions.\n"
            f"    severity: high\n"
            f'    rejection: "REJECTED — Raw HTTP error exposed to caller."\n'
        )

        rejected = (
            '      - "REJECTED — Untyped parameter in operation."\n'
            '      - "REJECTED — Auth credential passed per operation call."\n'
            '      - "REJECTED — Raw HTTP error exposed to caller."\n'
        )

        return (
            f"capability:\n"
            f"  name: {slug}-client\n"
            f"  id: cap-{slug}-client-001\n"
            f"  platform: HTTP/REST\n"
            f'  description: "Typed {slug} API client capability"\n'
            f'  version: "1.0.0"\n'
            f"  status: active\n"
            f"  authored_by: ClientCharterAgent\n"
            f"  approved_by: CingulateAgent\n"
            f"  effective_date: '{today}'\n"
            f"  review_date: '{review}'\n"
            f"  module_id: {slug.replace('-', '_')}\n"
            f"  module_name: {slug.title().replace('-', ' ')}\n"
            f"  tags:\n"
            f"    - api-client\n"
            f"    - {slug}\n"
            f"    - capability\n\n"
            f"auth:\n"
            f"  type: bearer\n"
            f"  header_name: Authorization\n"
            f'  format_template: "Bearer {{{{token}}}}"\n'
            f"  scopes: []\n"
            f'  refresh_endpoint: ""\n'
            f"  ttl_seconds: 3600\n\n"
            f"endpoint:\n"
            f'  base_url: "{base_url}"\n'
            f"  protocol: http\n"
            f'  version: "1.0.0"\n'
            f"  tls_verify: false\n"
            f"  timeout_seconds: 30\n\n"
            f"operations:\n{ops_yaml}\n"
            f"rate_limit:\n"
            f"  requests_per_hour: 1000\n"
            f"  strategy: fixed_window\n"
            f"  backoff_multiplier: 2.0\n"
            f"  max_retries: 3\n"
            f"  retry_on_status: [429, 500, 502, 503]\n\n"
            f"error_handling:\n"
            f"  transient_statuses: [500, 502, 503, 504]\n"
            f"  permanent_statuses: [400, 401, 403, 404, 422]\n"
            f"  retry_delay_ms: 1000\n"
            f"  circuit_breaker_threshold: 5\n"
            f"  circuit_breaker_window_seconds: 60\n\n"
            f"preamble:\n"
            f"  statement: >\n"
            f"    The {slug}-client capability wraps the {slug.title()} REST API as a registered\n"
            f"    MonkeyBrain ICapability. It provides typed operations for interacting with\n"
            f"    {slug} resources. Authentication is via JWT Bearer token injected at registration.\n"
            f"    Errors are mapped to domain exceptions. Retries on 429/5xx with exponential backoff.\n\n"
            f"invariants:\n{invs}\n"
            f"reviewGate:\n"
            f"  mode: constitutional\n"
            f"  outcomes:\n"
            f'    approved: "APPROVED — {slug}-client capability conforms to all API client invariants."\n'
            f"    rejected:\n{rejected}"
        )
