"""RequirementsAgent — turns a raw stakeholder ask into a structured,
traceable requirements artifact.

Distinct from PlannerAgent (planner.py): PlannerAgent produces an execution
plan (a YAML sequence of monkeypatched make steps) — it assumes requirements
are already understood. RequirementsAgent runs *before* that, producing the
requirements themselves: functional requirements, non-functional constraints,
and acceptance criteria a later ArchitectureDecisionAgent/PlannerAgent can
consume, and a CodeReviewAgent can later check an implementation against.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.requirements")


class RequirementsAgent(BaseETASSAgent):
    agent_type = "requirements"
    description = "Turns a raw stakeholder ask into a structured, traceable requirements artifact"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("requirements")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                if output.get("requirements"):
                    self._reward(True)
                    return self._result(
                        payload=output,
                        observations=[f"{len(output['requirements'])} requirements captured"],
                    )
            except Exception as e:
                logger.warning("[requirements] capability failed: %s — spec-driven LLM", e)

        return await self._spec_llm(context)

    async def _spec_llm(self, context: dict):
        ask = str(context.get("question") or context.get("ask") or "").strip()
        if not ask:
            self._reward(False, 0.0)
            return self._result(
                payload={"requirements": []},
                observations=["no ask/question in context"],
            )

        goal = (
            f"Analyze this stakeholder ask and produce a structured requirements artifact:\n\n{ask}\n\n"
            "Break it into discrete functional requirements, non-functional constraints "
            "(performance, security, compliance), and testable acceptance criteria."
        )
        try:
            raw = await self._llm_from_spec(
                "requirements_authoring",
                goal_override=goal,
                system_override=(
                    "You are a requirements-authoring engine. Reply with ONE valid JSON object "
                    "and nothing else — no markdown code fences, no prose before or after it, "
                    "no trailing commas, no comments. Every string must use double quotes and "
                    "escape any double quotes inside it. Keep it to at most 6 requirements total "
                    "so the response fits well within the token budget and is never truncated.\n\n"
                    "Exact schema:\n"
                    '{"requirements": [{"id": "R1", "statement": "...", '
                    '"type": "functional", "acceptance_criteria": ["..."]}], '
                    '"open_questions": ["..."]}\n\n'
                    '"type" must be exactly "functional" or "non_functional".'
                ),
                max_tokens=2048,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"requirements": []}
        except Exception as e:
            logger.warning("[requirements] spec LLM failed: %s", e)
            self._reward(False, 0.2)
            return self._result(payload={"requirements": [], "error": str(e)})

        reqs = data.get("requirements", [])
        self._reward(bool(reqs), 0.3)
        return self._result(
            payload={
                "requirements": reqs,
                "open_questions": data.get("open_questions", []),
            },
            observations=[f"{len(reqs)} requirements captured"]
            + [f"open: {q}" for q in data.get("open_questions", [])],
        )
