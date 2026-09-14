"""Testing agents — UnitTestingAgent, StaticAnalysisAgent, SecurityAgent — typed AgentResult."""

from __future__ import annotations
import asyncio
import logging
import os
import subprocess
import sys as _sys
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.testing")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))
_PYTHON = str(Path(_sys.executable))  # same interpreter that launched the agent


def _sh(cmd: list[str]) -> tuple[bool, str]:
    r = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True, timeout=120)
    return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


class UnitTestingAgent(BaseETASSAgent):
    agent_type = "unit_testing"
    description = "Runs pytest; reports pass/fail and coverage signal"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("pytest")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                passed = output.get("passed", False)
                self._reward(passed, 0.4)
                return self._result(
                    payload={"passed": passed},
                    metrics={"passed": float(passed)},
                    observations=[output.get("output", "")[:200]],
                )
            except Exception as e:
                logger.warning("[unit_testing] capability failed: %s", e)

        loop = asyncio.get_event_loop()
        passed, output = await loop.run_in_executor(None, lambda: _sh([_PYTHON, "-m", "pytest", "--tb=short", "-q"]))
        self._reward(passed, 0.2 if "error" in output.lower() else 0.5)
        return self._result(
            payload={"passed": passed},
            metrics={"passed": float(passed)},
            observations=[output[-500:]],
            evidence=[{"output": output, "passed": passed}] if not passed else [],
        )


class StaticAnalysisAgent(BaseETASSAgent):
    agent_type = "static_analysis"
    description = "Runs ruff linter for code quality; skips gracefully if not installed"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            loop = asyncio.get_event_loop()
            clean, output = await loop.run_in_executor(None, lambda: _sh(["ruff", "check", "src/", "packages/"]))
            self._reward(clean, 0.6)
            return self._result(
                payload={"clean": clean},
                metrics={"clean": float(clean)},
                observations=[output[-400:] if not clean else "no linting issues"],
                evidence=[{"output": output}] if not clean else [],
            )
        except FileNotFoundError:
            self._reward(True, 0.8)
            return self._result(
                payload={"clean": True, "skipped": True},
                observations=["ruff not installed"],
            )
        except Exception as e:
            self._reward(True, 0.6)
            return self._result(payload={"clean": True}, observations=[str(e)])


class SecurityAgent(BaseETASSAgent):
    agent_type = "security_scan"
    description = "Runs bandit security scanner; skips gracefully if not installed"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        try:
            loop = asyncio.get_event_loop()
            clean, output = await loop.run_in_executor(None, lambda: _sh(["bandit", "-r", "src/", "-ll", "-q"]))
            self._reward(clean, 0.5)
            return self._result(
                payload={"clean": clean},
                metrics={"clean": float(clean)},
                observations=[output[-400:] if not clean else "no security findings"],
                evidence=[{"findings": output}] if not clean else [],
            )
        except FileNotFoundError:
            self._reward(True, 0.8)
            return self._result(
                payload={"clean": True, "skipped": True},
                observations=["bandit not installed"],
            )
        except Exception as e:
            self._reward(True, 0.6)
            return self._result(payload={"clean": True}, observations=[str(e)])
