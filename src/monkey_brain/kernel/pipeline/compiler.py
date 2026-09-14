"""Pipeline compiler — transforms PipelineRequest into CompiledRequest.

Compiler pipeline:
    PipelineRequest
        → resolve_intent()
        → resolve_goal()
        → build_intent_ir()
        → build_goal_ir()
        → compile_execution_graph()  [optional, needs belief]
        → CompiledRequest

The compiler is:
- Deterministic: same input → same output
- Stateless: no instance state beyond config
- Side-effect free: no world mutation, no runtime access
- Idempotent: safe to call multiple times

It depends only on compilation components (intent classification,
goal resolution, IR builders). It never touches runtime services.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.contracts import (
    CompiledRequest,
    PipelineRequest,
)

logger = logging.getLogger("agentos.pipeline.compiler")


class CompilationError(Exception):
    """Structured compilation failure.

    Attributes:
        code: Machine-readable error code
        step: Pipeline step where the error occurred
        details: Additional context
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        step: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.step = step
        self.details = details or {}


class RequestCompiler:
    """Compiles a PipelineRequest into a CompiledRequest.

    Orchestrates existing compilation components without owning them.
    Stateless — all state lives in the request and compilation outputs.
    """

    def __init__(self, *, actor_id: str = "compiler") -> None:
        self._actor_id = actor_id

    async def compile(
        self,
        request: PipelineRequest,
        *,
        belief: Any = None,
    ) -> CompiledRequest:
        """Compile a PipelineRequest into a CompiledRequest.

        Args:
            request: The incoming pipeline request
            belief: Optional SparseTransitionTensor for execution graph compilation.
                    If None, execution_graph will be None in the output.

        Returns:
            Fully populated CompiledRequest

        Raises:
            CompilationError: If any compilation step fails
        """
        start = time.time()

        # Step 1: Resolve intent
        intent = self._resolve_intent(request)

        # Step 2: Resolve goal
        goal = self._resolve_goal(request, intent)

        # Step 3: Compile IntentIR (signed, versioned)
        intent_ir = self._compile_intent_ir(request, intent, goal)

        # Step 4: Compile GoalIR (domain, entities, constraints)
        goal_ir = self._compile_goal_ir(request, intent, goal)

        # Step 5: Compile ExecutionContext
        execution_context = self._compile_execution_context(request, intent_ir)

        # Step 6: Compile ExecutionGraph (optional — needs belief tensor)
        execution_graph = None
        if belief is not None:
            execution_graph = self._compile_execution_graph(
                goal_ir,
                belief,
            )

        compilation_ms = (time.time() - start) * 1000

        return CompiledRequest(
            request=request,
            intent=intent,
            goal=goal,
            intent_ir=intent_ir,
            goal_ir=goal_ir,
            execution_context=execution_context,
            execution_graph=execution_graph,
            metadata={
                "compilation_ms": round(compilation_ms, 2),
                "actor_id": self._actor_id,
                "has_graph": execution_graph is not None,
            },
        )

    # ── Step 1: Intent Resolution ─────────────────────────────────────────

    def _resolve_intent(self, request: PipelineRequest) -> dict:
        """Classify the question's intent via predicate-based lookup.

        Returns:
            Intent dict with keys: intent, confidence, workload_id

        Raises:
            CompilationError: If no intent can be classified
        """
        from src.monkey_brain.kernel.execute.orchestration.routing import resolve_intent
        from src.shared.config import ACTOR_CONFIG

        # Check IR cache first
        try:
            from src.monkey_brain.persistence.ir_cache import get_ir_cache

            cached = get_ir_cache().get_intent_ir(request.question)
            if cached is not None:
                return cached
        except Exception:
            logger.debug(
                "_resolve_intent: suppressed exception", exc_info=True
            )  # Cache unavailable — proceed with classification

        result = resolve_intent(request.question)

        # Auto-register unseen domains if configured
        if result is None and ACTOR_CONFIG.get("auto_register_unseen_domains"):
            result = self._try_auto_register(request.question)

        if result is None:
            raise CompilationError(
                code="INTENT_CLASSIFICATION_FAILED",
                message=f"Cannot classify intent for: '{request.question[:100]}'",
                step="resolve_intent",
            )

        return result

    def _try_auto_register(self, question: str) -> dict | None:
        """Attempt to auto-register an unseen domain intent."""
        try:
            from src.shared.config import DOMAIN_HINTS
            from src.monkey_brain.kernel.plan.intents.intent_registry import (
                register_domain_intent,
            )

            question_lower = question.lower()
            for token in question_lower.split():
                if token in DOMAIN_HINTS:
                    detected_domain, detected_entity = DOMAIN_HINTS[token]
                    route = register_domain_intent(detected_domain, detected_entity)
                    if route:
                        return {
                            "intent": route.intent,
                            "confidence": 0.8,
                            "workload_id": route.intent,
                        }
        except Exception as e:
            logger.debug("[compiler] auto-register failed: %s", e)
        return None

    # ── Step 2: Goal Resolution ───────────────────────────────────────────

    def _resolve_goal(self, request: PipelineRequest, intent: dict) -> Any:
        """Resolve the goal from the classified intent.

        Returns:
            Goal object with name, description, constraints, confidence

        Raises:
            CompilationError: If goal cannot be resolved
        """
        from src.monkey_brain.kernel.execute.orchestration.routing import (
            resolve_goal_from_intent,
        )

        try:
            result = resolve_goal_from_intent(request.question, intent)
        except Exception as e:
            raise CompilationError(
                code="GOAL_RESOLUTION_FAILED",
                message=f"Goal resolution failed: {e}",
                step="resolve_goal",
            ) from e

        if result is None:
            # Fallback to default goal
            return _DefaultGoal(
                name="world_graph_traversal",
                description="",
                required_inputs=[],
                required_outputs=[],
                constraints={},
                confidence=0.5,
            )

        return result

    # ── Step 3: IntentIR Compilation ──────────────────────────────────────

    def _compile_intent_ir(
        self,
        request: PipelineRequest,
        intent: dict,
        goal: Any,
    ) -> Any:
        """Compile a signed, versioned IntentIR.

        Returns:
            Frozen IntentIR dataclass
        """
        from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir

        return build_intent_ir(
            intent=intent,
            goal={
                "name": getattr(goal, "name", "unknown"),
                "description": getattr(goal, "description", ""),
            },
            question=request.question,
            run_id=request.request_id,
        )

    # ── Step 4: GoalIR Compilation ────────────────────────────────────────

    def _compile_goal_ir(
        self,
        request: PipelineRequest,
        intent: dict,
        goal: Any,
    ) -> Any:
        """Compile GoalIR with domain, entities, and constraints.

        Extracts domain detection and entity/constraint extraction from
        the existing IRBuilder logic without requiring CognitiveRuntime.
        """
        from src.shared.domain_models import Entity, Constraint, Relationship
        from src.shared.config import DOMAIN_KEYWORDS, DEFAULT_CONSTRAINTS, DOMAIN_HINTS
        from src.monkey_brain.persistence.ir_cache import get_ir_cache

        # Check cache
        ir_cache = get_ir_cache()
        intent_type = intent.get("intent", "world_graph_traversal") if intent else "unknown"
        cached_goal = ir_cache.get_goal_ir(request.question, intent_type)
        if cached_goal is not None:
            return cached_goal

        entities: list[Entity] = []
        constraints: list[Constraint] = []
        relationships: list[Relationship] = []
        question_lower = request.question.lower()

        # Detect domain from keywords
        domain = "general"
        for entity_name, keywords in DOMAIN_KEYWORDS.items():
            if any(kw in question_lower for kw in keywords):
                entities.append(Entity(name=entity_name, entity_type="unknown"))
                if entity_name in DOMAIN_HINTS:
                    domain = DOMAIN_HINTS[entity_name][0] or domain
                break

        # Add default constraints. Each DEFAULT_CONSTRAINTS entry (src/
        # shared/config.py) is shaped {"type": ..., <limit_key>: ...} —
        # e.g. {"type": "time", "max_duration": 3600} — never a literal
        # "value" key, so the limit is whichever key isn't "type".
        for c in DEFAULT_CONSTRAINTS:
            limit_value = next(v for k, v in c.items() if k != "type")
            constraints.append(
                Constraint(
                    constraint_type=c["type"],
                    value=limit_value,
                )
            )

        # Handle unseen domains
        is_unseen = domain == "general" and not entities
        web_search_results = None

        if is_unseen:
            web_search_results = self._explore_unseen_domain(
                request.question,
                domain,
                entities,
            )
            if web_search_results and web_search_results.get("domain"):
                domain = web_search_results["domain"]
                is_unseen = False
            else:
                raise CompilationError(
                    code="UNSEEN_DOMAIN",
                    message=f"No matching domain found for: '{request.question[:100]}'",
                    step="compile_goal_ir",
                )

        goal_ir = _GoalIR(
            intent_type=intent_type,
            domain=domain,
            goal=request.question,
            entities=entities,
            constraints=constraints,
            relationships=relationships,
            metadata={
                "is_unseen": is_unseen,
                "web_search_results": web_search_results,
            },
        )

        # Cache the result
        ir_cache.set_goal_ir(request.question, intent_type, domain, goal_ir)

        return goal_ir

    def _explore_unseen_domain(
        self,
        question: str,
        domain: str,
        entities: list,
    ) -> dict | None:
        """Explore knowledge base for unseen domains.

        Falls back to generic transitions if knowledge base is unavailable.
        """
        entity_name = entities[0].name if entities else "item"

        try:
            from src.monkey_brain.kernel.plan.intents.intent_registry import (
                get_somatic_compiler,
            )

            compiler = get_somatic_compiler()
            if compiler is not None and hasattr(compiler, "search"):
                knowledge = compiler.search(question)
                results = knowledge if isinstance(knowledge, list) else []
                if results:
                    from src.shared.config import GENERIC_FALLBACK_TRANSITIONS

                    transitions = [
                        (
                            s.format(entity=entity_name),
                            d.format(entity=entity_name),
                            dom.format(domain=domain),
                        )
                        for s, d, dom in GENERIC_FALLBACK_TRANSITIONS
                    ]
                    return {
                        "domain": domain,
                        "source": "knowledge_base",
                        "confidence": 0.7,
                        "transitions_generated": True,
                        "transitions": transitions,
                    }
        except Exception as e:
            logger.debug("[compiler] knowledge base exploration failed: %s", e)

        return {
            "domain": domain,
            "source": "fallback",
            "confidence": 0.5,
            "transitions_generated": True,
        }

    # ── Step 5: ExecutionContext Compilation ───────────────────────────────

    def _compile_execution_context(
        self,
        request: PipelineRequest,
        intent_ir: Any,
    ) -> Any:
        """Create an immutable ExecutionContext for this compilation."""
        from src.monkey_brain.kernel.execute.context import ExecutionContext
        from src.monkey_brain.kernel.execute.models import ExecutionMode

        return ExecutionContext.create(
            run_id=request.request_id,
            execution_mode=ExecutionMode.EXECUTE,
            intent_ir=intent_ir,
            user_id=request.actor_id,
            trace_id=request.request_id,
            request_metadata={
                "tenant_id": request.tenant_id,
                "session_id": request.session_id,
            },
        )

    # ── Step 6: ExecutionGraph Compilation (optional) ─────────────────────

    def _compile_execution_graph(
        self,
        goal_ir: Any,
        belief: Any,
    ) -> Any | None:
        """Compile an ExecutionGraph from the goal IR.

        TECHNICAL DEBT: The current implementation requires a belief tensor
        because RuntimeConstraintEngine.plan() was designed around belief-
        weighted beam search. The graph should represent WHAT could be
        executed (derived from the goal), not what the current belief
        state predicts. Belief should influence planning and execution,
        not graph construction.

        This step is optional — only runs when a belief tensor is provided.
        When the graph builder is decoupled from belief, this parameter
        should be removed and graph compilation should always run.
        """
        try:
            from src.monkey_brain.kernel.compile.constraints import (
                RuntimeConstraintEngine,
                PlanConstraints,
            )
            from src.shared.config import PLANNING_CONFIG, ENTITY_TO_STATE

            domain = getattr(goal_ir, "domain", "general")
            web_search_results = getattr(goal_ir, "metadata", {}).get("web_search_results") or {}
            has_llm = web_search_results.get("transitions_generated", False)

            # Determine start and goal states
            start_state = self._find_domain_start_state(domain, belief)
            goal_state = "task_complete"

            if has_llm and web_search_results.get("transitions"):
                llm = web_search_results["transitions"]
                start_state, goal_state = llm[0][0], llm[-1][1]
            elif getattr(goal_ir, "entities", None):
                name = goal_ir.entities[0].name
                start_state = ENTITY_TO_STATE.get(name, f"search_{name}")
                if start_state not in belief.states():
                    start_state = self._find_domain_start_state(domain, belief)

            planner = RuntimeConstraintEngine(self._actor_id)
            constraints = PlanConstraints(
                budget=PLANNING_CONFIG["budget"],
                max_steps=PLANNING_CONFIG["max_steps"],
            )
            graph = planner.plan(start_state, goal_state, belief, constraints=constraints)
            return graph

        except Exception as e:
            logger.warning("[compiler] ExecutionGraph compilation failed: %s", e)
            return None

    def _find_domain_start_state(self, domain: str, belief: Any) -> str:
        """Find the start state for a domain in the belief tensor."""
        from src.shared.config import DOMAIN_START_STATES

        start = DOMAIN_START_STATES.get(domain)
        if not start or start not in belief.states():
            for s in belief.states():
                if belief.domain_of(s) == domain:
                    start = s
                    break
        if not start or start not in belief.states():
            start = DOMAIN_START_STATES.get("shopping", "task_complete")
        return start


# ── Internal Types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _DefaultGoal:
    """Fallback goal when resolution fails."""

    name: str = "world_graph_traversal"
    description: str = ""
    required_inputs: list = field(default_factory=list)
    required_outputs: list = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    confidence: float = 0.5


@dataclass(frozen=True)
class _GoalIR:
    """Internal GoalIR — same shape as actor.layers.GoalIR."""

    intent_type: str
    domain: str
    goal: str
    entities: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
