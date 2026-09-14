"""MotorCortexAgent — code generation, returns typed AgentResult with file artifacts."""

from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.motor_cortex")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))
_GEN_DIR = Path(os.environ.get("MONKEYBRAIN_GEN_DIR", str(_REPO.parent / "generated" / _REPO.name)))


class MotorCortexAgent(BaseETASSAgent):
    agent_type = "codegen"
    description = "Code generation from SOMA charts via SittingFace (SomaticCompiler + CodeGenAgent)"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        if context.get("action") == "serve":
            # ServeAgent calls handle({"action": "serve", ...}) as a
            # best-effort NOTIFICATION after launching a generated service
            # (serve_agent.py's docstring: "notifies MotorCortexAgent") — it
            # was never actually checked here, so every serve silently
            # re-ran the full SomaticCompiler + CodeGenAgent pass (an LLM
            # regeneration of every existing chart's source) as an
            # unintended side effect, wrapped in a bare except that hid any
            # failure. Acknowledge it instead of re-triggering codegen.
            service = context.get("service", "")
            logger.info(
                "[motor_cortex] serve notification for %s (pid=%s) — no codegen triggered",
                service,
                context.get("pid", ""),
            )
            self._reward(True, 0.5)
            return self._result(payload={"acknowledged": True, "service": service})

        cap = self._find_capability("sittingface_codegen")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                files = output.get("files_generated", 0)
                self._reward(files > 0, 0.6)
                return self._result(
                    payload={"files_generated": files},
                    metrics={
                        "files_generated": float(files),
                        "charts_processed": float(output.get("charts_processed", 0)),
                    },
                )
            except Exception as e:
                logger.warning("[motor_cortex] capability failed: %s — direct SittingFace", e)

        return await self._sittingface(context)

    async def _sittingface(self, context: dict):
        src = str(_REPO / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        try:
            from sittingface.somatic_compiler import SomaticCompiler
            from sittingface.codegen_agent import CodeGenAgent

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
            all_files = [f for r in reports for f in r.files_written]
            self._reward(len(all_files) > 0, 0.5)

            try:
                from src.monkey_brain.kernel.execute.runtime.outcome import Artifact

                artifacts = [Artifact(kind="file", name=str(f), uri=str(f)) for f in all_files]
            except ImportError:
                artifacts = []

            return self._result(
                payload={"files_generated": len(all_files), "charts": len(reports)},
                artifacts=artifacts,
                metrics={
                    "files_generated": float(len(all_files)),
                    "charts_processed": float(len(reports)),
                },
            )
        except Exception as e:
            logger.error("[motor_cortex] SittingFace failed: %s", e)
            self._reward(False, 0.0)
            return self._result(payload={"error": str(e)})
