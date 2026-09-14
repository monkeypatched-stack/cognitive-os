"""Base class for all Broca ETASS agents — extends Agent for runtime/policy/observer inheritance."""

from __future__ import annotations
import asyncio
import logging
from typing import Any

logger = logging.getLogger("broca.agents")

try:
    from src.monkey_brain.kernel.execute.runtime.outcome import AgentResult, Artifact
except ImportError:
    AgentResult = None  # type: ignore
    Artifact = None  # type: ignore

try:
    from src.broca.agent import Agent
except ImportError:
    try:
        from broca.agent import Agent
    except ImportError:
        Agent = object  # type: ignore


class BaseETASSAgent(Agent):
    """Base for ETASS step agents.

    Auto-registers every concrete subclass into _AUTOREGISTER_CLASSES via __init_subclass__.
    BrocaAgentRegistry._bootstrap() instantiates them on first discover()/list_agents() call.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        agent_type = getattr(cls, "agent_type", "base")
        if (
            agent_type not in ("base", "")
            and not getattr(cls, "__abstractmethods__", None)
            and not getattr(cls, "_no_autoregister", False)
        ):
            try:
                from broca.registry import _AUTOREGISTER_CLASSES

                if cls not in _AUTOREGISTER_CLASSES:
                    _AUTOREGISTER_CLASSES.append(cls)
            except ImportError:
                pass

    agent_type: str = "base"
    description: str = "Base ETASS agent"
    _last_reward: float = 0.5

    def __init__(
        self,
        runtime=None,
        policy=None,
        observer=None,
        learning=None,
        lemon=None,
    ) -> None:
        if runtime is None:
            runtime = self._app_runtime()
        # Agent.__init__ is only called when Agent is the real class (not object fallback)
        if Agent is not object:
            super().__init__(
                runtime=runtime,
                policy=policy,
                observer=observer,
                learning=learning,
                lemon=lemon,
            )
        else:
            self._runtime = runtime
            self._policy = policy
            self._observer = observer
            self._learning = learning
            self._lemon = lemon

    # ------------------------------------------------------------------
    # Capability discovery (uses inherited self._runtime)
    # ------------------------------------------------------------------

    def _find_capability(self, cap_name: str) -> Any | None:
        if self._runtime is None:
            self._runtime = self._app_runtime()
        if self._runtime is None:
            return None
        return getattr(self._runtime, "_capabilities", {}).get(cap_name)

    @staticmethod
    def _app_runtime() -> Any | None:
        # Try the Broca registry's stored runtime reference first (no app coupling)
        try:
            from broca.registry import get_registry

            reg = get_registry()
            stored = getattr(reg, "_runtime", None)
            if stored is not None:
                return stored
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Reward tracking and AgentResult construction
    # ------------------------------------------------------------------

    def feedback(self) -> float:
        return self._last_reward

    def _reward(self, success: bool | float, partial: float = 0.5) -> float:
        r = 1.0 if (success is True or success == 1.0) else (float(success) if isinstance(success, float) else partial)
        self._last_reward = r
        self._last_success = bool(success is True or (isinstance(success, float) and success >= 1.0))
        return r

    def _result(
        self,
        payload: dict | None = None,
        artifacts: list | None = None,
        metrics: dict | None = None,
        observations: list[str] | None = None,
        evidence: list | None = None,
    ):
        """Build a typed AgentResult from the last _reward() call."""
        if AgentResult is None:
            # Fallback if execution_outcome not importable — return plain dict
            return {**(payload or {}), "reward": self._last_reward}
        return AgentResult(
            reward=self._last_reward,
            payload=payload or {},
            artifacts=artifacts or [],
            metrics=metrics or {},
            observations=observations or [],
            evidence=evidence or [],
            success=getattr(self, "_last_success", self._last_reward >= 0.5),
        )

    async def _run(self, context: dict, _impl):
        """Execute the agent's impl. Returns AgentResult. Feedback via ExecutionOutcome → Runtime.publish()."""
        return await _impl(context)

    async def _llm_from_spec(
        self,
        spec_name: str,
        goal_override: str = "",
        extra_evidence: list[str] | None = None,
        system_override: str = "",
        max_tokens: int = 512,
    ) -> str:
        """Compile the agent's YAML spec and call ModelBackend. Returns raw model text.

        spec_name: filename stem under etass/workloads/ (e.g. "governance_review")
        goal_override: replaces the spec's goal field if provided
        extra_evidence: appended to spec.evidence list before compilation
        system_override: if set, passed as system prompt to ModelBackend (instead of role from IR)
        """
        from pathlib import Path
        from etass.specification import ETASSSpec
        from broca.agents.prompt_compiler import PromptCompilerAgent
        from src.monkey_brain.kernel.execute.provider.model_backend import get_backend

        spec_path = Path(__file__).parents[4] / "etass" / "workloads" / f"{spec_name}.yaml"
        if spec_path.exists():
            spec = ETASSSpec.from_yaml(spec_path)
        else:
            spec = ETASSSpec(workload=spec_name, goal=goal_override or spec_name)

        if goal_override:
            spec.goal = goal_override
        if extra_evidence:
            spec.evidence = list(spec.evidence) + extra_evidence

        compiler = PromptCompilerAgent()
        result = await compiler.handle({"spec": spec})
        ir = result.payload["prompt_ir"]

        system = system_override or ir.role
        backend = get_backend()
        # ModelBackend.complete() is fully synchronous (plain httpx.post /
        # SDK calls, no async client) — calling it directly here would block
        # the *entire* event loop for the whole LLM round-trip, freezing
        # every other in-flight request (including SDLC status polling)
        # until it returns. Running it in a thread keeps the loop free.
        return await asyncio.to_thread(backend.complete, ir.compiled_prompt, system=system, max_tokens=max_tokens)
