"""Agent Middleware — standardized lifecycle for all agents.

Every agent follows the same lifecycle:
    Intent → Classify Capability → Resolve Ambiguity → Load Context → Retrieve Knowledge → Execute → Commit Operations → Serialize Response

The runtime enforces this lifecycle, not individual agents.

The runtime is part of the operating system.
It should coordinate execution—not implement business logic.
No runtime component should know what a Task, Blog, Robot, Work Order, Document,
or any other domain object is.

All domain behavior belongs to capabilities and repositories.

Runtime Responsibilities:
1. Validate IntentIR
2. Classify capability (task.create, blog.write, etc.)
3. Detect ambiguity and request clarification if needed
4. Load execution context
5. Retrieve knowledge
6. Resolve implementation (agent, provider, workflow)
7. Invoke capability
8. Commit returned operations
9. Serialize response
10. Emit telemetry

Nothing else.
"""

from __future__ import annotations

import json
import logging
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any

from src.monkey_brain.runtime.capability_result import CapabilityResult, Response
from src.monkey_brain.runtime.repository_manager import RepositoryManager
from src.monkey_brain.runtime.intent_resolver import IntentResolver
from src.monkey_brain.runtime.clarification_result import ClarificationResult
from src.monkey_brain.runtime.capability_classifier import (
    CapabilityClassifier,
    CapabilityClassification,
)
from src.monkey_brain.runtime.capability_router import CapabilityRouter, CapabilityRoute
from src.monkey_brain.runtime.capability_resolver import (
    CapabilityResolver,
    CapabilityResolution,
)
from src.monkey_brain.runtime.capability_types import (
    CapabilityType,
    capability_type_for_group,
)

logger = logging.getLogger("agentos.agent_middleware")


def _get_lemon():
    try:
        from src.introspection.lemon import get_lemon as _lemon

        return _lemon()
    except Exception:
        return None


class IntentType(str, Enum):
    CREATE = "create"
    RETRIEVE = "retrieve"
    UPDATE = "update"
    DELETE = "delete"
    ANALYZE = "analyze"
    COMPARE = "compare"
    UNKNOWN = "unknown"


# ── Intent Detection (domain-agnostic) ────────────────────────────────────────

_CREATE_WORDS = {
    "add",
    "create",
    "new",
    "write",
    "draft",
    "generate",
    "compose",
    "build",
    "make",
}
_RETRIEVE_WORDS = {
    "get",
    "show",
    "list",
    "find",
    "search",
    "what",
    "how many",
    "status",
    "read",
}
_UPDATE_WORDS = {
    "update",
    "edit",
    "modify",
    "change",
    "set",
    "mark",
    "complete",
    "finish",
}
_DELETE_WORDS = {"delete", "remove", "drop", "clear"}
_ANALYZE_WORDS = {
    "analyze",
    "analysis",
    "summarize",
    "summary",
    "explain",
    "review",
    "evaluate",
}
_COMPARE_WORDS = {"compare", "diff", "difference", "versus", "vs"}


def detect_intent(question: str) -> IntentType:
    """Classify user intent from question text. Domain-agnostic."""
    q_lower = question.lower()
    words = set(q_lower.split())

    if words & _CREATE_WORDS:
        return IntentType.CREATE
    if words & _RETRIEVE_WORDS:
        return IntentType.RETRIEVE
    if words & _UPDATE_WORDS:
        return IntentType.UPDATE
    if words & _DELETE_WORDS:
        return IntentType.DELETE
    if words & _ANALYZE_WORDS:
        return IntentType.ANALYZE
    if words & _COMPARE_WORDS:
        return IntentType.COMPARE
    return IntentType.UNKNOWN


# ── Knowledge Grounding (domain-agnostic) ─────────────────────────────────────


def _identity_from_state(state: dict | None) -> dict | None:
    """Extract caller identity (user/tenant/roles) from execution state for knowledge
    scoping. Returns None when no identity is present."""
    if not state:
        return None
    ident = {
        "user_id": state.get("user_id") or state.get("__user_id__"),
        "tenant": state.get("tenant") or state.get("tenant_id") or state.get("__tenant__"),
        "roles": state.get("roles") or state.get("role_ids") or [],
    }
    return ident if (ident["user_id"] or ident["tenant"] or ident["roles"]) else None


def _pack_permitted(pack_meta: dict, identity: dict | None) -> bool:
    """Whether a caller may retrieve a knowledge pack.

    A pack is public when it declares no access metadata — those stay universally
    visible (backward compatible). A pack that declares `visibility: restricted`,
    `tenant:`, or `allowed_roles:` is scoped: it is returned only when the caller's
    identity matches. Without identity, restricted packs are withheld — a bare question
    no longer returns everything.
    """
    visibility = str(pack_meta.get("visibility", "public")).lower()
    allowed_roles = pack_meta.get("allowed_roles")
    pack_tenant = pack_meta.get("tenant")
    if visibility == "public" and not allowed_roles and not pack_tenant:
        return True
    if not identity:
        return False
    if pack_tenant and pack_tenant != identity.get("tenant"):
        return False
    if allowed_roles and not (set(allowed_roles) & set(identity.get("roles") or [])):
        return False
    return True


def load_knowledge_context(question: str, identity: dict | None = None) -> dict:
    """Load grounded knowledge from knowledge packs, scoped to the caller.

    `identity` — optional {user_id, tenant, roles}. When given, packs are filtered to
    those the caller may access (see _pack_permitted). Packs with no access metadata are
    public; restricted packs (visibility/tenant/allowed_roles) require a matching
    identity, so retrieval is no longer "everything for a bare question string".

    Returns dict with:
        - sources: list of knowledge sources used
        - context: concatenated knowledge text
        - grounding_confidence: 0.0-1.0
    """
    try:
        import yaml

        kp_dir = Path(__file__).parents[3] / "somatic" / "knowledge_packs"
        if not kp_dir.exists():
            return {"sources": [], "context": "", "grounding_confidence": 0.0}

        sources = []
        context_parts = []

        for kp_file in kp_dir.glob("*.yaml"):
            try:
                with open(kp_file) as f:
                    data = yaml.safe_load(f)
                pack_meta = data.get("pack", {}) or {}
                if not _pack_permitted(pack_meta, identity):
                    continue  # caller not authorized for this pack
                pack_name = pack_meta.get("name", kp_file.stem)
                items = data.get("items", [])

                for item in items[:5]:
                    content = item.get("content", "")
                    if content:
                        context_parts.append(content.strip()[:500])
                        sources.append(
                            {
                                "pack": pack_name,
                                "item_id": item.get("id", ""),
                                "provenance": item.get("provenance", 0.0),
                            }
                        )
            except Exception:
                continue

        confidence = 0.0
        if sources:
            provenances = [s.get("provenance", 0) for s in sources]
            confidence = sum(provenances) / len(provenances) if provenances else 0.0

        return {
            "sources": sources[:10],
            "context": "\n\n".join(context_parts[:3]),
            "grounding_confidence": confidence,
        }
    except Exception:
        return {"sources": [], "context": "", "grounding_confidence": 0.0}


# ── Serialization (domain-agnostic) ───────────────────────────────────────────


def serialize_response(response: Response) -> dict[str, Any]:
    """Serialize a Response object to JSON-safe dict."""

    def _sanitize(obj: Any) -> Any:
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        return str(obj)

    return _sanitize(
        {
            "answer": response.answer,
            "format": response.format,
            "metadata": response.metadata,
        }
    )


# ── Middleware Class (completely domain-agnostic) ─────────────────────────────


class AgentMiddleware:
    """Enforces standardized lifecycle for all agent executions.

    The runtime is part of the operating system.
    It should coordinate execution—not implement business logic.
    No runtime component should know what a Task, Blog, Robot, Work Order,
    Document, or any other domain object is.

    Lifecycle:
        Validate → Classify → Context → Knowledge → Resolve → Invoke → Commit → Serialize → Telemetry
    """

    def __init__(self, agent_name: str = ""):
        self.agent_name = agent_name
        self.repository_manager = RepositoryManager()
        self.classifier = CapabilityClassifier()
        self.router = CapabilityRouter()
        self.resolver = CapabilityResolver()

    async def execute(self, agent=None, state: dict = None) -> CapabilityResult | ClarificationResult:
        """Execute through the standardized lifecycle.

        If agent is provided, use it directly with the original state.
        If not, classify capability and resolve implementation.

        Returns CapabilityResult or ClarificationResult.
        """
        if state is None:
            state = {}

        t0 = time.monotonic()
        question = state.get("question", "")

        # Bound up front so the except block below can never raise
        # UnboundLocalError over them and mask the original exception.
        capability_result: CapabilityResult | None = None
        knowledge: dict = {}
        intent: IntentType | None = None
        resolution = None

        try:
            # SHORT-CIRCUIT: If agent is provided, execute it directly
            # without the classify → route → resolve cycle.
            if agent is not None:
                exec_state = dict(state)
                resolver_result = await agent.execute(exec_state)
                capability_result = self._extract_capability_result(resolver_result)

                capability_result.metadata["agent"] = getattr(agent, "agent_type", self.agent_name)
                capability_result.metadata["intent"] = state.get("intent", "")
                capability_result.metadata["question"] = question

                elapsed = (time.monotonic() - t0) * 1000
                capability_result.metadata["latency_ms"] = elapsed

                lemon = _get_lemon()
                if lemon:
                    lemon.counter(
                        "middleware.agent.executed",
                        agent=getattr(agent, "agent_type", self.agent_name),
                    )
                    lemon.histogram(
                        "middleware.agent.latency_ms",
                        elapsed,
                        agent=getattr(agent, "agent_type", self.agent_name),
                    )

                return capability_result

            # 1. Detect Intent (domain-agnostic)
            intent = detect_intent(question)

            # 2. Classify Capability
            classification = self.classifier.classify(question)
            # Whether the *question* itself resolved to a real capability. The
            # answer text can never revise this — it comes from the agent whose
            # capability is in doubt. This is the grounding gate below.
            question_unresolved = classification.capability == "unknown" or classification.confidence < 0.5
            logger.info(
                "[middleware] Classified capability: %s (confidence: %.2f)",
                classification.capability,
                classification.confidence,
            )

            # 3. Resolve Intent — detect ambiguity
            intent_resolver = IntentResolver()
            resolution = intent_resolver.resolve(question, intent.value)

            # 4. If clarification required, return ClarificationResult
            if resolution.requires_clarification:
                elapsed = (time.monotonic() - t0) * 1000
                logger.info(
                    "[middleware] Ambiguity detected for '%s', requesting clarification",
                    question[:50],
                )
                lemon = _get_lemon()
                if lemon:
                    lemon.counter("middleware.clarification_required")
                return ClarificationResult(
                    question=resolution.clarification_question,
                    candidates=resolution.candidates,
                    original_question=question,
                    intent_type=intent.value,
                    metadata={"latency_ms": elapsed, "agent": self.agent_name},
                )

            # 5. Route capability to group
            route = self.router.route(classification.capability)
            logger.info("[middleware] Routed to group: %s", route.group)

            question_capability = classification.capability
            question_confidence = classification.confidence
            lemon = _get_lemon()
            cap_type = capability_type_for_group(route.group)

            if cap_type == CapabilityType.LLM_GENERATIVE:
                # No ground truth to resolve against — the LLM's own output
                # IS the deliverable (summarize/translate: a single LLM call
                # suffices, no multi-step pipeline needed).
                if lemon:
                    lemon.counter(
                        "middleware.capability.routed",
                        capability=classification.capability,
                        group=route.group,
                    )
                    lemon.counter("middleware.generative.invoked")
                knowledge = load_knowledge_context(question, identity=_identity_from_state(state))
                capability_result = await self._generate_via_llm(question, classification.capability, knowledge)
                executed_implementation = "llm_provider"
                reported_capability = classification.capability
            elif cap_type == CapabilityType.COMPOSITE:
                # No single capability satisfies this — compose the group's
                # recipe as a real pipeline (e.g. content_generation:
                # research -> generate -> review -> publish). See
                # capability_types.COMPOSITE_RECIPES and _execute_composite.
                if lemon:
                    lemon.counter(
                        "middleware.capability.routed",
                        capability=classification.capability,
                        group=route.group,
                    )
                    lemon.counter("middleware.composite.invoked")
                knowledge = load_knowledge_context(question, identity=_identity_from_state(state))
                capability_result = await self._execute_composite(route.group, question, knowledge)
                executed_implementation = f"composite:{route.group}"
                reported_capability = classification.capability
            else:
                # 6. Resolve implementation
                impl = self.resolver.resolve(route.group)
                logger.info(
                    "[middleware] Resolved implementation: %s (%s)",
                    impl.implementation,
                    impl.implementation_type,
                )

                if lemon:
                    lemon.counter(
                        "middleware.capability.routed",
                        capability=classification.capability,
                        group=route.group,
                    )
                    lemon.counter(
                        "middleware.implementation.resolved",
                        implementation=impl.implementation,
                    )

                # 7. Load Context
                context = {
                    "agent_name": impl.implementation,
                    "intent": intent.value,
                    "capability": classification.capability,
                    "capability_group": route.group,
                    "question": question,
                    "knowledge": {},
                    "resolved_parameters": (resolution.best_candidate.parameters if resolution.best_candidate else {}),
                }

                # 8. Retrieve Knowledge (domain-agnostic)
                knowledge = load_knowledge_context(question, identity=_identity_from_state(state))
                context["knowledge"] = knowledge

                # 9. Resolve & Invoke Capability
                from src.monkey_brain.runtime.agent_resolver import get_resolver

                resolver = get_resolver()
                # impl.implementation_type == "generated" means CapabilityResolver
                # had no real mapping and fabricated f"{group}Agent" as a
                # placeholder (see capability_resolver.py) — there is no real
                # responsibility behind that name, so it must never trigger a
                # real n8n workflow creation. Broca/provider discovery of an
                # already-real agent under that name is still allowed.
                agent = await resolver.resolve(
                    impl.implementation,
                    allow_create=impl.implementation_type != "generated",
                    question=question,
                )
                if agent is None:
                    return CapabilityResult(
                        success=False,
                        response=Response(answer=f"Could not resolve implementation for {classification.capability}"),
                        metadata={"error": f"no implementation for {impl.implementation}"},
                    )

                exec_state = {**state, **context}
                resolver_result = await agent.execute(exec_state)

                # 10. Extract CapabilityResult from agent result
                capability_result = self._extract_capability_result(resolver_result)
                # The agent resolved from the question is the one that ran; keep it.
                # Refinement only relabels the capability for reporting.
                executed_implementation = impl.implementation
                classification, route = self._refine_unknown_classification_from_response(
                    classification,
                    route,
                    impl,
                    capability_result,
                )

                # A label inferred from the agent's own answer text is a diagnostic,
                # not the capability we served. Report what the question resolved to.
                reported_capability = classification.capability
                if question_unresolved and classification.capability != question_capability:
                    capability_result.metadata["inferred_capability"] = classification.capability
                    reported_capability = question_capability

                # 11. Commit Operations (domain-agnostic)
                if capability_result.operations:
                    commit_results = self.repository_manager.commit(capability_result.operations)
                    capability_result.metadata["commit_results"] = commit_results

                # Grounding gate — an unbacked or ungrounded answer must abstain
                # rather than assert. Decided from whether a real agent actually
                # resolved (see _enforce_grounding), never from classifier text
                # alone — a weakly-classified question can still resolve to a
                # real, working agent, and a confidently-classified one can still
                # have none behind it.
                self._enforce_grounding(
                    capability_result,
                    question_unresolved=question_unresolved,
                    group=route.group,
                )

                # A real capability actually ran (executable=True) — its answer
                # is raw/structured (an n8n Code node's JSON return, a repository
                # row, ...), not prose. Reformat it into a readable answer. This
                # is presentation only: the LLM sees exactly what the capability
                # returned and nothing else, so it cannot introduce facts the
                # real data didn't provide — never called for advisory/CodeGen
                # answers, which are already the LLM's own prose.
                if capability_result.metadata.get("executable"):
                    reformatted = await self._reformat_answer_with_llm(question, capability_result)
                    if reformatted:
                        capability_result.metadata["raw_answer"] = capability_result.response.answer
                        capability_result.response.answer = reformatted

            # 12. Add metadata
            capability_result.metadata["capability"] = reported_capability
            capability_result.metadata["capability_group"] = route.group
            capability_result.metadata["implementation"] = executed_implementation
            capability_result.metadata["executed_implementation"] = executed_implementation
            capability_result.metadata["classification_confidence"] = (
                question_confidence if question_unresolved else classification.confidence
            )
            capability_result.metadata["knowledge_sources"] = [s["pack"] for s in knowledge.get("sources", [])]
            capability_result.metadata["grounding_confidence"] = knowledge.get("grounding_confidence", 0.0)
            capability_result.metadata["intent"] = intent.value
            capability_result.metadata["agent"] = executed_implementation

            elapsed = (time.monotonic() - t0) * 1000
            capability_result.metadata["latency_ms"] = elapsed

            # A real provider (any kind — Broca agent, n8n workflow,
            # SDLC-built agent, a DB-backed capability) just confirmed it
            # can satisfy this capability. Record that in the mesh so
            # future resolution can check "what already works" before
            # re-running discovery — every new agent/capability/connector
            # earns its way into the mesh by actually succeeding, not by
            # merely being created.
            if capability_result.metadata.get("executable"):
                await self._register_capability_provider(executed_implementation, reported_capability)

            # 13. Emit telemetry
            self._emit_telemetry(intent, knowledge, capability_result, elapsed)

            lemon = _get_lemon()
            if lemon:
                lemon.observe_pipeline_step(
                    pipeline_id=question[:32],
                    step_name=classification.capability,
                    capability=executed_implementation,
                    status="ok" if capability_result.success else "error",
                    latency_ms=elapsed,
                    agent_type=executed_implementation,
                )
                lemon.observe_agent_reasoning(
                    agent_type=executed_implementation,
                    perception={"question": question[:200], "intent": intent.value},
                    decision={
                        "capability": classification.capability,
                        "group": route.group,
                    },
                    latency_ms=elapsed,
                )
                lemon.counter("middleware.agent.executed", agent=executed_implementation)
                lemon.histogram(
                    "middleware.agent.latency_ms",
                    elapsed,
                    agent=executed_implementation,
                )

            return capability_result

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            error = str(e) or type(e).__name__
            logger.error("[middleware] Execution failed: %s", e, exc_info=True)

            # The exception may have fired before the agent ever ran, so every
            # local below may be unbound — read them defensively. Raising in
            # here would mask `e`, which is the only record of what went wrong.
            if capability_result is None:
                capability_result = CapabilityResult(
                    success=False,
                    response=Response(answer=f"Agent error: {error}"),
                )

            # An exception reached here — the step failed even if the agent
            # already produced a partial result; never report it as success.
            capability_result.success = False
            capability_result.metadata["error"] = error

            # 8. Commit Operations (domain-agnostic)
            if capability_result.operations:
                commit_results = self.repository_manager.commit(capability_result.operations)
                capability_result.metadata["commit_results"] = commit_results

            # 9. Add knowledge metadata
            capability_result.metadata["knowledge_sources"] = [s["pack"] for s in knowledge.get("sources", [])]
            capability_result.metadata["grounding_confidence"] = knowledge.get("grounding_confidence", 0.0)
            capability_result.metadata["intent"] = intent.value if intent else ""
            capability_result.metadata["agent"] = self.agent_name
            capability_result.metadata["resolution_confidence"] = (
                resolution.best_candidate.confidence if resolution and resolution.best_candidate else 0.0
            )
            capability_result.metadata["latency_ms"] = elapsed

            # 10. Emit telemetry
            self._emit_telemetry(intent, knowledge, capability_result, elapsed)

            return capability_result

    def _extract_capability_result(self, result: Any) -> CapabilityResult:
        """Extract CapabilityResult from various agent result types.

        Agents may return:
        - CapabilityResult directly
        - dict with answer/success
        - AgentResult with produced/payload
        - raw string
        """
        if isinstance(result, CapabilityResult):
            return result

        if isinstance(result, dict):
            return CapabilityResult(
                success=result.get("success", True),
                response=Response(
                    answer=result.get("answer", str(result)),
                    format=result.get("format", "text"),
                ),
                operations=[],
                metadata=result.get("metadata", {}),
            )

        # Handle AgentResult from agent_resolver
        if hasattr(result, "produced"):
            produced = result.produced
            if isinstance(produced, dict):
                # AgentResult.metadata is a top-level field, separate from
                # .produced — it carries provenance (source="codegen",
                # synthetic, advisory, grounded, unimplemented, provider name)
                # that the grounding gate needs. `produced.get("metadata")`
                # was always {} since nothing nests metadata inside produced;
                # merge both, with the envelope-level metadata taking priority.
                metadata = dict(produced.get("metadata", {}))
                metadata.update(getattr(result, "metadata", {}) or {})
                # raw_output (e.g. N8nProvider.execute()'s parsed webhook
                # body) isn't itself the answer — it's the real capability's
                # structured result, kept for the LLM-reformat step below to
                # turn into prose instead of being dropped here.
                if produced.get("raw_output") is not None:
                    metadata["raw_output"] = produced["raw_output"]
                # CapabilityResult has room for an answer and metadata, but nowhere for the
                # agent's structured payload — so `sources`, the very thing that makes an
                # answer grounded, was dropped here and could never reach the grounding check.
                # Carry it through the only channel there is.
                metadata["agent_payload"] = produced
                return CapabilityResult(
                    success=result.success if hasattr(result, "success") else True,
                    response=Response(
                        answer=produced.get("answer", str(produced)),
                        format=produced.get("format", "text"),
                    ),
                    operations=[],
                    metadata=metadata,
                )

        # Handle AgentResult with payload containing agent's act() result
        if hasattr(result, "payload"):
            payload = result.payload
            if isinstance(payload, dict):
                # Agent's act() returns {"action": "robot.status", "success": True}
                # Extract the action as the answer, not the whole payload
                action = payload.get("action", "")
                success = payload.get("success", result.success if hasattr(result, "success") else True)
                answer = action if action else payload.get("answer", str(payload))
                return CapabilityResult(
                    success=success if isinstance(success, bool) else bool(success),
                    response=Response(
                        answer=answer,
                        format="action",
                    ),
                    operations=[],
                    metadata=payload,
                )

        # Fallback: wrap raw result
        return CapabilityResult(
            success=True,
            response=Response(answer=str(result)),
            operations=[],
        )

    def _refine_unknown_classification_from_response(
        self,
        classification: CapabilityClassification,
        route: CapabilityRoute,
        impl: CapabilityResolution,
        result: CapabilityResult,
    ) -> tuple[CapabilityClassification, CapabilityRoute]:
        """Use the generated response as a second signal for weak classifications.

        Labelling only. The answer text is produced by the very agent whose
        capability is in doubt, so it may never promote the run to success nor
        reattribute the implementation — the agent resolved from the *question*
        is the one that actually ran. Callers keep `impl` and gate success on
        whether the question itself resolved to a real capability.
        """
        if classification.capability != "unknown" and classification.confidence >= 0.5:
            result.metadata.setdefault("classification_source", "question")
            return classification, route

        answer = (result.response.answer or "").strip()
        if not answer:
            result.metadata.setdefault("classification_source", "question")
            return classification, route

        response_classification = self.classifier.classify(answer)
        if response_classification.capability == "unknown":
            result.metadata.setdefault("classification_source", "question")
            return classification, route

        result.metadata["initial_capability"] = classification.capability
        result.metadata["initial_capability_group"] = route.group
        result.metadata["initial_classification_confidence"] = classification.confidence
        result.metadata["classification_source"] = "response"
        result.metadata["capability_inferred_from_answer"] = True
        result.metadata["response_classification_confidence"] = response_classification.confidence

        response_route = self.router.route(response_classification.capability)
        return response_classification, response_route

    @staticmethod
    def _enforce_grounding(
        result: CapabilityResult,
        *,
        question_unresolved: bool,
        group: str,
    ) -> None:
        """Separate *answering* the question from *executing* it.

        When no capability backs the request, the answer may still be useful —
        so it is kept and returned, marked advisory. What must never happen is
        the run reporting success, or the answer implying work was performed.

        `executable` is decided by whether a real agent actually resolved
        (`synthetic` — set by AgentResolver.resolve() when Broca and every
        configured provider were checked and none matched), not by question
        classification. Classification is a cheap upfront guess and can be
        wrong in both directions: a confidently-classified capability can
        still have no agent behind it, and a poorly-classified one can still
        resolve to a real, working agent. `question_unresolved`/`group` are
        used only to phrase the blocker message, never to gate execution.

        Sets:
            executable — a real (Broca/Provider) agent actually ran
            advisory   — the answer is informational; nothing was executed
            grounded   — the answer came from retrieved data, not general recall
            unimplemented — no capability exists at all (a genuine blocker),
                            as opposed to resolution being merely inconclusive
                            (e.g. a provider was unreachable — recoverable)
        """
        synthetic = bool(result.metadata.get("synthetic"))
        claims_grounded = bool(result.metadata.get("grounded"))
        executable = not synthetic

        result.metadata["executable"] = executable
        # A real capability's output is grounded by construction. A synthetic
        # (CodeGen) answer is grounded only when it says it drew on retrieved data.
        result.metadata["grounded"] = claims_grounded if synthetic else True

        if executable:
            return

        # Nothing ran, so this is never a success — but the advisory answer
        # still stands on its own.
        result.success = False
        result.metadata["advisory"] = True

        # CodeGenAgent already computed this from real resolution diagnostics:
        # True only when Broca and every configured provider were conclusively
        # checked and none matched. Default True (genuine) if a future
        # synthetic source omits the flag — silence must not read as certainty.
        unimplemented = bool(result.metadata.get("unimplemented", True))
        result.metadata["unimplemented"] = unimplemented

        if unimplemented:
            if question_unresolved or group == "unknown":
                reason = "no capability in this system matches the request"
            else:
                reason = "no agent is registered or reachable for this capability"
            result.metadata.setdefault("blocker", reason)
        else:
            unreachable = result.metadata.get("providers_unreachable") or []
            detail = f" ({', '.join(unreachable)} unreachable)" if unreachable else ""
            result.metadata.setdefault(
                "blocker",
                f"capability availability could not be confirmed{detail} — try again",
            )

        answer = (result.response.answer or "").strip()
        if not answer:
            result.response.answer = (
                "No capability is available to execute this request, and no advisory answer could be produced."
            )

    async def _execute_composite(self, group: str, question: str, knowledge: dict) -> CapabilityResult:
        """Entry point for a composite capability — recursively decompose
        and execute it via the capability mesh. See _compose_capability for
        the actual recursive engine; this just adapts its (answer, meta)
        return into a CapabilityResult and reports the composition path
        that was used (mesh reuse vs. freshly LLM-discovered vs. flat) for
        observability.
        """
        # skip_direct_resolution=True at the top level: we already know
        # `group` is CapabilityType.COMPOSITE (that's why we're here) — a
        # monolithic implementation for it isn't supposed to exist, so
        # trying to resolve one first (as the recursive step does for
        # sub-capabilities, where a real implementation genuinely might
        # exist) just wastes an n8n auto-create attempt on a group name
        # that will only ever produce an empty stub.
        answer, meta = await self._compose_capability(group, question, prior_context="", skip_direct_resolution=True)
        if meta.get("failed"):
            return CapabilityResult(
                success=False,
                response=Response(
                    answer=f"Composition for {group!r} failed: {meta.get('error', 'no output produced')}"
                ),
                metadata={
                    "executable": False,
                    "composite": True,
                    "blocker": meta.get("error", "composition failed"),
                    "trace": meta.get("trace", []),
                },
            )
        return CapabilityResult(
            success=True,
            response=Response(answer=answer, format="markdown" if meta.get("published") else "text"),
            operations=[],
            metadata={
                "executable": True,
                "composite": True,
                "generative": True,
                "synthetic": False,
                "composed_from": meta.get("composed_from"),
                "trace": meta.get("trace", []),
            },
        )

    async def _compose_capability(
        self,
        capability: str,
        question: str,
        prior_context: str = "",
        depth: int = 0,
        max_depth: int = 3,
        skip_direct_resolution: bool = False,
        ancestry: frozenset[str] = frozenset(),
    ) -> tuple[str, dict[str, Any]]:
        """Recursively resolve a capability into real output.

        Order per call:
          1. Try a real implementation via the normal provider hierarchy
             (Local Registry -> ARD -> OpenClaw -> n8n -> SDLC) — composition
             is only for capabilities nothing can satisfy directly. Skipped
             when skip_direct_resolution=True (the top-level call for a
             capability already known to be CapabilityType.COMPOSITE, where
             a monolithic implementation isn't supposed to exist and trying
             anyway just wastes an n8n auto-create attempt on an empty stub).
          2. If that fails (or was skipped), check the persistent capability
             mesh (Neo4j: (:Capability)-[:REQUIRES]->(:Capability)) for a
             decomposition discovered by a *previous* request — reuse it
             rather than asking the LLM to re-derive the same thing.
          3. If the mesh has none either, ask an LLM to decompose this
             capability into an ordered list of sub-capabilities, persist
             that decomposition into the mesh for future reuse, then
             recurse into each sub-capability the same way — threading each
             step's real output into the next.
          4. An atomic capability (the LLM couldn't decompose it further) or
             a decomposition budget of max_depth exhausted both fall back to
             direct LLM generation — the same honest floor every
             undecomposable capability lands on.

        Returns (answer, meta); meta["failed"] is always present, and on
        success meta["trace"] records every step actually taken/composed —
        the "composed graph" the caller reports as metadata.
        """
        if not skip_direct_resolution:
            answer, meta = await self._resolve_composite_subcapability(capability, question, prior_context)
            if not meta.get("failed"):
                return answer, {
                    "failed": False,
                    "trace": [
                        {
                            "capability": capability,
                            "via": "real_implementation",
                            "implementation": meta.get("implementation"),
                        }
                    ],
                }

            # Direct resolution under this exact name failed — before
            # composing/generating from scratch, check whether this is
            # really just a more specific name for a capability that
            # already has a real implementation (e.g. planner-invented
            # "DoctorAvailabilityAgent" generalizing to the real "appointment"
            # domain agent). A known link (from a prior request) is checked
            # first; if none exists yet, try to discover one now against
            # Broca's real agent catalog and persist it for next time.
            general = await self._lookup_generalization(capability)
            discovered_now = False
            if general is None:
                general = await self._discover_generalization(capability, question)
                discovered_now = bool(general)
            if general:
                # `general` is a real Broca agent_type (e.g. "appointment"),
                # not a capability *group* — resolve it directly rather than
                # through _resolve_composite_subcapability, which routes
                # names through CapabilityResolver's 8-group table and would
                # just fabricate yet another placeholder for anything not in it.
                gen_answer, gen_meta = await self._resolve_known_agent_type(general, question, prior_context)
                if not gen_meta.get("failed"):
                    if discovered_now:
                        await self._link_capability_generalization(capability, general)
                    return gen_answer, {
                        "failed": False,
                        "trace": [
                            {
                                "capability": capability,
                                "via": "generalization",
                                "generalizes_to": general,
                                "implementation": gen_meta.get("implementation"),
                            }
                        ],
                    }
                # The linked/discovered general capability doesn't actually
                # resolve either (stale link, or a bad discovery this time)
                # — fall through to composition/generation as normal.

            # Recursive composition via the Execution Mesh: before asking an
            # LLM to invent a decomposition, check whether this capability
            # already exists as a reusable ExecutionGraph — a whole workflow
            # a prior run built and completed. If so, expand it as a
            # subgraph: execute its agents in dependency order, threading
            # context, exactly as if the planner had inlined it.
            mesh_answer, mesh_meta = await self._resolve_via_execution_mesh(capability, question, prior_context)
            if not mesh_meta.get("failed"):
                return mesh_answer, {
                    "failed": False,
                    "trace": [
                        {
                            "capability": capability,
                            "via": "execution_mesh_subgraph",
                            **{k: v for k, v in mesh_meta.items() if k in ("graph_key", "goal", "agents")},
                        }
                    ],
                }

        if depth >= max_depth:
            text, gen_meta = await self._composite_llm_step("generate", question, prior_context, {})
            if gen_meta.get("failed"):
                return "", {
                    "failed": True,
                    "error": f"depth budget exhausted and LLM fallback failed for {capability!r}: {gen_meta.get('error', '')}",
                }
            return text, {
                "failed": False,
                "trace": [{"capability": capability, "via": "llm_direct_depth_limit"}],
            }

        sub_capabilities = await self._lookup_capability_composition(capability)
        from_mesh = sub_capabilities is not None
        if sub_capabilities is None:
            sub_capabilities = await self._decompose_capability_via_llm(capability, question)

        if not sub_capabilities or sub_capabilities == [capability]:
            # Atomic capability — nothing further to decompose. This is the
            # generative floor: an LLM call IS the fulfillment.
            role = "generate" if depth == 0 else "generate"
            text, gen_meta = await self._composite_llm_step(role, question, prior_context, {})
            if gen_meta.get("failed"):
                return "", {
                    "failed": True,
                    "error": f"atomic capability {capability!r} has no implementation and LLM generation failed: {gen_meta.get('error', '')}",
                }
            return text, {
                "failed": False,
                "trace": [{"capability": capability, "via": "llm_direct_atomic"}],
            }

        if not from_mesh:
            await self._persist_capability_composition(capability, sub_capabilities)

        context_so_far = prior_context
        trace: list[dict[str, Any]] = [
            {
                "capability": capability,
                "via": "mesh" if from_mesh else "llm_decomposed",
                "composed_from": sub_capabilities,
            }
        ]
        next_ancestry = ancestry | {capability}
        for sub in sub_capabilities:
            if sub in next_ancestry:
                # A capability decomposed into (partly) itself — the LLM's
                # decomposition was self-referential (observed in practice:
                # "generate_text" -> ["research", "generate_text"]). Recursing
                # would just re-derive the same cycle up to max_depth, wasting
                # real LLM calls at every level for no new information.
                # Resolve this one occurrence directly instead of recursing.
                logger.debug(
                    "[middleware] cycle detected composing %r: %r already in ancestry %r — resolving directly",
                    capability,
                    sub,
                    next_ancestry,
                )
                sub_answer, sub_meta = await self._composite_llm_step("generate", question, context_so_far, {})
                if sub_meta.get("failed"):
                    return "", {
                        "failed": True,
                        "error": f"sub-capability {sub!r} (cyclic) failed: {sub_meta.get('error', '')}",
                        "composed_from": sub_capabilities,
                        "trace": trace,
                    }
                trace.append({"capability": sub, "via": "llm_direct_cycle_guard"})
            else:
                sub_answer, sub_meta = await self._compose_capability(
                    sub,
                    question,
                    context_so_far,
                    depth + 1,
                    max_depth,
                    ancestry=next_ancestry,
                )
                if sub_meta.get("failed"):
                    return "", {
                        "failed": True,
                        "error": f"sub-capability {sub!r} failed: {sub_meta.get('error', '')}",
                        "composed_from": sub_capabilities,
                        "trace": trace,
                    }
                trace.extend(sub_meta.get("trace", []))
            if sub_answer:
                context_so_far = sub_answer

        return context_so_far, {
            "failed": False,
            "composed_from": sub_capabilities,
            "published": sub_capabilities[-1] in ("publish", "publish_markdown", "format_markdown"),
            "trace": trace,
        }

    async def _resolve_via_execution_mesh(
        self, capability: str, question: str, prior_context: str
    ) -> tuple[str, dict[str, Any]]:
        """Expand an existing reusable ExecutionGraph as a subgraph for this
        capability — the recursive-composition path: an unresolved
        capability can be a whole workflow the mesh already knows, not just
        a single missing agent. Reads the workflow's canonical Layer-2
        structure (Agents joined by name + DEPENDS_ON {graph_key} ordering
        — no per-run instance nodes involved), topo-sorts it, and executes
        each agent through the same known-agent resolution every planner
        node gets, threading each step's output into the next.
        """
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return "", {"failed": True, "error": "graph store unavailable"}
            match = await store.find_execution_graph_for_capability(capability)
            if not match or not match.get("graph_key"):
                return "", {"failed": True, "error": "no execution graph in mesh"}
            view = await store.get_execution_graph_view(str(match["graph_key"]))
            agent_names = [str(n.get("name", "")) for n in view.get("nodes", []) if n.get("name")]
            if not agent_names:
                return "", {"failed": True, "error": "mesh graph has no agents"}

            deps: dict[str, set[str]] = {name: set() for name in agent_names}
            for edge in view.get("edges", []):
                src, dst = str(edge.get("from", "")), str(edge.get("to", ""))
                if src in deps and dst in deps:
                    deps[dst].add(src)
            ordered: list[str] = []
            remaining = set(agent_names)
            while remaining:
                ready = sorted(name for name in remaining if not (deps[name] & remaining))
                if not ready:
                    ready = sorted(remaining)  # cycle — degrade to name order
                ordered.extend(ready)
                remaining -= set(ready)

            logger.info(
                "[middleware] expanding ExecutionGraph %r (%s) as subgraph for capability %r",
                match.get("goal"),
                match.get("graph_key"),
                capability,
            )
            context_so_far = prior_context
            executed: list[str] = []
            for agent_type in ordered:
                sub_answer, sub_meta = await self._resolve_known_agent_type(agent_type, question, context_so_far)
                if sub_meta.get("failed"):
                    return "", {
                        "failed": True,
                        "error": f"subgraph agent {agent_type!r} failed: {sub_meta.get('error', '')}",
                    }
                executed.append(agent_type)
                if sub_answer:
                    context_so_far = sub_answer
            if not executed:
                return "", {
                    "failed": True,
                    "error": "mesh graph had no executable agents",
                }
            return context_so_far, {
                "failed": False,
                "graph_key": match.get("graph_key"),
                "goal": match.get("goal"),
                "agents": executed,
            }
        except Exception as e:
            return "", {"failed": True, "error": f"mesh expansion failed: {e}"}

    @staticmethod
    async def _register_capability_provider(agent_name: str, capability: str) -> None:
        """Record a real provider's success into the mesh (see
        GraphStore.register_capability_provider). Best-effort — Neo4j being
        unavailable must never fail the request whose result it's recording.
        """
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return
            await store.register_capability_provider(agent_name, capability)
        except Exception as e:
            logger.debug(
                "[middleware] capability provider registration failed for %s/%s: %s",
                agent_name,
                capability,
                e,
            )

    async def _lookup_capability_composition(self, capability: str) -> list[str] | None:
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return None
            return await store.get_capability_composition(capability)
        except Exception as e:
            logger.debug("[middleware] capability mesh lookup failed for %r: %s", capability, e)
            return None

    async def _persist_capability_composition(self, capability: str, sub_capabilities: list[str]) -> None:
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return
            await store.save_capability_composition(capability, sub_capabilities)
        except Exception as e:
            logger.debug("[middleware] capability mesh persist failed for %r: %s", capability, e)

    @staticmethod
    async def _decompose_capability_via_llm(capability: str, question: str) -> list[str]:
        """Ask an LLM what ordered sub-capabilities are needed to satisfy a
        capability nothing could resolve directly. Returns [capability]
        unchanged (the caller's signal for "atomic, don't recurse further")
        on any failure or when the LLM says it can't be broken down further.
        """
        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
            if generator._llm is None:
                return [capability]
        except Exception:
            return [capability]

        system = (
            "You decompose a capability into the minimal ordered sequence of sub-capabilities "
            "needed to satisfy it. Each sub-capability must be a short, reusable, domain-general "
            "name in snake_case (e.g. 'research', 'generate_document', 'review_text', "
            "'publish_markdown', 'lookup_customer', 'send_notification') — never a specific agent "
            "or product name. Return ONLY a JSON array of strings, in execution order, nothing else."
        )
        prompt = (
            f"Capability needed: {capability}\n"
            f"Original request this capability must help satisfy: {question}\n\n"
            f"What ordered sub-capabilities are needed to satisfy '{capability}'? "
            f"If it is already atomic — cannot be meaningfully broken down further — return "
            f'a single-element array containing just "{capability}" unchanged.'
        )
        try:
            raw = await generator._llm.generate(prompt, system=system)
            import re

            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if not m:
                return [capability]
            steps = json.loads(m.group(0))
            steps = [str(s).strip() for s in steps if str(s).strip()]
            return steps or [capability]
        except Exception as e:
            logger.debug("[middleware] LLM decomposition failed for %r: %s", capability, e)
            return [capability]

    # Phrases a small local model tends to open a *conversational reply*
    # with instead of doing the requested transformation — e.g. asked to
    # "return the revised text," it sometimes just praises the input
    # ("Excellent! That's exactly what I was looking for...") instead of
    # revising it. A transform step's output starting with one of these,
    # especially if much shorter than its input, is commentary, not a
    # result — quarantined by _looks_like_commentary rather than passed
    # downstream, where it would silently overwrite real content.
    _COMMENTARY_OPENERS = (
        "excellent",
        "great",
        "i understand",
        "sure",
        "certainly",
        "okay",
        "sounds good",
        "thank you",
        "thanks",
        "of course",
        "here is",
        "here's",
        "got it",
        "i've",
    )

    @classmethod
    def _looks_like_commentary(cls, text: str, input_text: str) -> bool:
        """True if a transform step's output reads like a reply about the
        input rather than a transformed version of it."""
        stripped = text.strip().lower()
        if not stripped:
            return False
        opens_conversationally = any(stripped.startswith(p) for p in cls._COMMENTARY_OPENERS)
        much_shorter = input_text and len(text) < len(input_text) * 0.5
        return opens_conversationally and much_shorter

    async def _composite_llm_step(
        self, role: str, question: str, prior_context: str, knowledge: dict
    ) -> tuple[str, dict[str, Any]]:
        """One LLM-backed step of a composite pipeline (generate/review/publish).

        Never has access to anything beyond the prior step's real output and
        the original question — it cannot fabricate research it wasn't
        given, only write/edit/format what's actually there. review/publish
        steps that come back as conversational commentary rather than an
        actual transform (see _looks_like_commentary) fall back to passing
        the untransformed prior_context through unchanged, rather than
        corrupting the pipeline with a reply that isn't the deliverable.
        """
        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
            if generator._llm is None:
                return "", {"failed": True, "error": "no LLM provider configured"}
        except Exception as e:
            return "", {"failed": True, "error": f"LLM provider unavailable: {e}"}

        if role == "generate":
            system = "You write complete, well-structured long-form content. Produce the actual content directly — this response IS the deliverable, never a reply about it."
            prompt = f"Request: {question}\n\n"
            if prior_context:
                prompt += f"Research/background gathered for this request:\n{prior_context}\n\n"
            if knowledge.get("context"):
                prompt += f"Additional system context — use only if genuinely relevant to the topic, ignore otherwise:\n{knowledge['context']}\n\n"
            prompt += "Write the complete content now. Output the content itself — no preamble, no commentary about it."
        elif role == "review":
            system = (
                "You are a text-transformation tool, not a conversational assistant. Input: a draft. "
                "Output: ONLY the same draft with grammar, clarity, and flow fixed — same length "
                "ballpark, same content. Never reply about the draft, never praise it, never add "
                "commentary before or after. If it's already fine, output it unchanged."
            )
            prompt = f"Transform this draft in place — output the corrected draft only:\n\n{prior_context}"
        elif role == "publish":
            system = (
                "You are a text-transformation tool, not a conversational assistant. Input: text. "
                "Output: ONLY that same text reformatted as clean Markdown (headings, paragraphs, "
                "emphasis where appropriate) — same content, nothing added, nothing removed, no "
                "commentary before or after."
            )
            prompt = f"Reformat this text as Markdown — output the formatted document only:\n\n{prior_context}"
        else:
            return "", {"failed": True, "error": f"unknown composite LLM role {role!r}"}

        try:
            result = await generator._llm.generate(prompt, system=system)
        except Exception as e:
            return "", {"failed": True, "error": f"LLM call failed: {e}"}
        result = (result or "").strip()
        if not result:
            return "", {"failed": True, "error": "LLM returned empty content"}

        if role in ("review", "publish") and self._looks_like_commentary(result, prior_context):
            logger.warning(
                "[middleware] composite %s step returned commentary instead of a transform — passing prior content through unchanged",
                role,
            )
            return prior_context, {"failed": False, "passthrough": True}

        return result, {"failed": False}

    def _search_repositories(self, question: str, max_entities_per_repo: int = 20) -> str:
        """Real keyword search over RepositoryManager's actual stored data
        (~/.monkeybrain/world_state/*.json) — the one genuinely working
        database in this system. No repository named "KnowledgeAgent" or
        similar has ever existed; this is the real "data search" a
        knowledge_management/research capability should mean, not an
        advisory LLM guess.
        """
        terms = {w for w in question.lower().split() if len(w) > 2}
        if not terms:
            return ""
        hits: list[str] = []
        for repo_name in self.repository_manager.list_repositories():
            for entity in self.repository_manager.list_entities(repo_name)[:max_entities_per_repo]:
                blob = json.dumps(entity, default=str).lower()
                if any(term in blob for term in terms):
                    hits.append(f"[{repo_name}] {json.dumps(entity, default=str)[:300]}")
        return "\n".join(hits[:10])

    async def _resolve_composite_subcapability(
        self, group: str, question: str, prior_context: str
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a composite step that's a real capability group (not an
        LLM role) through the normal provider hierarchy — e.g. the
        "research" step of content_generation resolving through
        knowledge_management, exactly as a top-level request to that
        capability would.

        knowledge_management specifically tries a real database search
        first (see _search_repositories) — its nominal implementation,
        "KnowledgeAgent", has never existed as a real agent, so without
        this every "research" step silently fell through to an
        ungrounded CodeGen guess instead of ever checking real stored data.

        AgentResolver.resolve() always returns *something* — CodeGenAgent
        (advisory-only, synthetic=True) is its guaranteed non-None last
        resort — so "agent is not None" is never a valid success signal on
        its own; a fabricated sub-capability name like "generate_textAgent"
        would silently "succeed" via CodeGen's improvised, ungrounded
        prose every time otherwise, defeating the entire point of
        recursing into further decomposition for a capability nothing
        real actually satisfies. success/executable=True (a real,
        non-synthetic provider) is the only thing this treats as resolved.

        Coming up empty (executable but no answer text) is reported but not
        treated as a hard failure by the caller for the "research" step
        specifically — research legitimately can find nothing, and the
        pipeline should still attempt generate/review/publish rather than
        aborting outright.

        Checks the capability mesh for a previously-confirmed provider
        before running the full discovery cascade (ARD search, OpenClaw,
        n8n GET, ...) — the mesh is what makes a second request for the
        same capability fast: the first request pays for real discovery,
        every later one hits the index instead of re-deriving it. Falls
        through to full discovery if the mesh has nothing, or if the
        previously-confirmed agent no longer resolves.
        """
        if group == "knowledge_management":
            db_hits = self._search_repositories(question)
            if db_hits:
                return db_hits, {
                    "failed": False,
                    "implementation": "repository_manager_search",
                }

        try:
            from src.monkey_brain.runtime.agent_resolver import get_resolver

            resolver = get_resolver()

            known_agent_name = await self._lookup_known_provider(group)
            if known_agent_name:
                agent = await resolver.resolve(known_agent_name, allow_create=False, question=question)
                if agent is not None:
                    result = await agent.execute({"question": question, "capability_group": group})
                    cap_result = self._extract_capability_result(result)
                    is_synthetic = (
                        bool(cap_result.metadata.get("synthetic")) or cap_result.metadata.get("source") == "codegen"
                    )
                    if not is_synthetic and cap_result.success:
                        return (cap_result.response.answer or ""), {
                            "failed": False,
                            "implementation": known_agent_name,
                            "via_mesh_index": True,
                        }
                    # Mesh pointed at something that no longer actually works
                    # (e.g. an n8n workflow that got deleted/deactivated) —
                    # fall through to full discovery below rather than
                    # trusting a stale index entry.

            impl = self.resolver.resolve(group)
            agent = await resolver.resolve(
                impl.implementation,
                allow_create=impl.implementation_type != "generated",
                question=question,
            )
            if agent is None:
                return "", {
                    "failed": True,
                    "error": f"no implementation for {impl.implementation}",
                    "implementation": impl.implementation,
                }
            result = await agent.execute({"question": question, "capability_group": group})
        except Exception as e:
            return "", {"failed": True, "error": str(e)}

        cap_result = self._extract_capability_result(result)
        is_synthetic = bool(cap_result.metadata.get("synthetic")) or cap_result.metadata.get("source") == "codegen"
        if is_synthetic or not cap_result.success:
            return "", {
                "failed": True,
                "error": cap_result.metadata.get("error", "resolved only to an advisory/synthetic answer"),
                "implementation": impl.implementation,
            }
        # Full discovery found a real provider — index it so the *next*
        # request for this capability hits _lookup_known_provider above
        # instead of paying for ARD/OpenClaw/n8n discovery again.
        await self._register_capability_provider(impl.implementation, group)
        return (cap_result.response.answer or ""), {
            "failed": False,
            "implementation": impl.implementation,
        }

    @staticmethod
    async def _lookup_known_provider(capability: str) -> str | None:
        """Most-recently-confirmed real agent for this capability, from the
        mesh — None if the mesh has no record (first time this capability
        has ever been resolved) or Neo4j is unavailable."""
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return None
            providers = await store.find_capability_providers(capability)
            return providers[0]["agent"] if providers else None
        except Exception as e:
            logger.debug("[middleware] known-provider lookup failed for %r: %s", capability, e)
            return None

    async def _resolve_known_agent_type(
        self, agent_type: str, question: str, prior_context: str
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a real Broca agent_type directly (no CapabilityResolver
        group indirection) — used for generalization targets, which are
        already-real agent names (e.g. "appointment"), not capability
        groups. Same non-synthetic-only success bar as
        _resolve_composite_subcapability, for the same reason: CodeGen's
        guaranteed-non-None advisory fallback must never read as "resolved."
        """
        try:
            from src.monkey_brain.runtime.agent_resolver import get_resolver

            resolver = get_resolver()
            agent = await resolver.resolve(agent_type, allow_create=False, question=question)
            if agent is None:
                return "", {
                    "failed": True,
                    "error": f"no implementation for {agent_type}",
                }
            result = await agent.execute({"question": question, "prior_context": prior_context})
        except Exception as e:
            return "", {"failed": True, "error": str(e)}

        cap_result = self._extract_capability_result(result)
        is_synthetic = bool(cap_result.metadata.get("synthetic")) or cap_result.metadata.get("source") == "codegen"
        if is_synthetic or not cap_result.success:
            return "", {
                "failed": True,
                "error": cap_result.metadata.get("error", "resolved only to an advisory/synthetic answer"),
                "implementation": agent_type,
            }
        return (cap_result.response.answer or ""), {
            "failed": False,
            "implementation": agent_type,
        }

    @staticmethod
    async def _lookup_generalization(capability: str) -> str | None:
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return None
            return await store.find_generalization(capability)
        except Exception as e:
            logger.debug("[middleware] generalization lookup failed for %r: %s", capability, e)
            return None

    @staticmethod
    async def _link_capability_generalization(specific: str, general: str) -> None:
        try:
            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return
            await store.link_capability_generalization(specific, general)
        except Exception as e:
            logger.debug(
                "[middleware] generalization link failed for %r -> %r: %s",
                specific,
                general,
                e,
            )

    @staticmethod
    async def _discover_generalization(capability: str, question: str) -> str | None:
        """Is `capability` (a planner-invented name with no real
        implementation, e.g. "DoctorAvailabilityAgent") actually just a
        specific instance of an existing real Broca agent (e.g.
        "appointment")? Narrows Broca's ~250-agent catalog to a cheap
        keyword-overlap shortlist first (asking an LLM to consider the
        whole catalog every time would be slow and noisy), then has the LLM
        pick genuinely — returning None rather than a strained guess is the
        expected, common answer; most granular names have no real
        counterpart yet.
        """
        try:
            from broca.registry import get_registry

            registry = get_registry()
            all_types = registry.agent_types()
        except Exception:
            return None
        if not all_types:
            return None

        # PascalCase word boundaries only exist before lowercasing —
        # "AppointmentSlotAgent".lower() is "appointmentslotagent", one
        # undifferentiated blob that will never substring-match "appointment"
        # (it's *longer* than "appointment", not a match in either
        # direction). Split on case boundaries first, exactly like
        # runtime.py's own humanization of planner node names.
        humanized_capability = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", capability)
        words = {w for w in re.findall(r"[a-z0-9]+", humanized_capability.lower()) if len(w) > 2}
        words |= {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
        shortlist = [t for t in all_types if any(w in t.replace("_", "") for w in words)]
        if not shortlist:
            return None
        shortlist = shortlist[:25]  # keep the LLM prompt small

        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
            if generator._llm is None:
                return None
            system = (
                "You determine whether a specific, invented capability name is actually just a "
                "special case of an existing, real capability. Be conservative — most of the "
                "time there is genuinely no match, and guessing wrong routes real requests to "
                "the wrong implementation. Respond with ONLY the exact matching name from the "
                'candidate list, or the single word "none" if nothing genuinely applies — no '
                "other text."
            )
            prompt = (
                f"Specific capability needed: {capability!r}\n"
                f"Request it came from: {question!r}\n\n"
                f"Candidate existing real capabilities: {shortlist}\n\n"
                f"Is {capability!r} a specific instance of exactly one of these candidates? "
                'If yes, respond with that candidate\'s exact name. If no genuine match, respond "none".'
            )
            raw = (await generator._llm.generate(prompt, system=system)).strip().strip('"').strip("'")
        except Exception as e:
            logger.debug(
                "[middleware] generalization discovery LLM call failed for %r: %s",
                capability,
                e,
            )
            return None

        if raw.lower() == "none" or raw not in shortlist:
            return None
        return raw

    @staticmethod
    async def _generate_via_llm(question: str, capability: str, knowledge: dict) -> CapabilityResult:
        """Fulfill a generative capability (writing, summarizing, translating,
        ...) directly via an LLM provider.

        There is no real agent/workflow/DB row to resolve for these — the
        LLM's own output is the actual deliverable, not a stand-in for a
        missing implementation, so success=True here means "the LLM produced
        the requested content," not "an ungrounded guess was allowed through."
        Fails honestly, naming the missing/broken piece, if no LLM provider
        is reachable or it returns nothing — never fabricates a fake success.
        """
        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
        except Exception as e:
            logger.warning(
                "[middleware] no LLM provider available for generative capability %r: %s",
                capability,
                e,
            )
            return CapabilityResult(
                success=False,
                response=Response(answer=f"No LLM provider is available to fulfill this request ({capability})."),
                metadata={
                    "executable": False,
                    "generative": True,
                    "unimplemented": True,
                    "blocker": f"LLM provider unavailable: {e}",
                },
            )
        if generator._llm is None:
            return CapabilityResult(
                success=False,
                response=Response(answer=f"No LLM provider is available to fulfill this request ({capability})."),
                metadata={
                    "executable": False,
                    "generative": True,
                    "unimplemented": True,
                    "blocker": "no LLM provider configured",
                },
            )

        system = (
            f"You are fulfilling a generative request (capability: {capability}). "
            "Produce the actual requested content directly and completely — this response "
            "IS the final deliverable, not a description of what you would do or a summary "
            "of your plan."
        )
        if knowledge.get("context"):
            system += f"\n\nRelevant context, if useful:\n{knowledge['context']}"

        try:
            content = await generator._llm.generate(question, system=system)
        except Exception as e:
            logger.warning("[middleware] generative LLM call failed for %r: %s", capability, e)
            return CapabilityResult(
                success=False,
                response=Response(answer=f"The LLM provider failed to fulfill this request ({capability})."),
                metadata={
                    "executable": False,
                    "generative": True,
                    "unimplemented": False,
                    "blocker": f"LLM call failed: {e}",
                },
            )

        content = (content or "").strip()
        if not content:
            return CapabilityResult(
                success=False,
                response=Response(answer=f"The LLM provider returned no content for this request ({capability})."),
                metadata={
                    "executable": False,
                    "generative": True,
                    "unimplemented": False,
                    "blocker": "LLM returned empty content",
                },
            )

        return CapabilityResult(
            success=True,
            response=Response(answer=content, format="text"),
            operations=[],
            metadata={
                "executable": True,
                "generative": True,
                "generated_by": "llm",
                "synthetic": False,
                "grounded": bool(knowledge.get("context")),
                "knowledge_sources": [s["pack"] for s in knowledge.get("sources", [])],
                "grounding_confidence": knowledge.get("grounding_confidence", 0.0),
            },
        )

    @staticmethod
    async def _reformat_answer_with_llm(question: str, result: CapabilityResult) -> str | None:
        """Turn a real capability's raw, structured output into a readable
        answer. Presentation only — the LLM is shown exactly what the
        capability returned (never anything else) and instructed to use
        only those facts, so this cannot introduce content the underlying
        capability/repository didn't actually provide. Returns None (leave
        the original answer alone) on any failure, empty input, or an
        answer that already reads as prose rather than raw data.
        """
        raw = result.metadata.get("raw_output")
        if raw is None:
            raw = result.response.answer
        if raw is None or raw == "" or raw == {} or raw == []:
            return None
        # Already-prose answers (CodeGen advisory, an agent that composed
        # its own sentence) don't need reformatting — a short heuristic
        # rather than an LLM call for the common case, since most agent
        # answers are either short strings or structured data, rarely both.
        if isinstance(raw, str) and len(raw.split()) > 6 and not raw.strip().startswith(("{", "[")):
            return None

        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
            if generator._llm is None:
                return None
            raw_text = raw if isinstance(raw, str) else json.dumps(raw, default=str)[:4000]
            response = await generator._llm.generate(
                f"Question: {question}\n\nRaw result from a real system capability:\n{raw_text}\n\n"
                "Rewrite this raw result as a single clear, natural-language answer to the "
                "question. Use only facts present in the raw result — never invent, assume, or "
                "add anything not there. If the raw result is empty or doesn't actually answer "
                "the question, say so plainly rather than making something up.",
                system="You reformat structured data into a human-readable answer. You never invent facts beyond what's given.",
            )
            reformatted = response.strip()
            return reformatted or None
        except Exception as e:
            logger.debug("[middleware] LLM reformat failed: %s", e)
            return None

    def _emit_telemetry(
        self,
        intent: IntentType | None,
        knowledge: dict,
        result: CapabilityResult,
        latency_ms: float,
    ) -> None:
        """Emit telemetry for the execution. Domain-agnostic.

        `intent` is None when the failure preceded intent detection.
        """
        try:
            from src.monkey_brain.runtime.telemetry import get_telemetry

            telemetry = get_telemetry()
            trace = telemetry.start_trace(f"{self.agent_name}_{int(time.time())}")
            trace.intent = intent.value if intent else ""
            trace.knowledge_sources = [s["pack"] for s in knowledge.get("sources", [])]
            trace.grounding_confidence = knowledge.get("grounding_confidence", 0.0)
            trace.response = result.to_dict()
            trace.finish()
        except Exception as e:
            logger.debug("[middleware] Telemetry failed: %s", e)
