"""ArchitectureDecisionAgent — produces an Architecture Decision Record (ADR)
before implementation begins.

Distinct from BoundedContextAgent/DomainAgent (ddd/*.py): those enforce
architectural boundaries at runtime, reactively, once a domain model already
exists. ArchitectureDecisionAgent runs earlier in the pipeline — given
requirements (from RequirementsAgent) and the existing architecture context,
it produces the decision itself: what's being decided, why, what alternatives
were considered, and what the consequences are. Writes the ADR to disk under
docs/adr/ so it's a durable, reviewable artifact rather than only living in
an LLM response.
"""

from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.architecture_decision")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


class ArchitectureDecisionAgent(BaseETASSAgent):
    agent_type = "architecture_decision"
    description = "Produces an Architecture Decision Record (ADR) from requirements before implementation begins"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("architecture_decision")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                if output.get("decision"):
                    self._reward(True)
                    return self._result(payload=output, observations=[output.get("decision", "")[:200]])
            except Exception as e:
                logger.warning("[architecture_decision] capability failed: %s — spec-driven LLM", e)

        return await self._spec_llm(context)

    async def _spec_llm(self, context: dict):
        requirements = context.get("requirements") or []
        ask = str(context.get("question") or context.get("ask") or "").strip()
        if not requirements and not ask:
            self._reward(False, 0.0)
            return self._result(
                payload={"decision": ""},
                observations=["no requirements/ask in context"],
            )

        req_text = "\n".join(f"- {r.get('statement', r)}" if isinstance(r, dict) else f"- {r}" for r in requirements)
        goal = (
            f"Produce an Architecture Decision Record for:\n\n{ask}\n\nRequirements:\n{req_text}\n\n"
            "Decide the architectural approach (components, boundaries, data flow), list "
            "alternatives considered and why they were rejected, and state the consequences "
            "(trade-offs, risks, what this decision constrains later)."
        )
        try:
            raw = await self._llm_from_spec(
                "architecture_decision",
                goal_override=goal,
                system_override=(
                    "You are an architecture-decision-record engine. Reply with ONE valid JSON "
                    "object and nothing else — no markdown code fences, no prose before or after "
                    "it, no trailing commas, no comments. Every string must use double quotes and "
                    "escape any double quotes inside it. Keep alternatives_considered to at most 3 "
                    "entries and consequences to at most 4, so the response fits well within the "
                    "token budget and is never truncated.\n\n"
                    "Exact schema:\n"
                    '{"title": "...", "status": "proposed", "context": "...", '
                    '"decision": "...", "alternatives_considered": [{"option": "...", "rejected_because": "..."}], '
                    '"consequences": ["..."]}'
                ),
                max_tokens=2048,
            )
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        except Exception as e:
            logger.warning("[architecture_decision] spec LLM failed: %s", e)
            self._reward(False, 0.2)
            return self._result(payload={"decision": "", "error": str(e)})

        decision = data.get("decision", "")
        adr_path = self._write_adr(data, context)
        self._reward(bool(decision), 0.3)

        artifacts = []
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact

            if adr_path:
                artifacts = [Artifact(kind="adr", name=data.get("title", "ADR"), uri=str(adr_path))]
        except ImportError:
            pass

        return self._result(
            payload={**data, "adr_path": str(adr_path) if adr_path else ""},
            artifacts=artifacts,
            observations=[decision[:200]] if decision else ["no decision produced"],
        )

    def _write_adr(self, data: dict, context: dict) -> Path | None:
        if not data.get("decision"):
            return None
        try:
            adr_dir = _REPO / "docs" / "adr"
            adr_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md"))
            next_num = (int(existing[-1].name[:4]) + 1) if existing else 1
            slug = re.sub(r"[^a-z0-9]+", "-", data.get("title", "decision").lower()).strip("-")[:60]
            path = adr_dir / f"{next_num:04d}-{slug or 'decision'}.md"

            alternatives = "\n".join(
                f"- **{a.get('option', '')}** — rejected because: {a.get('rejected_because', '')}"
                for a in data.get("alternatives_considered", [])
            )
            consequences = "\n".join(f"- {c}" for c in data.get("consequences", []))
            path.write_text(
                f"# {next_num:04d}. {data.get('title', 'Untitled decision')}\n\n"
                f"Status: {data.get('status', 'proposed')}\n"
                f"Date: {datetime.now(timezone.utc).date().isoformat()}\n\n"
                f"## Context\n\n{data.get('context', '')}\n\n"
                f"## Decision\n\n{data.get('decision', '')}\n\n"
                f"## Alternatives Considered\n\n{alternatives or '(none recorded)'}\n\n"
                f"## Consequences\n\n{consequences or '(none recorded)'}\n"
            )
            return path
        except Exception as e:
            logger.warning("[architecture_decision] failed to write ADR file: %s", e)
            return None
