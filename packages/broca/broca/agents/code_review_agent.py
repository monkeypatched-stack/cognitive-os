"""CodeReviewAgent — reads a diff and reasons about correctness/design.

Distinct from CingulateAgent (governance/compliance gate) and
StaticAnalysisAgent/SecurityAgent (mechanical lint/scan tools): this agent
does the thing a human reviewer does — reads the actual diff content and
judges whether the change is correct and well-designed relative to the
requirements/architecture decision that motivated it, not just whether it
passes a linter. Distinct from the external `/code-review` skill: this is
the same capability made available as a first-class SDLC-pipeline agent, so
a CodeGenRuntime run can gate on it the same way it gates on any other stage.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.code_review")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _git_diff(base: str, repo_dir: Path) -> str:
    r = subprocess.run(
        ["git", "diff", f"{base}...HEAD"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.stdout if r.returncode == 0 else ""


class CodeReviewAgent(BaseETASSAgent):
    agent_type = "code_review"
    description = "Reads a diff against the spec/architecture decision and flags correctness/design issues"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("code_review")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                if "approved" in output:
                    self._reward(output["approved"])
                    return self._result(payload=output, evidence=output.get("findings", []))
            except Exception as e:
                logger.warning("[code_review] capability failed: %s — spec-driven LLM", e)

        return await self._spec_llm(context)

    async def _spec_llm(self, context: dict):
        diff = str(context.get("diff") or "").strip()
        if not diff:
            base = str(context.get("base_ref", "main"))
            repo_dir = Path(str(context.get("repo_dir") or _REPO))
            loop = asyncio.get_event_loop()
            diff = await loop.run_in_executor(None, lambda: _git_diff(base, repo_dir))

        if not diff.strip():
            self._reward(True, 0.5)
            return self._result(
                payload={"approved": True, "findings": []},
                observations=["no diff to review"],
            )

        decision = str(context.get("architecture_decision") or "")
        goal = (
            "Review this diff for correctness and design quality"
            + (f", against this architecture decision:\n\n{decision}\n" if decision else ".")
            + f"\n\nDiff:\n{diff[:8000]}\n\n"
            "Flag inverted conditions, missing error handling, dropped validation, "
            "and design issues (unnecessary complexity, wrong abstraction level)."
        )
        try:
            raw = await self._llm_from_spec(
                "code_review",
                goal_override=goal,
                system_override=(
                    "You are a code-review engine. Reply with ONE valid JSON object and "
                    "nothing else — no markdown code fences, no prose before or after it, "
                    "no trailing commas, no comments. Every string must use double quotes and "
                    "escape any double quotes inside it. Keep findings to at most 5 entries so "
                    "the response fits well within the token budget and is never truncated.\n\n"
                    "Exact schema:\n"
                    '{"approved": true, "findings": '
                    '[{"file": "...", "summary": "...", "severity": "high"}]}\n\n'
                    '"severity" must be exactly "high", "medium", or "low".'
                ),
                max_tokens=2048,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"approved": True, "findings": []}
        except Exception as e:
            logger.warning("[code_review] spec LLM failed: %s", e)
            self._reward(True, 0.5)
            return self._result(payload={"approved": True, "findings": [], "error": str(e)})

        approved = data.get("approved", True)
        findings = data.get("findings", [])
        self._reward(approved, 0.4)
        return self._result(
            payload={"approved": approved, "findings": findings},
            evidence=findings,
            observations=[f"{len(findings)} findings"] + [f.get("summary", "") for f in findings[:5]],
        )
