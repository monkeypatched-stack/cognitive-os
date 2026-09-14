"""TestGenerationAgent — writes new tests from a spec or diff.

Distinct from UnitTestingAgent/TestServiceAgent (testing.py, test_service_agent.py):
those only *run* tests that already exist. This agent authors new test
content — given requirements or a diff, it generates pytest test functions
covering the stated acceptance criteria, and writes them to disk so
UnitTestingAgent/TestServiceAgent have something new to run afterward.
"""

from __future__ import annotations
import logging
import os
import re
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.test_generation")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _extract_code_block(raw: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    return (m.group(1) if m else raw).strip()


class TestGenerationAgent(BaseETASSAgent):
    agent_type = "test_authoring"
    description = "Writes new pytest tests from requirements/acceptance criteria or a diff, distinct from agents that only run existing tests"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("test_authoring")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                if output.get("test_code"):
                    self._reward(True)
                    return self._result(payload=output)
            except Exception as e:
                logger.warning("[test_authoring] capability failed: %s — spec-driven LLM", e)

        return await self._spec_llm(context)

    async def _spec_llm(self, context: dict):
        requirements = context.get("requirements") or []
        diff = str(context.get("diff") or "")
        service_slug = str(context.get("service_slug", "")).strip()

        if not requirements and not diff:
            self._reward(False, 0.0)
            return self._result(
                payload={"test_code": ""},
                observations=["no requirements/diff to generate tests from"],
            )

        req_text = "\n".join(
            (
                f"- {r.get('statement', r)} (acceptance: {', '.join(r.get('acceptance_criteria', []))})"
                if isinstance(r, dict)
                else f"- {r}"
            )
            for r in requirements
        )
        goal = (
            "Write pytest test functions covering these acceptance criteria"
            + (f":\n\n{req_text}\n" if req_text else "")
            + (f"\n\nFor this diff:\n{diff[:4000]}\n" if diff else "")
            + "\n\nReturn only valid Python test code (pytest-style test_* functions), no prose."
        )
        try:
            raw = await self._llm_from_spec(
                "test_authoring",
                goal_override=goal,
                system_override="Reply with a single Python code block containing pytest test functions. No prose outside the code block.",
                max_tokens=1024,
            )
            test_code = _extract_code_block(raw)
        except Exception as e:
            logger.warning("[test_authoring] spec LLM failed: %s", e)
            self._reward(False, 0.2)
            return self._result(payload={"test_code": "", "error": str(e)})

        if not test_code or "def test_" not in test_code:
            self._reward(False, 0.2)
            return self._result(
                payload={"test_code": test_code},
                observations=["generated content has no test_* functions"],
            )

        written_path = self._write_test_file(test_code, service_slug, context)
        self._reward(True, 0.7)

        artifacts = []
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact

            if written_path:
                artifacts = [Artifact(kind="test_file", name=written_path.name, uri=str(written_path))]
        except ImportError:
            pass

        return self._result(
            payload={
                "test_code": test_code,
                "written_path": str(written_path) if written_path else "",
            },
            artifacts=artifacts,
            observations=[f"{test_code.count('def test_')} test functions generated"],
        )

    def _write_test_file(self, test_code: str, service_slug: str, context: dict) -> Path | None:
        try:
            if service_slug:
                output_dir = Path(str(context.get("output_dir") or _REPO / "generated")) / service_slug / "tests"
            else:
                output_dir = _REPO / "tests" / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-z0-9_]+", "_", (service_slug or "generated").lower())
            path = output_dir / f"test_{name}_generated.py"
            path.write_text(test_code + "\n")
            return path
        except Exception as e:
            logger.warning("[test_authoring] failed to write test file: %s", e)
            return None
