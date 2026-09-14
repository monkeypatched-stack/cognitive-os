"""AgentResolver — centralizes agent resolution across all sources.

Resolution hierarchy:
    1. Cache (fastest)
    2. Local Broca registry (fuzzy match PascalCase → snake_case)
    3. Provider Registry (OpenClaw, n8n, NANDA, ARD) — active discovery
    4. n8n auto-create — create a workflow for the agent on-the-fly
    5. CodeGen (last resort)

The resolver returns an ExecutableAgent that can be called via execute().
"""

from __future__ import annotations

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.agent_resolver")

# A fuzzy match on a very short key means nothing — "d" is a substring of most agent types.
# Below this length, only an exact match is honoured.
_MIN_FUZZY_CORE = 4

AUTOCREATE_ENV = "MB_AGENT_AUTOCREATE"


def _autocreate_enabled() -> bool:
    """Whether an unresolved agent may be MANUFACTURED on the spot. Off by default.

    When a planner-invented name resolved to nothing, the resolver asked an LLM to synthesize
    an n8n workflow for it, activated it, called it, and counted the 200 it got back as
    success. The system was inventing the implementation, running it, and then grading itself
    on the result. Executing "build a simple todo agent" therefore reported success with four
    of four nodes complete, and what it had actually built was:

        return [{json: {result: 'Todo received!'}}];

    An echo. It also wrote a new workflow into the operator's live n8n on every run, for every
    name the planner happened to invent that time.

    Off by default: an agent that does not exist should be reported as not existing (CodeGen
    advisory, success=False), which is the truth, rather than conjured into a shape that
    returns 200. Set MB_AGENT_AUTOCREATE=1 to allow it — worth having, but it is a deliberate
    act, not something that should happen silently behind a question.
    """
    return os.getenv(AUTOCREATE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _snake_case(agent_name: str) -> str:
    """PascalCase → snake_case. 'LineResolverAgent' → 'line_resolver_agent'."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", agent_name).lower()
    return re.sub(r"[^a-z0-9]+", "_", snake).strip("_")


def _strip_agent_suffix(snake: str) -> str:
    """Drop a trailing '_agent'. 'line_resolver_agent' → 'line_resolver'.

    This used to be `snake.rstrip('_agent')`, which strips trailing CHARACTERS from the set
    {_,a,g,e,n,t} rather than the suffix — so 'storage_agent' became 'stor', 'message_agent'
    became 'mess', and 'data_agent' became 'd'. A one-character key then went into the
    substring match below and hit 65 of the 311 registered types, and the resolver bound the
    first: DataAgent silently resolved to `nanda`. Wrong agent, no error.
    """
    return snake[: -len("_agent")] if snake.endswith("_agent") else snake


def _fuzzy_agent_type(core: str, registered: list[str], agent_name: str = "") -> str | None:
    """Best registered agent_type for `core`, or None when there is no unambiguous answer.

    Ambiguity is refused rather than guessed. Binding the wrong agent produces a confident,
    wrong result that looks like a success; returning None falls through to the provider /
    SDLC / CodeGen path, which is honest about not knowing.
    """
    if not core or len(core) < _MIN_FUZZY_CORE:
        return None

    tokens = set(core.split("_"))

    # Tier 1: same tokens, any order ('due_calibration' ~ 'calibration_due').
    exact_tokens = [t for t in registered if set(t.split("_")) == tokens]
    if len(exact_tokens) == 1:
        return exact_tokens[0]

    # Tier 2: a registered type that covers EVERY token of the key, plus qualifiers.
    #         'storage' → 'cognitive_storage' is a safe widening: the registered agent does
    #         everything the key asked for. The reverse direction is not safe — it would let
    #         the generic `line` agent absorb a request for `line_resolver`, silently dropping
    #         the part of the request it cannot do. Whole tokens only, so a bare substring
    #         like 'd' in 'nanda' can never match.
    candidates = [t for t in registered if tokens.issubset(set(t.split("_")))]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        logger.warning(
            "[agent_resolver] %r is ambiguous — matches %d registered agents (%s). Refusing to "
            "guess; falling through to provider/SDLC/CodeGen.",
            agent_name or core,
            len(candidates),
            ", ".join(sorted(candidates)[:5]),
        )
    return None


@dataclass
class AgentResult:
    """Standardised result envelope from agent execution."""

    agent_name: str
    success: bool
    produced: dict[str, Any] = field(default_factory=dict)
    events_emitted: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    feedback: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    reward: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""


class ExecutableAgent(ABC):
    """Interface for anything that can be executed."""

    @abstractmethod
    async def execute(self, state: dict) -> AgentResult:
        """Execute the agent with the given state."""
        ...


class BrocaAgent(ExecutableAgent):
    """Wrapper for a locally registered Broca agent."""

    def __init__(self, agent: Any, agent_name: str):
        self._agent = agent
        self._agent_name = agent_name
        # The registry key actually matched (e.g. "appointment"), which can
        # differ from the requested agent_name (e.g. "AppointmentSlotAgent")
        # under fuzzy/substring matching — this is the identity that should
        # be recorded as the real Capability Graph provider, not the
        # caller's one-off name.
        self.resolved_type: str = getattr(agent, "agent_type", agent_name)

    async def execute(self, state: dict) -> AgentResult:
        t0 = time.monotonic()
        try:
            raw = await self._agent.handle(state)
            elapsed = (time.monotonic() - t0) * 1000
            if isinstance(raw, AgentResult):
                return raw

            # Every BaseETASSAgent returns kernel.execute.runtime.outcome.AgentResult —
            # a DIFFERENT class from this module's AgentResult, so the isinstance above
            # never matched it and it fell through to the str() branch below: the agent's
            # structured payload was stringified into {"value": "AgentResult(reward=1.0,
            # payload={...})"} and success was hardcoded True. That is why every Broca step
            # reported "[ok]" with an empty action and why no agent could ever contribute
            # evidence — its sources were flattened into a string before anything read them.
            payload = getattr(raw, "payload", None)
            if isinstance(payload, dict):
                # An agent's OWN verdict wins. BaseDDDAgent._impl calls _reward(True) and
                # _result() never sets success, so the envelope says success=True whenever
                # act() merely returned without raising — even for an agent reporting
                # {"success": False, "error": "no line matching line 3 exists"}. The envelope
                # means "the agent ran"; the payload means "the agent got an answer". Only the
                # second is what a caller asked about.
                success = bool(payload.get("success", getattr(raw, "success", True)))
                return AgentResult(
                    agent_name=self._agent_name,
                    success=success,
                    produced=payload,
                    events_emitted=list(getattr(raw, "observations", []) or []),
                    latency_ms=elapsed,
                    feedback=float(getattr(raw, "reward", 0.5)),
                )

            if isinstance(raw, dict):
                return AgentResult(
                    agent_name=self._agent_name,
                    success=raw.get("success", True),
                    produced=raw.get("payload", raw),
                    latency_ms=elapsed,
                )

            logger.warning(
                "[agent_resolver] %s returned an unrecognised result type %s — no payload to read",
                self._agent_name,
                type(raw).__name__,
            )
            return AgentResult(
                agent_name=self._agent_name,
                success=False,
                produced={"error": f"unrecognised agent result type {type(raw).__name__}"},
                latency_ms=elapsed,
            )
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            return AgentResult(
                agent_name=self._agent_name,
                success=False,
                produced={"error": str(e)},
                latency_ms=elapsed,
            )


class ProviderAgent(ExecutableAgent):
    """Wrapper for an agent discovered via a Provider."""

    def __init__(self, provider_name: str, agent_name: str):
        self._provider_name = provider_name
        self._agent_name = agent_name

    async def execute(self, state: dict) -> AgentResult:
        t0 = time.monotonic()
        try:
            from src.monkey_brain.kernel.provider_registry import init_providers

            registry = init_providers()
            result = await registry.execute_agent(self._agent_name, state)
            elapsed = (time.monotonic() - t0) * 1000
            return AgentResult(
                agent_name=self._agent_name,
                success=result.get("success", False),
                produced=result,
                latency_ms=elapsed,
                metadata={"provider": self._provider_name},
            )
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            return AgentResult(
                agent_name=self._agent_name,
                success=False,
                produced={"error": str(e)},
                latency_ms=elapsed,
            )


class CodeGenAgent(ExecutableAgent):
    """Generates a new agent on-the-fly when no existing agent matches.

    Only called as last resort when all other resolution methods fail.
    Uses LLM to generate agent code, then executes it.
    """

    def __init__(self, agent_name: str, resolution_diagnostics: dict | None = None):
        self._agent_name = agent_name
        # Why Broca/Provider resolution didn't find anything — see
        # AgentResolver.resolve(). Carried into AgentResult.metadata so the
        # caller can tell "genuinely nothing exists" from "couldn't confirm
        # because a provider was unreachable."
        self._resolution_diagnostics = dict(resolution_diagnostics or {})

    async def execute(self, state: dict) -> AgentResult:
        t0 = time.monotonic()
        question = state.get("question", "")

        try:
            # Use the graph generator's LLM to generate a plan for this agent
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()

            if generator._llm is None:
                return AgentResult(
                    agent_name=self._agent_name,
                    success=False,
                    produced={"error": "No LLM available for code generation"},
                    latency_ms=(time.monotonic() - t0) * 1000,
                )

            # Load knowledge packs for grounded data, scoped to the caller identity
            from src.monkey_brain.runtime.agent_middleware import _identity_from_state

            knowledge_context = self._load_knowledge_context(question, identity=_identity_from_state(state))

            # Last-resort fallback: no real implementation backs this agent, so it
            # cannot execute anything. It may still answer the question in an
            # advisory capacity — from the knowledge context, or from general
            # knowledge — provided it never claims to have acted or to have read
            # live system data. The caller marks the result non-executable.
            prompt = f"""You are {self._agent_name}, an advisory analyst.

Question: {question}

No system capability exists to carry this out, so you cannot perform it. You can
still answer the question itself, informatively and concisely.

Rules:
- Never claim an action was performed, scheduled, booked, created, or completed.
- Never invent system records, IDs, timestamps, or query results.
- Prefer the knowledge context below. Where you rely on general knowledge instead,
  say so, and set "grounded" to false.
- Answer in a calm, precise, matter-of-fact register. No apologies, no filler.

Return JSON:
  {{"answer": "<your informative answer>", "grounded": <true if drawn from the knowledge context, else false>}}"""

            system_prompt = (
                f"You are {self._agent_name}. You are advisory only: you cannot perform "
                f"actions or read live data. Answer the question; never assert that you "
                f"acted. Be calm, concise, and factual."
            )
            if knowledge_context:
                system_prompt += f"\n\nKnowledge context:\n{knowledge_context}"
            else:
                system_prompt += "\n\nNo knowledge context is available for this question."

            response = await generator._llm.generate(
                prompt,
                system=system_prompt,
            )

            elapsed = (time.monotonic() - t0) * 1000

            # This agent executes nothing — success is always False. The answer it
            # produces is advisory: informative, but no capability performed the
            # work. `grounded` is only true when the answer came from a knowledge
            # context we actually supplied.
            import json
            import re

            # Nothing ran either way — this never succeeds. But whether the
            # capability *genuinely does not exist* (unimplemented — a real
            # execution blocker) is only true when Broca/Provider resolution
            # was conclusive. If a provider errored mid-lookup, its agent
            # might have matched; we cannot rule that out, so it is not a
            # blocker, only an unresolved/recoverable outcome.
            resolution_uncertain = bool(
                self._resolution_diagnostics.get("broca_error")
                or self._resolution_diagnostics.get("provider_registry_error")
                or self._resolution_diagnostics.get("providers_unreachable")
            )
            unimplemented = not resolution_uncertain

            def _advisory(answer: str, grounded: bool) -> AgentResult:
                metadata = {
                    "source": "codegen",
                    "grounded": grounded,
                    "advisory": True,
                    "synthetic": True,
                    "unimplemented": unimplemented,
                    "resolution_uncertain": resolution_uncertain,
                }
                metadata.update(self._resolution_diagnostics)
                return AgentResult(
                    agent_name=self._agent_name,
                    success=False,
                    produced={
                        "answer": answer,
                        "grounded": grounded,
                        "advisory": True,
                        "unimplemented": unimplemented,
                    },
                    latency_ms=elapsed,
                    metadata=metadata,
                )

            m = re.search(r"\{.*\}", response, re.DOTALL)
            if m:
                try:
                    result = json.loads(m.group(0))
                    grounded = bool(result.get("grounded", False)) and bool(knowledge_context)
                    return _advisory(str(result.get("answer", "")).strip(), grounded)
                except json.JSONDecodeError:
                    logger.debug("execute: suppressed exception", exc_info=True)

            # Raw (non-JSON) text response — still advisory, never grounded.
            return _advisory(response.strip(), False)

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            return AgentResult(
                agent_name=self._agent_name,
                success=False,
                produced={"error": str(e)},
                latency_ms=elapsed,
            )

    def _load_knowledge_context(self, question: str, identity: dict | None = None) -> str:
        """Load relevant knowledge from knowledge packs for grounded responses.

        Scoped by caller identity (tenant/roles) just like the middleware's
        load_knowledge_context — packs with no access metadata stay public; restricted
        packs require a matching identity.
        """
        try:
            from pathlib import Path
            import yaml
            from src.monkey_brain.runtime.agent_middleware import _pack_permitted

            kp_dir = Path(__file__).parents[3] / "somatic" / "knowledge_packs"
            if not kp_dir.exists():
                return ""

            context_parts = []
            for kp_file in kp_dir.glob("*.yaml"):
                try:
                    with open(kp_file) as f:
                        data = yaml.safe_load(f)
                    if not _pack_permitted(data.get("pack", {}) or {}, identity):
                        continue  # caller not authorized for this pack
                    items = data.get("items", [])
                    for item in items[:5]:  # Limit to 5 items per pack
                        content = item.get("content", "")
                        if content:
                            context_parts.append(content.strip()[:500])
                except Exception:
                    continue

            return "\n\n".join(context_parts[:3])  # Limit total context
        except Exception:
            return ""


class AgentResolver:
    """Resolves agent names to ExecutableAgent instances.

    Resolution hierarchy:
        1. Cache (fastest)
        2. Local Broca registry
        3. Provider Registry (OpenClaw, n8n, NANDA, ARD)
        4. CodeGen (last resort — only when agent cannot be found)

    CodeGen is only invoked when all other sources fail. It generates
    a new agent on-the-fly using the LLM.
    """

    def __init__(self, cache_size: int = 256):
        self._cache: dict[str, ExecutableAgent] = {}
        self._cache_size = cache_size

    async def resolve(
        self, agent_name: str, *, allow_create: bool = True, question: str = ""
    ) -> ExecutableAgent | None:
        """Resolve an agent name to an ExecutableAgent.

        `allow_create=False` disables tier 4 (n8n auto-create) and tier 5
        (SDLC build) — for an agent name that is itself a fabricated
        placeholder (e.g. capability classification came back "unknown" and
        CapabilityResolver invented f"{group}Agent"), spinning up real
        infrastructure for it is never correct: there is no real
        responsibility to implement, only a name that means "we don't know."
        Broca/provider *discovery* (tier 2/3) still runs — an already-real
        agent should still be found.

        `question` is the original triggering request, when the caller has
        it — used only by tier 5 (SDLC build) to give the pipeline's
        specification/requirements stages real context instead of just the
        bare agent name. Falls back to agent_name if omitted.

        Returns None only if CodeGen also fails.
        """
        # 1. Cache
        cached = self._cache.get(agent_name)
        if cached is not None:
            return cached

        # Collected as we go so the CodeGen fallback, if reached, can report
        # *why* nothing real was found — specifically, whether resolution was
        # inconclusive (a provider errored) rather than genuinely empty.
        diagnostics: dict[str, Any] = {}

        # 2. Local Broca registry
        agent = self._resolve_broca(agent_name, diagnostics)
        if agent is not None:
            self._cache_put(agent_name, agent)
            await self._register_provider(agent.resolved_type, agent_name)
            return agent

        # 2.5 Generalization — the planner invents granular, one-off names
        # ("DoctorAvailabilityAgent") that will never exact- or fuzzy-match
        # a real agent, even when a real one already covers the general
        # case ("appointment"). This MUST run before tier 3 (Provider
        # Registry), not after: _resolve_provider's own n8n-auto-create
        # sub-step fires on any allow_create=True call and will happily
        # build a whole new (redundant) workflow for the granular name
        # before a later-positioned generalization check ever got a look
        # in — confirmed in practice, not just in theory. Lazy import:
        # avoids a module-load-time cycle with agent_middleware, which
        # already imports from this module inside its own functions.
        try:
            from src.monkey_brain.runtime.agent_middleware import AgentMiddleware

            general = await AgentMiddleware._lookup_generalization(agent_name)
            discovered_now = False
            if general is None:
                general = await AgentMiddleware._discover_generalization(agent_name, question or agent_name)
                discovered_now = bool(general)
            if general and general != agent_name:
                general_agent = await self.resolve(general, allow_create=False, question=question)
                if general_agent is not None and not isinstance(general_agent, CodeGenAgent):
                    if discovered_now:
                        await AgentMiddleware._link_capability_generalization(agent_name, general)
                    logger.info(
                        "[agent_resolver] %r resolved via generalization to real agent %r",
                        agent_name,
                        general,
                    )
                    self._cache_put(agent_name, general_agent)
                    return general_agent
        except Exception as e:
            logger.debug("[agent_resolver] generalization check failed for %r: %s", agent_name, e)

        # 3. Provider Registry (tier 4 n8n auto-create inside it is gated by allow_create)
        provider_agent = await self._resolve_provider(
            agent_name,
            diagnostics,
            allow_create=allow_create,
            question=question,
        )
        if provider_agent is not None:
            self._cache_put(agent_name, provider_agent)
            await self._register_provider(f"{provider_agent._provider_name}:{agent_name}", agent_name)
            return provider_agent

        # 5. SDLC build — last resort before CodeGen-advisory, only once n8n
        # auto-create has also failed (fast; SDLC is not — several minutes,
        # a real LLM call per stage). Local-only: no PR, no external deploy,
        # see CodeGenRuntime.build_sdlc_graph(include_release=False).
        if allow_create:
            sdlc_agent = await self._try_sdlc_build(agent_name, question or agent_name, diagnostics)
            if sdlc_agent is not None:
                self._cache_put(agent_name, sdlc_agent)
                return sdlc_agent

        # 6. CodeGen fallback — warns but allows pipeline to continue
        logger.warning(
            "[agent_resolver] Agent %r not found in Broca, providers, or via SDLC build — falling back to CodeGen",
            agent_name,
        )
        codegen_agent = CodeGenAgent(agent_name, resolution_diagnostics=diagnostics)
        self._cache_put(agent_name, codegen_agent)
        return codegen_agent

    @staticmethod
    async def _register_provider(provider_identifier: str, capability: str) -> None:
        """Record a real, confirmed resolution into the Capability Graph.

        Every tier of resolve() that finds a genuinely real agent (Broca,
        Provider Registry/n8n, SDLC build) calls this — previously only
        AgentMiddleware's own composite-composition path registered
        anything into the mesh, so a plain "the capability already exists
        and resolves on tier 2" run left no PROVIDES edge behind at all.
        Reuses AgentMiddleware's implementation rather than duplicating the
        Neo4j write; lazy import avoids a module-load cycle (agent_middleware
        already imports from this module at call time, not at import time).
        """
        try:
            from src.monkey_brain.runtime.agent_middleware import AgentMiddleware

            await AgentMiddleware._register_capability_provider(provider_identifier, capability)
        except Exception as e:
            logger.debug(
                "[agent_resolver] mesh provider registration failed for %s/%s: %s",
                provider_identifier,
                capability,
                e,
            )

    async def _try_sdlc_build(
        self, agent_name: str, question: str, diagnostics: dict[str, Any] | None
    ) -> ExecutableAgent | None:
        """Build a real agent via the SDLC runtime (spec → ... → serve →
        client_gen → register) and return it if registration succeeded.

        Synchronous and slow by nature — every stage is a real LLM call and
        several write real files under generated/, somatic/compiled/,
        somatic/charts/. Never raises: any failure here just means CodeGen-
        advisory is what the caller falls back to, same as an n8n failure.
        """
        try:
            from src.monkey_brain.kernel.codegen_runtime import (
                get_codegen_runtime_instance,
                _slugify_question,
            )

            codegen = get_codegen_runtime_instance()
            if codegen is None:
                logger.debug(
                    "[agent_resolver] SDLC build skipped for %r: CodeGenRuntime not booted",
                    agent_name,
                )
                return None

            service_slug = _slugify_question(agent_name)
            logger.info(
                "[agent_resolver] Attempting SDLC build for %r (slug=%r) — this can take several minutes",
                agent_name,
                service_slug,
            )

            # run_question() -> compile_intent() -> create_goal() requires the
            # question to match a registered INTENT_REGISTRY predicate — the
            # same gate /plan tolerates (it proceeds anyway, flagged as
            # low-confidence) but CodeGenRuntime hard-requires. A freeform
            # "build PriorityAgent" trigger will essentially never match one,
            # so build the IntentIR directly: we already know we want the
            # codegen pipeline for this question, there is nothing to
            # classify.
            from uuid import uuid4
            from src.monkey_brain.kernel.plan.goals.goal import (
                Goal,
                GoalType,
                GoalSource,
            )
            from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir

            run_id = uuid4().hex
            goal = Goal(
                name=f"sdlc_build:{service_slug}",
                description=question[:256],
                goal_type=GoalType.CREATE,
                source=GoalSource.AGENT,
                confidence=1.0,
                metadata={"agent_name": agent_name, "service_slug": service_slug},
            )
            intent_ir = build_intent_ir(
                intent={
                    "intent": "codegen",
                    "confidence": 1.0,
                    "workload_id": service_slug,
                },
                goal=goal,
                run_id=run_id,
                question=question,
            )

            result = await codegen.run(
                intent_ir,
                include_release=False,
                service_slug=service_slug,
            )
            if result.get("state") != "completed":
                logger.warning(
                    "[agent_resolver] SDLC build for %r ended in state=%s error=%s",
                    agent_name,
                    result.get("state"),
                    result.get("error"),
                )
                if diagnostics is not None:
                    diagnostics["sdlc_build_failed"] = result.get("state")
                return None

            registered_type = f"{service_slug}-client-agent"
            from broca.registry import get_registry

            built = get_registry().discover(registered_type)
            if built is None:
                logger.warning(
                    "[agent_resolver] SDLC build for %r completed but %r was never registered",
                    agent_name,
                    registered_type,
                )
                return None

            logger.info(
                "[agent_resolver] SDLC-built agent %r registered as %r",
                agent_name,
                registered_type,
            )
            # Close the "Register New Capability -> Update Capability Graph"
            # loop for the SDLC path specifically: ClientCapabilityRegisterAgent
            # only registers into Broca's in-memory registry (see its own
            # module docstring) which is lost on restart and never linked
            # into Neo4j — without this, an SDLC build that succeeds today
            # is invisible to tomorrow's generalization/mesh lookups and
            # gets rebuilt from scratch every time the process restarts.
            await self._register_provider(registered_type, agent_name)
            return BrocaAgent(built, agent_name)
        except Exception as e:
            logger.warning("[agent_resolver] SDLC build failed for %r: %s", agent_name, e)
            if diagnostics is not None:
                diagnostics["sdlc_build_error"] = str(e)
            return None

    async def check_availability(self, agent_name: str, question: str = "") -> dict[str, Any]:
        """Read-only: would `resolve(agent_name)` find a real agent right now?

        `question` (the original triggering request, when the caller has
        it) sharpens generalization discovery only — falls back to
        agent_name alone if omitted.

        Reuses the exact same Broca/Provider lookups `resolve()` uses — so
        "available" here means exactly what it will mean at execution time —
        but never falls back to CodeGen and never executes anything. Used to
        decide, before execution (e.g. at plan time), whether a capability is
        genuinely backed or would only ever produce an advisory answer.

        Returns:
            available            — a real (non-synthetic) agent would resolve
            source                — "cache" | "broca" | "provider:<name>" | None
            broca_error           — the Broca registry itself could not be queried
            provider_registry_error — the provider registry itself could not be queried
            providers_configured  — provider names that were actually queried
            providers_unreachable — of those, which raised during the query
        """
        cached = self._cache.get(agent_name)
        if cached is not None and not isinstance(cached, CodeGenAgent):
            return {"available": True, "source": "cache"}

        diagnostics: dict[str, Any] = {}

        agent = self._resolve_broca(agent_name, diagnostics)
        if agent is not None:
            return {"available": True, "source": "broca", **diagnostics}

        # allow_create=False: this is a read-only check. Creating an n8n
        # workflow is a real, visible side effect and has no place here.
        provider_agent = await self._resolve_provider(agent_name, diagnostics, allow_create=False)
        if provider_agent is not None:
            return {
                "available": True,
                "source": f"provider:{provider_agent._provider_name}",
                **diagnostics,
            }

        # Generalization check — lookup, then discovery if nothing's linked
        # yet. Without attempting discovery here too, this would only ever
        # help a granular name's *second* occurrence (once resolve() has
        # already discovered+linked it) — given the planner invents a fresh
        # name most times ("DoctorAvailabilityAgent" this run,
        # "AppointmentBookingAgent" next run), the exact same name rarely
        # repeats, so a lookup-only check would almost never fire. An LLM
        # query isn't a side effect the way n8n creation is (nothing
        # external is created or mutated), so it fits this method's
        # read-only contract even though it costs a real LLM call.
        try:
            from src.monkey_brain.runtime.agent_middleware import AgentMiddleware

            general = await AgentMiddleware._lookup_generalization(agent_name)
            discovered_now = False
            if general is None:
                general = await AgentMiddleware._discover_generalization(agent_name, question or agent_name)
                discovered_now = bool(general)
            if general and general != agent_name:
                general_available = await self.check_availability(general)
                if general_available.get("available"):
                    if discovered_now:
                        await AgentMiddleware._link_capability_generalization(agent_name, general)
                    return {
                        "available": True,
                        "source": f"generalization:{general}",
                        **diagnostics,
                    }
        except Exception as e:
            logger.debug(
                "[agent_resolver] check_availability generalization check failed for %r: %s",
                agent_name,
                e,
            )

        return {"available": False, "source": None, **diagnostics}

    def _resolve_broca(self, agent_name: str, diagnostics: dict[str, Any] | None = None) -> BrocaAgent | None:
        """Resolve from local Broca registry with fuzzy fallback.

        `diagnostics`, if given, is updated with `broca_error=True` when the
        registry itself could not be queried — as opposed to being queried
        successfully and having no match.
        """
        try:
            from broca.registry import get_registry

            registry = get_registry()

            # 1. Exact match
            agent = registry.discover(agent_name)
            if agent is not None:
                return BrocaAgent(agent, agent_name)

            # 2. PascalCase → snake_case, then again with the "_agent" suffix dropped.
            snake = _snake_case(agent_name)
            core = _strip_agent_suffix(snake)
            for key in (snake, core):
                if not key:
                    continue
                agent = registry.discover(key)
                if agent is not None:
                    return BrocaAgent(agent, agent_name)

            # 3. Fuzzy match — last resort, and only when it is UNAMBIGUOUS.
            match = _fuzzy_agent_type(core, registry.agent_types(), agent_name)
            if match:
                agent = registry.discover(match)
                if agent is not None:
                    logger.info(
                        "[agent_resolver] %r resolved to registered agent %r by fuzzy match",
                        agent_name,
                        match,
                    )
                    return BrocaAgent(agent, agent_name)

        except Exception as e:
            logger.debug("[agent_resolver] Broca lookup failed for %r: %s", agent_name, e)
            if diagnostics is not None:
                diagnostics["broca_error"] = True
        return None

    async def _resolve_provider(
        self,
        agent_name: str,
        diagnostics: dict[str, Any] | None = None,
        *,
        allow_create: bool = True,
        question: str = "",
    ) -> ProviderAgent | None:
        """Resolve from Provider Registry (OpenClaw, n8n, NANDA, ARD).

        1. Exact match on already-discovered agents.
        2. Active discovery — query each provider for agents matching the name.
        3. n8n workflow creation — if n8n is available, create a workflow for
           the agent on-the-fly and register it. Skipped when allow_create is
           False (check_availability): creating a workflow is a real side
           effect and has no place in a read-only availability check.

        `diagnostics`, if given, is updated with:
            providers_configured    — provider names actually queried
            providers_unreachable   — of those, which raised during the query
            provider_registry_error — the registry itself could not be reached
        """
        try:
            from src.monkey_brain.kernel.provider_registry import (
                init_providers,
                _provider_registry,
            )

            # Force re-init if no provider has a URL (stale singleton from before .env)
            if _provider_registry is not None:
                has_urls = any(p.url for p in _provider_registry._providers.values())
                if not has_urls:
                    import src.monkey_brain.kernel.provider_registry as pr

                    pr._provider_registry = None

            registry = init_providers()

            # 1. Exact match on cached agents
            agent_info = registry.find_agent(agent_name)
            if agent_info is not None:
                provider_name = agent_info.get("provider", "unknown")
                return ProviderAgent(provider_name, agent_name)

            # 2. Active discovery — ARD, then OpenClaw, then n8n, matching
            # the stated provider hierarchy (ARD Discovery -> OpenClaw ->
            # n8n). registry._providers is a plain dict in whatever order
            # init_providers() registered them, which doesn't reflect this
            # priority, so iterate an explicit order instead.
            import httpx

            _PROVIDER_PRIORITY = ("ard", "openclaw", "n8n")
            by_name = {p.name: p for p in registry._providers.values()}
            ordered_providers = [by_name[name] for name in _PROVIDER_PRIORITY if name in by_name]
            ordered_providers += [p for p in registry._providers.values() if p.name not in _PROVIDER_PRIORITY]

            for provider in ordered_providers:
                # A provider can be available via CLI/local-gateway with no
                # REST url at all (OpenClaw's default mode) — `available` is
                # the real signal; requiring a url too used to skip it
                # entirely instead of trying its CLI fallback.
                if not provider.available:
                    continue
                if diagnostics is not None:
                    diagnostics.setdefault("providers_configured", []).append(provider.name)
                try:
                    if provider.name == "n8n" and provider.url:
                        api_key = os.environ.get("N8N_API_KEY", "")
                        headers = {"X-N8N-API-KEY": api_key} if api_key else {}
                        resp = httpx.get(
                            f"{provider.url}/api/v1/workflows",
                            headers=headers,
                            timeout=3,
                        )
                        if resp.status_code == 200:
                            for wf in resp.json().get("data", []):
                                if wf.get("active"):
                                    agent_info = {
                                        "name": wf["name"],
                                        "description": wf.get("description", ""),
                                        "provider": "n8n",
                                        "workflow_id": wf["id"],
                                    }
                                    provider.register_agent(agent_info)
                    elif provider.name == "openclaw":
                        # Reuse OpenClawProvider.discover_agents() itself —
                        # it already tries REST (only when provider.url is a
                        # real headless REST server) then falls back to the
                        # `openclaw agents list --json` CLI, and registers
                        # results as it goes. The prior inline GET {url}/agents
                        # here bypassed that fallback and always misfired: the
                        # local gateway serves its web UI at /agents, not
                        # JSON, so a 200 response still failed to parse and
                        # got logged as "unreachable" even though OpenClaw was
                        # right there and the CLI could see it fine.
                        await provider.discover_agents(agent_name)
                    elif provider.name == "ard":
                        # ARDProvider requires a non-empty query — agent_name
                        # is the closest thing to one here. Was never queried
                        # at all before this: the hierarchy named it as the
                        # first external tier, but nothing ever called it.
                        await provider.discover_agents(agent_name)
                except Exception as e:
                    logger.debug("[agent_resolver] %s query failed: %s", provider.name, e)
                    if diagnostics is not None:
                        diagnostics.setdefault("providers_unreachable", []).append(provider.name)

                agent_info = registry.find_agent(agent_name)
                if agent_info is not None:
                    logger.info(
                        "[agent_resolver] Found %r in provider %s",
                        agent_name,
                        provider.name,
                    )
                    return ProviderAgent(provider.name, agent_name)

            # 3. n8n auto-create workflow if n8n is available
            if allow_create and _autocreate_enabled():
                n8n_provider = registry.get_provider("n8n")
                if n8n_provider and n8n_provider.available:
                    # `question` was accepted by _create_n8n_workflow but never passed, so the
                    # workflow was synthesized from the bare agent name with the request that
                    # triggered it thrown away — the generator's best context, discarded.
                    created = await self._create_n8n_workflow(n8n_provider, agent_name, question)
                    if created:
                        return ProviderAgent("n8n", agent_name)

        except Exception as e:
            logger.debug("[agent_resolver] Provider lookup failed for %r: %s", agent_name, e)
            if diagnostics is not None:
                diagnostics["provider_registry_error"] = True
        return None

    # Node types confirmed available on this n8n instance (matches the
    # existing hand-built "MonkeyBrain — *" workflows). executeCommand and
    # similar shell/SSH nodes are deliberately excluded — an LLM-generated
    # workflow must never be able to shell out.
    _N8N_ALLOWED_NODE_TYPES = frozenset(
        {
            "n8n-nodes-base.webhook",
            "n8n-nodes-base.respondToWebhook",
            "n8n-nodes-base.code",
            "n8n-nodes-base.set",
            "n8n-nodes-base.if",
            "n8n-nodes-base.httpRequest",
            "n8n-nodes-base.noOp",
        }
    )

    async def _generate_n8n_workflow_json(self, agent_name: str, question: str, webhook_path: str) -> dict | None:
        """Ask the LLM to synthesize a real n8n workflow for this agent.

        Not a stub: the LLM infers the agent's responsibility from its name
        and the triggering question, and implements it as real logic in a
        Code node — the same node the LLM's own advisory answers would
        otherwise just talk about doing.
        """
        from broca.agents.graph_generator_agent import GraphGeneratorAgent

        generator = GraphGeneratorAgent()
        if generator._llm is None:
            return None

        # The generator was told which node types it may use and NOTHING about the data, so
        # it invented the data: `pumpData.lines`, `pumpNumbers`, a hardcoded '3'. Hand it the
        # real schema, and forbid inventing what isn't in it.
        try:
            from src.monkey_brain.kernel.data_catalog import prompt_fragment

            data_context = await prompt_fragment()
        except Exception as exc:
            logger.debug("[agent_resolver] data catalog unavailable for %r: %s", agent_name, exc)
            data_context = ""

        allowed = ", ".join(sorted(self._N8N_ALLOWED_NODE_TYPES))
        # A small local model follows a concrete worked example far more
        # reliably than a prose schema description — it was consistently
        # inventing "settings" instead of "parameters" on nodes, and a flat
        # connections list instead of n8n's nested {source: {main: [[...]]}}
        # object, until this example was added.
        example = (
            "{\n"
            '  "name": "MonkeyBrain — ExampleAgent",\n'
            '  "nodes": [\n'
            '    {"parameters": {"httpMethod": "POST", "path": "exampleagent", "responseMode": "responseNode"}, '
            '"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 1, "position": [250, 300]},\n'
            '    {"parameters": {"jsCode": "const body = $input.first().json.body; return [{json: {result: body.value || 0}}];"}, '
            '"name": "Code", "type": "n8n-nodes-base.code", "typeVersion": 1, "position": [500, 300]},\n'
            '    {"parameters": {"respondWith": "json", "responseBody": "={{ $json }}"}, '
            '"name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1, "position": [750, 300]}\n'
            "  ],\n"
            '  "connections": {\n'
            '    "Webhook": {"main": [[{"node": "Code", "type": "main", "index": 0}]]},\n'
            '    "Code": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}\n'
            "  },\n"
            '  "settings": {}\n'
            "}"
        )
        system_prompt = (
            "You generate n8n public API (v1) workflow JSON for a self-hosted n8n instance. "
            f"Use ONLY these node types, confirmed available on this instance: {allowed}. "
            "Never use n8n-nodes-base.executeCommand, ssh, or any node that runs shell "
            "commands or reads local files — those are disabled here.\n\n"
            "Return strictly valid JSON, no markdown fences. Follow this exact shape — the "
            'same keys, same nesting, at every level (node config goes under "parameters", '
            'never "settings"; "connections" is an object keyed by source node name, '
            "never a list):\n\n"
            f"{example}\n\n"
            "Adapt this example to the agent below: keep the same Webhook → Code → Respond "
            f'structure, change the webhook path to "{webhook_path}", and replace the Code '
            "node's jsCode with logic that actually implements the agent's responsibility "
            "(reading $input.first().json.body, returning real computed output — never a "
            "placeholder echo).\n\n"
            "JSON-safety rules for jsCode, since it is embedded inside the JSON you return:\n"
            "- Use single quotes for all string literals in the JavaScript — never a double "
            "  quote character anywhere in it, not even inside a template literal or comment.\n"
            "- Write it as compact single-line statements; avoid backticks and escape "
            "  sequences entirely.\n"
            "- Keep it under 15 lines — simple field extraction and a plain object return."
            f"{data_context}"
        )
        prompt = (
            f"Agent to implement: {agent_name}\n"
            f"Request that needed it: {question}\n\n"
            f"Infer {agent_name}'s responsibility from its name and the request above, "
            "then generate the n8n workflow JSON that actually implements it."
        )

        import json
        import re

        _NODE_FIELDS = ("parameters", "name", "type", "typeVersion", "position")

        def _parse_and_validate(response: str) -> dict | None:
            m = re.search(r"\{.*\}", response, re.DOTALL)
            if not m:
                logger.warning(
                    "[agent_resolver] LLM did not return JSON for n8n workflow %r",
                    agent_name,
                )
                return None
            try:
                workflow = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                logger.warning(
                    "[agent_resolver] LLM returned invalid JSON for n8n workflow %r: %s",
                    agent_name,
                    e,
                )
                return None

            if not isinstance(workflow, dict) or not workflow.get("nodes"):
                logger.warning("[agent_resolver] LLM workflow for %r missing nodes", agent_name)
                return None

            # Safety net for the two shapes the local model reliably drifts
            # into despite the worked example: a flat list of edges instead
            # of n8n's nested {source: {main: [[...]]}} object, and per-node
            # config under "settings" instead of "parameters".
            connections = workflow.get("connections")
            if isinstance(connections, list):
                # n8n's connections object keys nodes by "name", but the edge
                # list references whatever ad-hoc "id" the LLM invented per
                # node — resolve id → name so edges still land on the right
                # (post-sanitization) node.
                id_to_name = {}
                for node in workflow["nodes"]:
                    if isinstance(node, dict) and node.get("name"):
                        for key in ("id", "name"):
                            if node.get(key):
                                id_to_name[node[key]] = node["name"]
                normalized: dict = {}
                for edge in connections:
                    if not isinstance(edge, dict):
                        continue
                    src = edge.get("sourceNodeId") or edge.get("source") or edge.get("from")
                    dst = edge.get("targetNodeId") or edge.get("target") or edge.get("to")
                    src = id_to_name.get(src, src)
                    dst = id_to_name.get(dst, dst)
                    if not src or not dst:
                        continue
                    normalized.setdefault(src, {}).setdefault("main", [[]])[0].append(
                        {"node": dst, "type": "main", "index": 0}
                    )
                connections = normalized
            if not isinstance(connections, dict) or not connections:
                logger.warning(
                    "[agent_resolver] LLM workflow for %r missing/malformed connections",
                    agent_name,
                )
                return None
            workflow["connections"] = connections

            # n8n's public API schema is `additionalProperties: false` on nodes
            # and requires "position" — keep only accepted fields, and fill in
            # position ourselves when the LLM omits it rather than burning a
            # retry on something this trivial to fix.
            sanitized_nodes = []
            for i, node in enumerate(workflow["nodes"]):
                if "parameters" not in node and isinstance(node.get("settings"), dict):
                    node = {**node, "parameters": node["settings"]}
                node_type = node.get("type", "")
                if node_type not in self._N8N_ALLOWED_NODE_TYPES:
                    logger.warning(
                        "[agent_resolver] LLM-generated workflow for %r used disallowed node type %r — rejecting",
                        agent_name,
                        node_type,
                    )
                    return None
                sanitized = {k: node[k] for k in _NODE_FIELDS if k in node}
                sanitized.setdefault("position", [250 + i * 250, 300])
                sanitized_nodes.append(sanitized)

            name = workflow.get("name") or f"MonkeyBrain — {agent_name}"
            settings = workflow.get("settings") or {}
            # Same additionalProperties strictness applies at the workflow
            # level — only pass through what the create schema accepts.
            return {
                "name": name,
                "nodes": sanitized_nodes,
                "connections": workflow["connections"],
                "settings": settings,
            }

        # The local Ollama model is flaky at strict JSON/schema compliance on
        # the first try (unescaped quotes in generated JS, missing required
        # fields) — worth a few extra attempts before giving up.
        for attempt in range(3):
            response = await generator._llm.generate(prompt, system=system_prompt)
            workflow = _parse_and_validate(response)
            if workflow is not None:
                return workflow
            logger.info(
                "[agent_resolver] retrying n8n workflow generation for %r (attempt %d failed)",
                agent_name,
                attempt + 1,
            )
        return None

    async def _find_active_n8n_workflow_by_path(self, n8n_url: str, headers: dict, webhook_path: str) -> dict | None:
        """Look up an already-active workflow whose Webhook node uses this
        path, regardless of its display name — active-discovery elsewhere
        keys agents by the workflow's *name* (e.g. "BlogAgent — Example"),
        which almost never matches the bare agent_name a caller resolves
        with, so a name that already has a working workflow still looked
        unresolved and got a doomed duplicate-create attempt (409 on
        activate, since two active workflows can't share a webhook path).
        """
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{n8n_url}/api/v1/workflows",
                    headers=headers,
                    params={"active": "true"},
                )
                if resp.status_code != 200:
                    return None
                for wf in resp.json().get("data", []):
                    for node in wf.get("nodes", []):
                        if (
                            node.get("type") == "n8n-nodes-base.webhook"
                            and node.get("parameters", {}).get("path") == webhook_path
                        ):
                            return {"id": wf.get("id", ""), "name": wf.get("name", "")}
        except Exception as e:
            logger.debug(
                "[agent_resolver] active n8n workflow lookup failed for path %r: %s",
                webhook_path,
                e,
            )
        return None

    async def _create_n8n_workflow(self, n8n_provider, agent_name: str, question: str = "") -> bool:
        """Create (and activate) a real, LLM-generated n8n workflow for an unknown agent.

        Reuses an already-active workflow at the same webhook path instead
        of creating a duplicate, if one exists — see
        _find_active_n8n_workflow_by_path for why that can't be found via
        the normal name-keyed discovery path.
        """
        n8n_url = n8n_provider.url
        api_key = os.environ.get("N8N_API_KEY", "")
        if not n8n_url or not api_key:
            return False

        webhook_path = agent_name.lower().replace("_", "-").replace(" ", "-")
        headers = {"X-N8N-API-KEY": api_key}

        existing = await self._find_active_n8n_workflow_by_path(n8n_url, headers, webhook_path)
        if existing is not None:
            logger.info(
                "[agent_resolver] Reusing already-active n8n workflow %r (id=%s) for %r instead of creating a duplicate",
                existing["name"],
                existing["id"],
                agent_name,
            )
            n8n_provider.register_agent(
                {
                    "name": agent_name,
                    "description": f"Reused existing n8n workflow {existing['name']!r}",
                    "provider": "n8n",
                    "workflow_id": existing["id"],
                    "webhook_path": webhook_path,
                }
            )
            return True

        try:
            import httpx

            workflow = await self._generate_n8n_workflow_json(agent_name, question, webhook_path)
            if workflow is None:
                return False

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(f"{n8n_url}/api/v1/workflows", json=workflow, headers=headers)
                if resp.status_code not in (200, 201):
                    logger.warning(
                        "[agent_resolver] n8n workflow create failed for %r: %s %s",
                        agent_name,
                        resp.status_code,
                        resp.text[:300],
                    )
                    return False
                wf = resp.json()
                workflow_id = wf.get("id", "")

                # This n8n version doesn't honor "active" at create time — a
                # separate activate call is required before the webhook is live.
                activate_resp = await client.post(
                    f"{n8n_url}/api/v1/workflows/{workflow_id}/activate",
                    headers=headers,
                )
                if activate_resp.status_code not in (200, 201):
                    # Most likely cause: a race with another resolve() call
                    # that created+activated a workflow at this path between
                    # our lookup above and this create — reuse it rather
                    # than fail outright now that one genuinely exists.
                    fallback = await self._find_active_n8n_workflow_by_path(n8n_url, headers, webhook_path)
                    if fallback is not None:
                        logger.info(
                            "[agent_resolver] Activate conflict for %r — reusing %r (id=%s) created concurrently",
                            agent_name,
                            fallback["name"],
                            fallback["id"],
                        )
                        n8n_provider.register_agent(
                            {
                                "name": agent_name,
                                "description": f"Reused concurrently-created n8n workflow {fallback['name']!r}",
                                "provider": "n8n",
                                "workflow_id": fallback["id"],
                                "webhook_path": webhook_path,
                            }
                        )
                        return True
                    logger.warning(
                        "[agent_resolver] n8n workflow activate failed for %r (id=%s): %s %s",
                        agent_name,
                        workflow_id,
                        activate_resp.status_code,
                        activate_resp.text[:300],
                    )
                    return False

            agent_info = {
                "name": agent_name,
                "description": f"LLM-generated n8n workflow for {agent_name}",
                "provider": "n8n",
                "workflow_id": workflow_id,
                "webhook_path": webhook_path,
            }
            n8n_provider.register_agent(agent_info)
            logger.info(
                "[agent_resolver] Created + activated n8n workflow for %r (id=%s)",
                agent_name,
                workflow_id,
            )

            # Register into Broca too (tier 1, checked before providers) so
            # this process resolves it instantly next time instead of
            # re-discovering it through the provider registry — and so it
            # never re-triggers workflow generation for the rest of this
            # process's lifetime.
            try:
                from broca.registry import get_registry
                from broca.agents.provider_proxy import ProviderProxyAgent

                get_registry().register(ProviderProxyAgent(agent_info, "n8n"))
            except Exception as e:
                logger.debug(
                    "[agent_resolver] Broca registration failed for %r (n8n workflow still usable via provider lookup): %s",
                    agent_name,
                    e,
                )

            return True
        except Exception as e:
            logger.debug("[agent_resolver] n8n auto-create failed for %r: %s", agent_name, e)
        return False

    def _cache_put(self, name: str, agent: ExecutableAgent) -> None:
        """Add to cache with simple eviction."""
        if len(self._cache) >= self._cache_size:
            # Evict oldest entry
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[name] = agent

    def clear_cache(self) -> None:
        """Clear the agent cache."""
        self._cache.clear()

    def summary(self) -> dict:
        return {
            "cached_agents": list(self._cache.keys()),
            "cache_size": len(self._cache),
            "cache_limit": self._cache_size,
        }


# Module-level singleton
_resolver: AgentResolver | None = None


def get_resolver() -> AgentResolver:
    """Get the singleton AgentResolver."""
    global _resolver
    if _resolver is None:
        _resolver = AgentResolver()
    return _resolver
