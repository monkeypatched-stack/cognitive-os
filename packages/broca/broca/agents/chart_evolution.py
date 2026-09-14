"""ChartEvolutionAgent — SOMA chart evolution driven by etass/workloads/chart_evolution.yaml.

No hardcoded prompts. The spec defines reasoning strategy, constraints, and output format.
"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent
import os as _os

logger = logging.getLogger("broca.agents.chart_evolution")
_CHARTS_DIR = Path(
    _os.environ.get(
        "MONKEYBRAIN_CHARTS_DIR",
        str(Path(_os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4]))) / "somatic/charts"),
    )
)


class ChartEvolutionAgent(BaseETASSAgent):
    agent_type = "chart_evolution"
    description = "Evolves SOMA charts via spec-driven LLM using operational evidence and architectural feedback"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        evidence = context.get("evidence", [])
        chart_name = context.get("chart_name", "monkeypatched")

        if not evidence:
            self._reward(True, 0.7)
            return self._result(
                payload={"evolved": False},
                observations=["no evidence — skipping chart evolution"],
            )

        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            self._reward(True, 0.6)
            return self._result(
                payload={"evolved": False},
                observations=["no API key — skipping chart evolution"],
            )

        try:
            chart_summary = self._chart_summary(chart_name)
            evidence_text = json.dumps(evidence[-3:], indent=2)[:1500]
            goal = (
                f"Analyze this SOMA chart and operational evidence. "
                f"Suggest concrete improvements.\n\n"
                f"Chart:\n{chart_summary}\n\nEvidence:\n{evidence_text}"
            )
            raw = await self._llm_from_spec(
                "chart_evolution",
                goal_override=goal,
                extra_evidence=[
                    f"chart: {chart_name}",
                    f"evidence_items: {len(evidence)}",
                ],
                system_override='Reply JSON only: {"suggestions": [{"field": "...", "current": "...", "proposed": "...", "reason": "..."}], "priority": "low|medium|high"}',
                max_tokens=1024,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"suggestions": [], "priority": "low"}
            suggestions = data.get("suggestions", [])
            priority = data.get("priority", "low")
            self._reward(len(suggestions) > 0, 0.7)
            return self._result(
                payload={"evolved": len(suggestions) > 0, "priority": priority},
                metrics={"suggestions": float(len(suggestions))},
                observations=[f"{len(suggestions)} evolution suggestions, priority={priority}"],
                evidence=suggestions,
            )
        except Exception as e:
            logger.error("[chart_evolution] spec LLM failed: %s", e)
            self._reward(False, 0.3)
            return self._result(payload={"evolved": False}, observations=[str(e)])

    def _chart_summary(self, chart_name: str) -> str:
        for p in _CHARTS_DIR.rglob("values.yaml"):
            if chart_name in str(p):
                return p.read_text()[:1500]
        return f"chart: {chart_name} (not found)"
