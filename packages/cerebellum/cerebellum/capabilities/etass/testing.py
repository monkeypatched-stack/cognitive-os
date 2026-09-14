"""PytestCapability — subprocess pytest runner as an ICapability."""

from __future__ import annotations
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("cerebellum.etass.testing")

try:
    from src.monkey_brain.kernel.capability_interface import ICapability
    from src.monkey_brain.kernel.execution_state import ExecutionState, CapabilityResult
except ImportError:
    ICapability = object  # type: ignore
    ExecutionState = Any  # type: ignore
    CapabilityResult = None  # type: ignore

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[6])))


class PytestCapability(ICapability):
    """Runs pytest and returns pass/fail status."""

    @property
    def capability_name(self) -> str:
        return "pytest"

    @property
    def name(self) -> str:
        """Satisfies ICapabilityProtocol so runtime.register() can register
        this — see the identical fix on SittingFaceCodegenCapability. Without
        it, UnitTestingAgent._find_capability("pytest") always got None."""
        return self.capability_name

    @property
    def capability_type(self) -> str:
        return "testing"

    def can_execute(self, state) -> bool:
        return True

    def estimate_reward(self, state) -> float:
        return 0.8

    def estimate_cost(self, state) -> float:
        return 0.4

    async def execute(self, state, **kwargs: Any):
        loop = asyncio.get_event_loop()
        success, output = await loop.run_in_executor(None, self._run_pytest)
        result = {"passed": success, "output": output[-1500:]}
        return self._wrap(result)

    def _run_pytest(self) -> tuple[bool, str]:
        r = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-q"],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0, (r.stdout + r.stderr)

    def _wrap(self, output: dict):
        if CapabilityResult is not None:
            try:
                return CapabilityResult(
                    success=output["passed"],
                    output=output,
                    metadata={"capability": self.capability_name},
                )
            except Exception:
                pass
        return output
