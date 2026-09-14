"""LegacyCognitiveRuntime — the Runtime Gateway for /plan, /execute, /knowledge, /predict.

Step 13.1 (ACP-1): renamed from CognitiveRuntime to LegacyCognitiveRuntime to
end a naming collision with the canonical
`kernel/pipeline/belief_runtime.py::CognitiveRuntime` (a same-named, unrelated
class — the two have never called each other and are not to be confused;
kernel.py's boot sequence has its own comment flagging this). This is a pure
rename, not a merge: THIS class is genuinely live production code, the
Runtime Gateway for request validation, runtime selection, routing,
orchestration, and response normalization for the four routes above, plus
/query indirectly — not dead weight, not mergeable into the canonical
per-actor engine without a separate feature project (different method
surface entirely: compile_intent/build_execution_runtime/
execute_cognitive_workload vs. the canonical engine's tick()). A
`CognitiveRuntime = LegacyCognitiveRuntime` alias at the bottom of this file
keeps every existing import of the old name working unchanged; only
kernel.py's boot phase was updated to reference the new name explicitly.
Its public contract does not change — internal refactoring toward
delegating cognitive-shaped work to the canonical engine happens by wrapping
(old method signature/return shape preserved, internals call the real
implementation), not by rewriting the routes themselves.

OS-process analogy this codebase follows explicitly:

    Process              → LegacyCognitiveRuntime (this class — one per request)
    Process Context      → ExecutionContext    (execute/context.py)
    Executable           → IntentIR             (plan/goals/intent_ir.py)

Lifecycle (mirrors compiling a program, loading it into a process, running it):

    Cognitive Runtime
        |
    Compile Intent             compile_intent()             question -> signed IntentIR
        |
    Build Execution Runtime    build_execution_runtime()     IntentIR -> ExecutionContext
        |
    Execute Cognitive Workload execute_cognitive_workload()  ExecutionContext -> result

Every entry point that used to build IntentIR/ExecutionContext inline
(/plan, /execute, /replay, UnifiedExecutor) goes through this class instead,
so there is exactly one place that knows how a question becomes a compiled,
signed, executed plan. Only execute_cognitive_workload() does anything —
compile_intent() and build_execution_runtime() are pure compilation, no
side effects on the world.

Not to be confused with CognitiveKernel (cognitive_kernel.py) — that's the
EPA-loop epistemic optimizer (predict/learn over many steps of a running
session). LegacyCognitiveRuntime is the per-request compile-and-execute
pipeline for turning one question into one validated execution.

Architecture:
    LegacyCognitiveRuntime is a thin façade composing focused components:
    - RuntimeBootstrap: lifecycle, dependency injection
    - IntentCompiler: intent/goal compilation
    - ExecutionCoordinator: workload execution, persistence, replay
    - CognitiveLoop: iterative learning loop (plan/simulate/act/compare/learn)
    - KnowledgeManager: knowledge base exploration and acquisition
    - WorldCoordinator: world mutation coordination
    - ObservationPipeline: sensor fusion, world estimation
    - AuditService: audit logging, governance checks

Step 12.0: the deprecated LegacyActorAdapter-backed actor-network methods
(init_actor_network, create_actor, connect_actors, get_actor,
get_actor_network, run_actor, run_all_actors, and their private world-graph
helpers) were removed here — confirmed zero live callers via
app.state.cognitive_runtime (the only apparent call sites, in
api/routes/cognitive.py, operate on a different, separately-dead-in-
production object, app.state.cognitive_architecture, which is never set
outside tests). See docs/architecture-hierarchical-runtime.md.
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.execute.orchestration.routing import (
    resolve_goal_from_intent,
)
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR
from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.compile.runtime_interface import (
    CognitiveRuntimeInterface,
    SocietyRuntimeInterface,
)

logger = logging.getLogger("agentos.cognitive_runtime")

_execution_graph: ContextVar[Any] = ContextVar("cognitive_runtime_execution_graph", default=None)


@dataclass(frozen=True)
class _WorldLayer:
    """Metadata snapshot of the pipeline's world tensor at init time."""

    transitions: int = 0
    domains: list = field(default_factory=list)
    states: list = field(default_factory=list)
    built: bool = True


class _WorldView:
    """Thin, read-only wrapper around the shared world tensor singleton —
    no copy, just a named handle satisfying kernel/pipeline/protocols.py::
    WorldView by delegation, so callers never reach into a bare tensor."""

    def __init__(self, world: Any) -> None:
        self._world = world

    def states(self) -> list[str]:
        return self._world.states()

    def domains(self) -> list[str]:
        return self._world.domains()

    def nnz(self) -> int:
        return self._world.nnz()

    def successors(self, state: str) -> list:
        return self._world.successors(state)

    def domain_of(self, state: str) -> str:
        return self._world.domain_of(state)


class LegacyCognitiveRuntime(CognitiveRuntimeInterface):
    """Thin façade composing focused cognitive components.

    Implements CognitiveRuntimeInterface (Dependency Inversion Principle).
    Delegates all work to specialized components via composition.
    """

    def __init__(self) -> None:
        """Initialize the façade with all focused components.

        Components are lightweight and lazy — heavy initialization happens in boot().
        """
        from src.monkey_brain.kernel.cognitive_layers.intent_compiler import (
            IntentCompiler,
        )
        from src.monkey_brain.kernel.cognitive_layers.execution_coordinator import (
            ExecutionCoordinator,
        )
        from src.monkey_brain.kernel.cognitive_layers.runtime_monitor import (
            RuntimeMonitor,
        )
        from src.monkey_brain.kernel.cognitive_layers.runtime_bootstrap import (
            RuntimeBootstrap,
        )
        from src.monkey_brain.kernel.cognitive_layers.cognitive_loop import (
            CognitiveLoop,
        )
        from src.monkey_brain.kernel.cognitive_layers.knowledge_manager import (
            KnowledgeManager,
        )
        from src.monkey_brain.kernel.cognitive_layers.world_coordinator import (
            WorldCoordinator,
        )
        from src.monkey_brain.kernel.cognitive_layers.observation_pipeline import (
            ObservationPipeline,
        )
        from src.monkey_brain.kernel.cognitive_layers.audit_service import AuditService

        # Focused components — each owns a single responsibility
        self._intent_compiler = IntentCompiler()  # Layer 1: intent/goal → IntentIR
        self._execution_coordinator = ExecutionCoordinator()  # Layer 3: execute workload
        self._monitor = RuntimeMonitor(name="CognitiveRuntime")  # Layer 4: health & observability
        self._bootstrap = RuntimeBootstrap()  # Lifecycle: boot, shutdown
        self._knowledge_manager = KnowledgeManager()  # Knowledge: explore, acquire, gap detection
        self._world_coordinator = WorldCoordinator()  # World: Context Stream, mutations
        self._observation_pipeline = ObservationPipeline()  # Observations: sensor fusion, estimation
        self._audit_service = AuditService()  # Audit: logging, governance, telemetry

        # Constructed on demand by CognitiveLoop
        self._cognitive_loop: CognitiveLoop | None = None

        # Injected by boot()
        self._event_bus: Any = None
        self.semantic_memory: Any = None
        self.graph_manager: Any = None
        self.wolverine: Any = None
        self.persistence: Any = None
        self.lemon: Any = None
        self.pcp: Any = None
        self.policy: Any = None
        self.observer: Any = None
        self.learning: Any = None
        self.graph_store: Any = None
        self.domain_registry: Any = None

        # SocietyRuntime coordination
        self._society_runtime: Any = None

        # Pipeline world layer (_pipeline_world_init) — cached WorldView/
        # World pair, no actor worlds created here.
        self._world: Any = None
        self._world_layer: Any = None
        self._actor_worlds: dict = {}
        self._world_init_lock = threading.Lock()

    def _pipeline_world_init(self) -> "_WorldLayer":
        """get_world_tensor() is the single source of truth for the
        pipeline's world layer: no _engine.world fallback, no _gpu_world
        branch, one cached WorldView/World pair, no actor worlds created
        here. Cached after the first call (double-checked locking, so
        concurrent callers never race the tensor's own lazy build)."""
        if self._world_layer is not None:
            return self._world_layer
        with self._world_init_lock:
            if self._world_layer is not None:
                return self._world_layer
            from src.monkey_brain.kernel.compile.world_tensor import get_world_tensor

            tensor = get_world_tensor()
            tensor._build_if_needed()
            self._world = _WorldView(tensor)
            self._world_layer = _WorldLayer(
                transitions=tensor.nnz(),
                domains=tensor.domains(),
                states=tensor.states(),
            )
            return self._world_layer

    def _get_cognitive_loop(self) -> Any:
        """Lazy-construct the CognitiveLoop with current dependencies."""
        from src.monkey_brain.kernel.cognitive_layers.cognitive_loop import (
            CognitiveLoop,
        )

        if self._cognitive_loop is None:
            self._cognitive_loop = CognitiveLoop(
                graph_manager=self.graph_manager,
                knowledge_manager=self._knowledge_manager,
                world_coordinator=self._world_coordinator,
                observation_pipeline=self._observation_pipeline,
                audit_service=self._audit_service,
                lemon=self.lemon,
                persistence=self.persistence,
                event_bus=self._event_bus,
                semantic_memory=self.semantic_memory,
            )
        return self._cognitive_loop

    @property
    def execution_graph(self) -> Any:
        """This request's execution graph (context-local, not shared)."""
        return _execution_graph.get()

    @execution_graph.setter
    def execution_graph(self, value: Any) -> None:
        _execution_graph.set(value)

    async def _publish(self, event_type: str, payload: dict) -> None:
        """Publish an event to the event bus (if configured)."""
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    # ── RuntimeInterface Compliance ────────────────────────────────────────

    @property
    def name(self) -> str:
        """Runtime name identifier."""
        return "cognitive"

    @property
    def health(self) -> dict[str, Any]:
        """Get runtime health status with component-level detail."""
        return {
            "name": "cognitive",
            "status": "healthy" if self.wolverine else "degraded",
            "components": {
                "wolverine": "ready" if self.wolverine else "offline",
                "policy": "ready" if self.policy else "offline",
                "society": "ready" if self._society_runtime else "pending",
            },
        }

    @classmethod
    async def boot(
        cls,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
        semantic_memory: Any = None,
        graph_manager: Any = None,
    ) -> "LegacyCognitiveRuntime":
        """Boot the runtime with kernel-injected dependencies.

        Called once at app startup. Wires all subsystems, runs health checks,
        initializes SemanticGraph, and stores the runtime on app.state.
        Delegates to RuntimeBootstrap for the actual wiring.
        """
        rt = cls()

        await rt._bootstrap.boot(
            rt,
            app,
            lemon=lemon,
            persistence=persistence,
            event_bus=event_bus,
            semantic_memory=semantic_memory,
            graph_manager=graph_manager,
        )

        # Wire components
        rt._audit_service.set_lemon(rt.lemon)
        rt._knowledge_manager.set_semantic_memory(rt.semantic_memory)
        if rt._bootstrap.semantic_graph:
            rt._world_coordinator.set_semantic_graph(rt._bootstrap.semantic_graph)

        app.state.cognitive_runtime = rt
        return rt

    async def shutdown(self, app: Any) -> None:
        """Gracefully shutdown all subsystems in reverse dependency order."""
        await self._bootstrap.shutdown(self, app)

    async def execute(self, context: Any, work: Any) -> Any:
        """Execute work within cognitive runtime context (RuntimeInterface compliance)."""
        return await self.execute_cognitive_workload(work)

    async def compile_goal(self, intent: Any) -> Any:
        """Compile intent into actionable goal."""
        return resolve_goal_from_intent(intent)

    # ── Intent Compilation (delegates to IntentCompiler) ───────────────────

    async def compile_intent(
        self,
        question: str,
        *,
        run_id: str | None = None,
        lemon: Any = None,
        store: bool = True,
    ) -> IntentIR | None:
        """Compile a question into a signed IntentIR.

        Returns None when the question doesn't resolve to any intent.
        Pure compilation — no side effects on the world.
        """
        return await self._intent_compiler.compile_intent(
            question,
            run_id=run_id,
            lemon=lemon,
            store=store,
        )

    def compile_intent_from_resolved(
        self,
        *,
        intent: dict,
        goal: Any,
        question: str,
        run_id: str | None = None,
        store: bool = True,
    ) -> IntentIR:
        """Compile from an already-resolved intent/goal pair.

        Skips classification when a caller already has an intent dict.
        """
        return self._intent_compiler.compile_from_resolved(
            intent=intent,
            goal=goal,
            question=question,
            run_id=run_id,
            store=store,
        )

    def build_execution_runtime(
        self,
        intent_ir: IntentIR,
        execution_mode: ExecutionMode,
        *,
        user_id: str = "",
        trace_id: str = "",
        request_metadata: dict | None = None,
    ) -> ExecutionContext:
        """Build an immutable ExecutionContext from a compiled IntentIR.

        Pure compilation — no execution, no world mutation.
        """
        from src.monkey_brain.kernel.cognitive_layers.runtime_builder import (
            RuntimeBuilder,
        )

        builder = RuntimeBuilder()
        return builder.build(
            intent_ir=intent_ir,
            execution_mode=execution_mode,
            user_id=user_id,
            trace_id=trace_id,
            request_metadata=request_metadata,
        )

    # ── Execution (delegates to ExecutionCoordinator) ──────────────────────

    async def execute_cognitive_workload(
        self,
        context: ExecutionContext,
        mongo_client: Any,
        **kwargs: Any,
    ) -> tuple[str, list, list, bool]:
        """Execute cognitive workload through the GoalExecutor.

        Validates the IntentIR, runs it through the executor, persists the
        execution mesh, and records graph observations.
        Returns (answer, semantic_hits, graph_paths, llm_answered).
        """
        answer, semantic_hits, graph_paths, llm_answered = await self._execution_coordinator.execute_workload(
            context,
            mongo_client,
            execution_graph=self.execution_graph,
            **kwargs,
        )
        await self._publish(
            "workload.executed",
            {"run_id": context.run_id, "llm_answered": llm_answered},
        )
        return answer, semantic_hits, graph_paths, llm_answered

    async def replay(self, run_id: str, mongo_client: Any) -> tuple[str, list, list, bool]:
        """Re-execute a stored IntentIR for run_id."""
        return await self._execution_coordinator.replay(run_id, mongo_client)

    def has_stored_run(self, run_id: str) -> bool:
        """Check if a run_id has a stored IntentIR."""
        return self._execution_coordinator.has_stored_run(run_id)

    # ── Knowledge (delegates to KnowledgeManager) ──────────────────────────

    def explore_knowledge_base(self, query: str | None = None) -> Any:
        """Query SittingFace as an external knowledge-base repo.

        Returns the compiler's summary when no query is given;
        returns None if SittingFace never loaded.
        """
        return self._knowledge_manager.explore_knowledge_base(query)

    # ── SocietyRuntime ─────────────────────────────────────────────────────

    def get_society_runtime(self) -> SocietyRuntimeInterface | None:
        """Get the coordinated SocietyRuntime instance."""
        return self._society_runtime

    def set_society_runtime(self, society: SocietyRuntimeInterface) -> None:
        """Set the coordinated SocietyRuntime instance."""
        self._society_runtime = society
        logger.info("[cognitive_runtime] SocietyRuntime registered (dependency inversion)")

    # ── Cognitive Loop (delegates to CognitiveLoop) ────────────────────────

    async def _plan(self, question: str, current_graph: dict, planner: str = "compiler") -> dict:
        """Generate candidate execution graphs via the CognitiveLoop planner."""
        loop = self._get_cognitive_loop()
        loop._graph_manager = self.graph_manager
        loop._semantic_memory = self.semantic_memory
        return await loop._plan(question, current_graph, planner=planner)

    async def run(self, question: str, mongo_client: Any = None, **kwargs: Any) -> dict:
        """Full cognitive cycle: Knowledge → Plan → Simulate → Act → Compare → Learn.

        Delegates to CognitiveLoop which owns the iterative learning loop.
        Returns dict with cycles, convergence status, epoch results, and metrics.
        """
        loop = self._get_cognitive_loop()
        loop._graph_manager = self.graph_manager
        loop._lemon = self.lemon
        loop._persistence = self.persistence
        loop._event_bus = self._event_bus
        loop._semantic_memory = self.semantic_memory
        return await loop.run(question, mongo_client, **kwargs)


def get_cognitive_runtime_instance() -> LegacyCognitiveRuntime:
    """Return the Kernel-booted cognitive runtime.

    Runtime construction is owned by Kernel; this compatibility accessor no
    longer creates an unconfigured production runtime.
    """
    from src.monkey_brain.kernel.kernel import Kernel

    kernel = Kernel._instance
    try:
        runtime = kernel.runtime_selector.select("cognitive") if kernel is not None else None
    except LookupError:
        runtime = None
    if runtime is None:
        raise RuntimeError("CognitiveRuntime is not booted; resolve it through Kernel boot")
    return runtime


# Step 13.1 (ACP-1): backward-compat alias — every existing import of the old
# name (`from ...cognitive_runtime import CognitiveRuntime`) keeps working.
# REMOVAL TARGET: remove after v1.0 (next major release)
CognitiveRuntime = LegacyCognitiveRuntime
