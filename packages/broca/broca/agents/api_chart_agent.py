"""ApiChartAgent — compiles a service DDD API chart into a somatic .prompt.md.

Replaces the inline SomaticCompiler calls in soma_api. Reads from the
sittingface.api_chart catalogue, renders the values.yaml, and compiles it.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.api_chart_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class ApiChartAgent(BaseETASSAgent):
    agent_type = "api_chart"
    description = "Compiles a service DDD API somatic chart into a .prompt.md"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        compiled_dir = Path(str(context.get("compiled_dir", _REPO / "somatic" / "compiled")))
        save_chart: bool = bool(context.get("save_chart", True))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"compiled": False}, observations=["no service_slug"])

        _ensure_sittingface()
        try:
            from sittingface.api_chart import service_domain, render_api_chart
            from sittingface.somatic_compiler import SomaticCompiler
        except ImportError as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"compiled": False, "error": repr(e)},
                observations=[f"sittingface import failed: {e}"],
            )

        domain = service_domain(service_slug)
        chart_yaml = render_api_chart(service_slug, domain)

        if save_chart:
            chart_dir = _REPO / "somatic" / "charts" / f"{service_slug}-api"
            chart_dir.mkdir(parents=True, exist_ok=True)
            values_path = chart_dir / "values.yaml"
            values_path.write_text(chart_yaml)
            source_dir = chart_dir
        else:
            tmp = tempfile.mkdtemp(prefix=f"soma-{service_slug}-")
            source_dir = Path(tmp)
            (source_dir / "values.yaml").write_text(chart_yaml)

        compiler = SomaticCompiler()
        compiler.load_path(source_dir)
        compiler.compile_prompts()
        compiled_dir.mkdir(parents=True, exist_ok=True)
        written = compiler.write_prompts(compiled_dir)

        prompt_path = written[0] if written else None
        logger.info("[api_chart] compiled: %s", prompt_path)

        self._reward(bool(prompt_path), 0.9 if prompt_path else 0.3)
        return self._result(
            payload={
                "compiled": bool(prompt_path),
                "chart_path": str(source_dir / "values.yaml"),
                "prompt_path": str(prompt_path) if prompt_path else "",
                "service_slug": service_slug,
                "aggregate": domain.get("aggregate", ""),
            },
            observations=[f"api chart compiled → {Path(prompt_path).name if prompt_path else 'none'}"],
        )
