"""CompileAgentAgent — compiles a client capability chart into an executable .prompt.md.

Reads somatic/charts/<service>-client/values.yaml (produced by ClientCharterAgent),
converts operations to CoT steps, and writes the compiled prompt.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.compile_agent_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class CompileAgentAgent(BaseETASSAgent):
    agent_type = "compile_agent"
    description = "Compiles a client capability chart (values.yaml) into an executable .prompt.md"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        output_dir = Path(str(context.get("output_dir", _REPO / "somatic" / "compiled")))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"compiled": False}, observations=["no service_slug"])

        chart_path = _REPO / "somatic" / "charts" / f"{service_slug}-client" / "values.yaml"
        if not chart_path.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={"compiled": False, "error": f"chart not found: {chart_path}"},
                observations=[f"run chart-from-client {service_slug} first"],
            )

        _ensure_sittingface()
        try:
            import yaml as _yaml
            from sittingface.somatic_compiler import SomaticCompiler, CompiledPrompt
        except ImportError as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"compiled": False, "error": repr(e)},
                observations=[f"somatic_compiler import failed: {e}"],
            )

        compiler = SomaticCompiler()
        charts = compiler.load_path(chart_path)
        if not charts:
            self._reward(False, 0.1)
            return self._result(
                payload={
                    "compiled": False,
                    "error": f"could not load chart: {chart_path}",
                },
                observations=["SomaticCompiler returned no charts"],
            )

        chart = charts[0]
        values = chart.values

        operations: list[dict] = values.get("operations", [])
        cot_steps = [
            {
                "step": i + 1,
                "title": op.get("name", f"op_{i + 1}"),
                "description": (
                    f"{op.get('method', 'GET')} {op.get('path', '')} — "
                    f"{op.get('description', op.get('name', ''))}. "
                    f"Auth required: {op.get('auth_required', False)}."
                ),
            }
            for i, op in enumerate(operations)
        ]
        cot_steps += values.get("cot", {}).get("steps", [])

        endpoint = values.get("endpoint", {})
        auth = values.get("auth", {})
        cap_meta = values.get("capability", {})

        compiled = CompiledPrompt(
            chart_name=chart.name,
            preamble=values.get("preamble", {}).get("statement", ""),
            cot_steps=cot_steps,
            review_gate=values.get("reviewGate", {}),
            constraints=[inv.get("id", "") for inv in values.get("invariants", [])],
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{service_slug}-client-cap"
        prompt_path = output_dir / f"{stem}.prompt.md"

        front: dict = {}
        if compiled.constraints:
            front["constraints"] = compiled.constraints
        if compiled.review_gate:
            front["review_gate"] = compiled.review_gate

        lines: list[str] = []
        if front:
            lines += [
                "---",
                _yaml.dump(front, default_flow_style=False).rstrip(),
                "---",
                "",
            ]
        lines += [f"# {chart.name} capability", ""]

        if cap_meta or endpoint or auth:
            lines += ["## Capability Metadata", ""]
            if cap_meta.get("description"):
                lines += [cap_meta["description"].strip(), ""]
            if endpoint.get("base_url"):
                lines += [f"**Base URL:** `{endpoint['base_url']}`"]
            if auth.get("type"):
                lines += [f"**Auth:** {auth['type']} — {auth.get('format_template', '')}"]
            lines.append("")

        if compiled.preamble:
            lines += ["## Preamble", "", compiled.preamble.strip(), ""]

        if compiled.cot_steps:
            lines += ["## Operations", ""]
            for i, step in enumerate(compiled.cot_steps, 1):
                title = step.get("title") or step.get("name") or f"Step {i}"
                desc = step.get("description") or ""
                lines.append(f"### {i}. {title}")
                if desc:
                    lines += ["", desc.strip()]
                lines.append("")

        prompt_path.write_text("\n".join(lines))
        logger.info("[compile_agent] wrote %s", prompt_path)

        self._reward(True, 0.9)
        return self._result(
            payload={
                "compiled": True,
                "service_slug": service_slug,
                "chart_path": str(chart_path.relative_to(_REPO)),
                "prompt_path": str(prompt_path.relative_to(_REPO)),
                "operations": len(operations),
                "constraints": compiled.constraints,
            },
            observations=[f"capability prompt → {prompt_path.name} ({len(operations)} operations)"],
        )
