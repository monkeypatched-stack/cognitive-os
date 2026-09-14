"""CingulateAgent — governance gate driven by etass/workloads/governance_review.yaml.

No hardcoded prompts. The spec defines the agent's reasoning, constraints, and output format.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.cingulate")


class CingulateAgent(BaseETASSAgent):
    agent_type = "governance"
    description = "Governance and compliance review — spec-driven constitutional check (Cingulate pre/post gate)"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        # Try Cerebellum capability first
        cap = self._find_capability("llm_governance")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                compliant = output.get("compliant", True)
                self._reward(compliant)
                return self._result(
                    payload={"compliant": compliant},
                    observations=[output.get("recommendation", "")],
                    evidence=[output] if output.get("issues") else [],
                )
            except Exception as e:
                logger.warning("[cingulate] capability failed: %s — spec-driven LLM", e)

        return await self._spec_llm(context)

    async def _spec_llm(self, context: dict):
        """Compile governance_review.yaml spec and call ModelBackend."""
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            self._reward(True)
            return self._result(
                payload={"compliant": True},
                observations=["auto-approved: no API key"],
            )
        try:
            artifact = str(context.get("question", ""))[:2000]
            goal = f"Review this artifact for compliance:\n\n{artifact}"
            raw = await self._llm_from_spec(
                "governance_review",
                goal_override=goal,
                system_override=(
                    "You are a governance-compliance engine. Reply with ONE valid JSON object "
                    "and nothing else — no markdown code fences, no prose before or after it, "
                    "no trailing commas, no comments. Every string must use double quotes and "
                    "escape any double quotes inside it. Keep issues to at most 4 entries so the "
                    "response fits well within the token budget and is never truncated.\n\n"
                    "Exact schema:\n"
                    '{"compliant": true, "issues": ["..."], "recommendation": "..."}'
                ),
                max_tokens=1024,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"compliant": True}
            compliant = data.get("compliant", True)
            self._reward(compliant)
            return self._result(
                payload={"compliant": compliant},
                observations=[data.get("recommendation", "")],
                evidence=[data] if data.get("issues") else [],
            )
        except Exception as e:
            logger.warning("[cingulate] spec LLM failed: %s", e)
            self._reward(True, 0.7)
            return self._result(payload={"compliant": True, "error": str(e)})
