"""SittingFaceCodegenCapability — SomaticCompiler + CodeGenAgent as an ICapability."""

from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("cerebellum.etass.codegen")

try:
    from src.monkey_brain.kernel.capability_interface import ICapability
    from src.monkey_brain.kernel.execution_state import ExecutionState, CapabilityResult
except ImportError:
    ICapability = object  # type: ignore
    ExecutionState = Any  # type: ignore
    CapabilityResult = None  # type: ignore

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[6])))
_GEN_DIR = Path(os.environ.get("MONKEYBRAIN_GEN_DIR", str(_REPO.parent / "generated" / _REPO.name)))


class SittingFaceCodegenCapability(ICapability):
    """Runs the SittingFace code generation pipeline (SomaticCompiler → CodeGenAgent)."""

    @property
    def capability_name(self) -> str:
        return "sittingface_codegen"

    @property
    def name(self) -> str:
        """Satisfies ICapabilityProtocol (src.shared.runtime_protocols) so
        cerebellum.providers.load_all_providers can register this via
        runtime.register(), which keys _capabilities by `.name` — the
        broca dispatch's _find_capability(cap_name) then looks this up by
        the same string. Without this, register() would raise AttributeError
        looking for `.name` on an object that only had `.capability_name`."""
        return self.capability_name

    @property
    def capability_type(self) -> str:
        return "codegen"

    def can_execute(self, state) -> bool:
        return True

    def estimate_reward(self, state) -> float:
        return 0.85

    def estimate_cost(self, state) -> float:
        return 0.7

    async def execute(self, state, **kwargs: Any):
        src = str(_REPO / "src")
        if src not in sys.path:
            sys.path.insert(0, src)

        try:
            from sittingface.somatic_compiler import SomaticCompiler
            from sittingface.codegen_agent import CodeGenAgent
        except ImportError as e:
            logger.error("[codegen] import failed: %s", e)
            return self._result({"files_generated": 0, "error": str(e)})

        try:
            compiler = SomaticCompiler()
            prompts = compiler.compile_prompts()
            agent = CodeGenAgent(output_dir=_GEN_DIR)
            prompt_dicts = [
                {
                    "chart": p.chart_name,
                    "preamble": p.preamble,
                    "steps": p.cot_steps,
                    "constraints": p.constraints,
                }
                for p in prompts
            ]
            loop = asyncio.get_event_loop()
            reports = await loop.run_in_executor(None, agent.run_all, prompt_dicts)
            total = sum(len(r.files_written) for r in reports)
            return self._result({"files_generated": total, "charts_processed": len(reports)})
        except Exception as e:
            logger.error("[codegen] execution failed: %s", e)
            return self._result({"files_generated": 0, "error": str(e)})

    def _result(self, output: dict):
        if CapabilityResult is not None:
            try:
                return CapabilityResult(
                    success=output.get("files_generated", 0) >= 0,
                    output=output,
                    metadata={"capability": self.capability_name},
                )
            except Exception:
                pass
        return output
