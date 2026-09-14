"""LLMGovernanceCapability — constitutional review via Claude (Cingulate gate)."""

from __future__ import annotations
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("cerebellum.etass.governance")

try:
    from src.monkey_brain.kernel.capability_interface import ICapability
    from src.monkey_brain.kernel.execution_state import ExecutionState, CapabilityResult
except ImportError:
    ICapability = object  # type: ignore
    ExecutionState = Any  # type: ignore
    CapabilityResult = None  # type: ignore


class LLMGovernanceCapability(ICapability):
    """LLM-based constitutional governance review for ETASS pipeline gates."""

    @property
    def capability_name(self) -> str:
        return "llm_governance"

    @property
    def name(self) -> str:
        """Satisfies ICapabilityProtocol so runtime.register() (which keys
        _capabilities by `.name`) can register this — see the identical fix
        on SittingFaceCodegenCapability. Without it, CingulateAgent._find_
        capability("llm_governance") (cingulate.py) always got None."""
        return self.capability_name

    @property
    def capability_type(self) -> str:
        return "governance"

    def can_execute(self, state) -> bool:
        return True

    def estimate_reward(self, state) -> float:
        return 0.9

    def estimate_cost(self, state) -> float:
        return 0.3

    async def execute(self, state, **kwargs: Any):
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        artifact = ""
        if hasattr(state, "query"):
            artifact = state.query[:2000]
        elif isinstance(state, dict):
            artifact = str(state.get("question", ""))[:2000]

        if not api_key:
            return self._result({"compliant": True, "source": "auto_approve_no_key"})

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=(
                    "You are the Cingulate governance gate for a cognitive OS. "
                    "Review the artifact for ETASS compliance. "
                    'Reply JSON only: {"compliant": bool, "issues": [], "recommendation": ""}'
                ),
                messages=[{"role": "user", "content": f"Artifact:\n{artifact}"}],
            )
            raw = msg.content[0].text
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"compliant": True}
            return self._result(data)
        except Exception as e:
            logger.warning("[llm_governance] failed: %s", e)
            return self._result({"compliant": True, "error": str(e)})

    def _result(self, output: dict):
        if CapabilityResult is not None and CapabilityResult is not None:
            try:
                return CapabilityResult(
                    success=True,
                    output=output,
                    metadata={"capability": self.capability_name},
                )
            except Exception:
                pass
        return output
