"""IntrospectionAgent and OperationalEvidenceAgent — typed AgentResult with evidence."""

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.introspection")

_EVIDENCE_DIR = Path(
    os.environ.get(
        "MONKEYBRAIN_EVIDENCE_DIR",
        str(Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4]))) / ".operational_evidence"),
    )
)


class IntrospectionAgent(BaseETASSAgent):
    agent_type = "introspection"
    description = "Reads MonkeyBrain health metrics and runtime state for self-assessment"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("introspection")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                self._reward(True, 0.8)
                return self._result(
                    payload=output,
                    metrics={k: float(v) for k, v in output.items() if isinstance(v, (int, float))},
                )
            except Exception as e:
                logger.warning("[introspection] capability failed: %s", e)

        lemon = getattr(self, "_lemon", None)
        metrics_raw = lemon.export() if lemon and hasattr(lemon, "export") else {}
        self._reward(True, 0.9)
        return self._result(
            payload={"metrics": metrics_raw},
            metrics={k: float(v) for k, v in metrics_raw.items() if isinstance(v, (int, float))},
        )


class OperationalEvidenceAgent(BaseETASSAgent):
    agent_type = "operational_evidence"
    description = "Reads .operational_evidence/ logs for pipeline learning and drift detection"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        if not _EVIDENCE_DIR.exists():
            self._reward(True, 0.7)
            return self._result(
                payload={"count": 0},
                observations=["no operational evidence found"],
            )

        try:
            files = sorted(
                _EVIDENCE_DIR.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )[:10]
            evidence = []
            for f in files:
                try:
                    evidence.append(json.loads(f.read_text()))
                except Exception:
                    pass
            self._reward(True)
            return self._result(
                payload={"count": len(evidence)},
                metrics={"evidence_count": float(len(evidence))},
                evidence=evidence,
                observations=[f"loaded {len(evidence)} evidence records"],
            )
        except Exception as e:
            self._reward(False, 0.4)
            return self._result(payload={"count": 0}, observations=[str(e)])
