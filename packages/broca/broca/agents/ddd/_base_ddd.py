"""BaseDDDAgent — DDD constructs as executable cognitive agents.

Pipeline per agent call:
  1. perceive(context)          — extract signals
  2. _compile_spec(perception)  — compile workload YAML → StructuredPromptIR
                                   → ModelBackend.complete()
                                   enriches perception with model_guidance
  3. reason(enriched_perception) — structural decision using model guidance
  4. act(decision)              — produce payload
  5. learn(outcome)             — optional state update

Step 2 is best-effort: if no model backend is configured or compilation fails,
perception["model_guidance"] is empty and reason() falls back to pure structural
logic. This preserves correctness in offline environments.

Read-only advisory agents:
  Set readonly = True on any agent that must never mutate the codebase or system
  state (Architecture, Security, Compliance, Safety, Coding Standards families).
  _impl() enforces this by blocking any act() return whose "action" field starts
  with a mutation verb. Advisory actions are whitelisted; everything else raises
  ReadOnlyAgentViolation before the payload ever leaves the agent.
"""

from __future__ import annotations

import inspect
import logging
import os
from abc import abstractmethod
from typing import Any

from broca.agents._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.ddd")

_ADVISORY_ACTIONS: frozenset[str] = frozenset(
    {
        "compliant",
        "non_compliant",
        "report",
        "flag",
        "warn",
        "recommend",
        "observe",
        "audit",
        "approve",
        "approved",
        "reject",
        "rejected",
        "escalate",
        "acknowledge",
    }
)

_MUTATION_PREFIXES: tuple[str, ...] = (
    "write",
    "delete",
    "update",
    "create",
    "deploy",
    "execute",
    "insert",
    "patch",
    "put",
    "post",
    "remove",
    "drop",
    "modify",
    "replace",
    "truncate",
    "commit",
    "push",
    "merge",
    "rollback",
)


class ReadOnlyAgentViolation(RuntimeError):
    """Raised when a readonly agent's act() returns a mutating action."""


# Keys that carry no result — only bookkeeping about the call itself.
_BOOKKEEPING_KEYS: frozenset[str] = frozenset(
    {
        "action",
        "success",
        "operation",
        "layer",
        "decision",
        "agent",
        "agent_type",
        "unimplemented",
        "error",
        "status",
    }
)


def is_status_echo(payload: Any) -> bool:
    """True when act() returned a status and no result.

    233 of the registered agents are structural stubs:

        class LineAgent(BaseDDDAgent):
            def act(self, decision):
                return {"action": f"line.{decision['operation']}", "success": True}

    They read nothing, do nothing, and report success unconditionally. Every step backed by
    one came back `complete`, which is how a request to build a todo agent finished green
    having built nothing, and how a graph of four nodes reported four successes while two of
    them were a robotics stub that had merely been handed the word "task".

    A payload is an echo when, after the bookkeeping keys above, nothing substantive is left —
    including the ones that only *look* like output, e.g. SchedulingAgent's {"schedule": {}}.
    An agent that genuinely did something has something to show for it: rows, a summary, an
    artifact, a source. This is deliberately a check on the RESULT rather than a flag on the
    class, because a flag can be set once and then quietly rot as the code changes; a stub that
    starts returning real data stops being treated as a stub the moment it does.
    """
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in _BOOKKEEPING_KEYS:
            continue
        # An empty container is not a result. 0 and False are.
        if value or value == 0 or value is False:
            return False
    return True


class BaseDDDAgent(BaseETASSAgent):
    """Base for all DDD cognitive agents.

    Class attributes to set on subclasses:
      agent_type   (str)  — registry key
      ddd_layer    (str)  — DDD construct name, e.g. "aggregate"
      workload_spec (str) — stem of the YAML in etass/workloads/ used for reasoning
      description  (str)  — shown to LLMExplorer
      readonly     (bool) — True for advisory-only families (compliance, security, etc.)
                            act() is blocked from returning mutation actions when True.

    Override:
      perceive(context) → dict    — extract signals from raw context
      reason(perception) → dict   — structural decision; receives model_guidance if available
      act(decision) → dict        — produce the payload (REQUIRED)
      learn(outcome) → None       — optional
    """

    ddd_layer: str = "generic"
    workload_spec: str = "source_control"
    readonly: bool = False  # subclasses set True to enforce advisory-only contract

    # ------------------------------------------------------------------
    # Cognitive loop
    # ------------------------------------------------------------------

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"layer": self.ddd_layer, "context": context}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"layer": self.ddd_layer, "decision": "structural"}

    @abstractmethod
    def act(self, decision: dict[str, Any]) -> dict[str, Any]: ...

    def learn(self, outcome: dict[str, Any]) -> None:
        pass

    def _guard_readonly(self, outcome: dict[str, Any]) -> None:
        """Raise ReadOnlyAgentViolation if a readonly agent tries to mutate state."""
        if not self.readonly:
            return
        action = str(outcome.get("action", "")).lower().strip()
        if not action:
            raise ReadOnlyAgentViolation(f"[{self.agent_type}] readonly agent returned outcome with no 'action' field")
        if action in _ADVISORY_ACTIONS:
            return
        if action.startswith(_MUTATION_PREFIXES):
            raise ReadOnlyAgentViolation(
                f"[{self.agent_type}] readonly agent returned mutating action "
                f"'{outcome.get('action')}' — only advisory actions are permitted: "
                f"{sorted(_ADVISORY_ACTIONS)}"
            )
        # deny-by-default: unknown actions are treated as mutations
        raise ReadOnlyAgentViolation(
            f"[{self.agent_type}] readonly agent returned unknown action "
            f"'{outcome.get('action')}' — add it to _ADVISORY_ACTIONS or _MUTATION_PREFIXES"
        )

    def _build_goal(self, context: dict[str, Any]) -> str:
        question = context.get("question", context.get("goal", ""))
        if question:
            return f"As a {self.ddd_layer} DDD agent, reason about: {question}"
        return f"As a {self.ddd_layer} DDD agent, evaluate the provided context and return a structured decision."

    # ------------------------------------------------------------------
    # Spec compilation — compile workload YAML → model output
    # ------------------------------------------------------------------

    async def _compile_spec(self, context: dict[str, Any]) -> str:
        """Compile the agent's workload spec and call ModelBackend.

        Returns model output string, or "" if no backend is configured.
        """
        has_backend = any(
            os.environ.get(k)
            for k in (
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "GOOGLE_API_KEY",
                "DASHSCOPE_API_KEY",
            )
        )
        if not has_backend:
            return ""
        try:
            return await self._llm_from_spec(
                self.workload_spec,
                goal_override=self._build_goal(context),
                extra_evidence=[
                    f"ddd_layer: {self.ddd_layer}",
                    f"agent: {self.agent_type}",
                ],
                max_tokens=256,
            )
        except Exception as exc:
            logger.debug("[%s] spec compile failed: %s", self.agent_type, exc)
            return ""

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _impl(self, context: dict[str, Any]):
        try:
            perception = self.perceive(context)

            # Step 2: compile spec → enrich perception with model guidance
            model_guidance = await self._compile_spec(context)
            if model_guidance:
                perception["model_guidance"] = model_guidance

            # Step 3: reason — support both sync and async subclass overrides
            if inspect.iscoroutinefunction(self.reason):
                decision = await self.reason(perception)
            else:
                decision = self.reason(perception)

            # Step 4: act
            outcome = self.act(decision)

            # Enforce readonly contract before the payload leaves the agent
            self._guard_readonly(outcome)

            # Step 5: learn
            self.learn(outcome)

            # An agent that returned a status and no result did not do the work. Reporting
            # success here — which is what happened for every one of the 233 stubs — makes a
            # node that touched nothing read `complete`, and makes a build that built nothing
            # read green. `unimplemented` is the honest word for it, and the runtime already
            # knows what to do with it.
            if is_status_echo(outcome):
                logger.debug(
                    "[%s] returned a status with no result — reporting unimplemented",
                    self.agent_type,
                )
                outcome = {
                    **outcome,
                    "success": False,
                    "unimplemented": True,
                    "error": (
                        f"{self.agent_type} is not implemented: it returned a status "
                        f"({outcome.get('action', 'executed')}) but produced no result"
                    ),
                }
                self._reward(False)
                return self._result(
                    payload=outcome,
                    observations=[f"{self.agent_type}: unimplemented — no result produced"],
                )

            self._reward(True)
            return self._result(
                payload=outcome,
                observations=[
                    f"{self.agent_type}: {outcome.get('action', 'executed')}",
                    f"spec: {self.workload_spec}" + (" → model guidance available" if model_guidance else ""),
                ],
            )
        except ReadOnlyAgentViolation:
            raise
        except Exception as exc:
            logger.error("[%s] cognitive cycle failed: %s", self.agent_type, exc)
            self._reward(False)
            return self._result(
                payload={"error": str(exc)},
                observations=[f"{self.agent_type}: failed — {exc}"],
            )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)
