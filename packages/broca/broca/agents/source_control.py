"""SourceControlAgent — git operations, returns typed AgentResult with commit artifact."""

from __future__ import annotations
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.source_control")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


class SourceControlAgent(BaseETASSAgent):
    agent_type = "source_control"
    description = "Git commit, branch, tag via subprocess — creates ETASS feature branches"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        operation = context.get("operation", "commit")
        chart_name = context.get("chart_name", "etass")
        version = context.get("chart_version", "0.1.0")
        branch = f"etass/{chart_name}/{version}"
        loop = asyncio.get_event_loop()

        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact
        except ImportError:
            Artifact = None

        if operation == "commit":
            await loop.run_in_executor(None, lambda: self._sh(["git", "add", "src/", "somatic/"]))
            status = await loop.run_in_executor(None, lambda: self._sh(["git", "status", "--short"]))
            if not status.strip():
                self._reward(True, 0.8)
                return self._result(
                    payload={"status": "nothing_to_commit", "branch": branch},
                    observations=["nothing to commit — working tree clean"],
                )
            msg = context.get("commit_message", f"feat: ETASS-generated {chart_name} v{version}")
            await loop.run_in_executor(None, lambda: self._sh(["git", "commit", "-m", msg]))
            self._reward(True)
            artifacts = [Artifact(kind="commit", name=msg, uri=branch)] if Artifact else []
            return self._result(
                payload={"status": "committed", "branch": branch, "message": msg},
                artifacts=artifacts,
                observations=[f"committed: {msg}"],
            )

        elif operation == "branch":
            await loop.run_in_executor(None, lambda: self._sh(["git", "checkout", "-b", branch]))
            self._reward(True)
            artifacts = [Artifact(kind="branch", name=branch)] if Artifact else []
            return self._result(
                payload={"status": "branched", "branch": branch},
                artifacts=artifacts,
                observations=[f"created branch: {branch}"],
            )

        self._reward(True, 0.6)
        return self._result(payload={"status": "noop", "operation": operation})

    def _sh(self, cmd: list[str]) -> str:
        r = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr
