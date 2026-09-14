"""Cognitive Runtime Facade - Coordinates all cognitive layers.

Delegates to specialized components:
- Layer 1: IntentCompiler (compile intent)
- Layer 2: RuntimeBuilder (build execution context)
- Layer 3: ExecutionCoordinator (execute workload)
- Layer 4: RuntimeMonitor (health & observability)

This facade maintains backward compatibility with CognitiveRuntimeInterface
while delegating to focused components following SOLID principles.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.compile.solid_interfaces import (
    CompilerInterface,
    ExecutorInterface,
    HealthMonitorInterface,
)
from src.monkey_brain.kernel.compile.runtime_interface import (
    CognitiveRuntimeInterface,
    SocietyRuntimeInterface,
)
from src.monkey_brain.kernel.cognitive_layers.intent_compiler import IntentCompiler
from src.monkey_brain.kernel.cognitive_layers.runtime_builder import RuntimeBuilder
from src.monkey_brain.kernel.cognitive_layers.execution_coordinator import (
    ExecutionCoordinator,
)
from src.monkey_brain.kernel.cognitive_layers.runtime_monitor import RuntimeMonitor

if TYPE_CHECKING:
    from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR
    from src.monkey_brain.kernel.execute.context import ExecutionContext
    from src.monkey_brain.kernel.execute.models import ExecutionMode

logger = logging.getLogger("agentos.cognitive.layers.facade")


class CognitiveRuntimeFacade(CognitiveRuntimeInterface):
    """Facade coordinating cognitive runtime layers.

    Responsibilities:
    - Delegate to specialized components (composition over inheritance)
    - Maintain CognitiveRuntimeInterface contract
    - Coordinate multi-actor via SocietyRuntime
    - Provide backward compatibility
    """

    def __init__(self) -> None:
        # Layer 1: Intent Compilation
        self._intent_compiler = IntentCompiler()

        # Layer 2: Runtime Building
        self._runtime_builder = RuntimeBuilder()

        # Layer 3: Execution Coordination
        self._execution_coordinator = ExecutionCoordinator()

        # Layer 4: Runtime Monitoring
        self._monitor = RuntimeMonitor(name="CognitiveRuntime")

        # Multi-actor support via SocietyRuntime
        self._society_runtime: SocietyRuntimeInterface | None = None

        # Event bus (injected)
        self._event_bus: Any = None

        # Knowledge management (injected)
        self.semantic_memory: Any = None
        self.graph_manager: Any = None

        # Other injected subsystems
        self.wolverine: Any = None
        self.persistence: Any = None
        self.lemon: Any = None
        self.pcp: Any = None
        self.policy: Any = None
        self.observer: Any = None
        self.learning: Any = None
        self.graph_store: Any = None
        self.domain_registry: Any = None

    # ── RuntimeInterface Compliance ────────────────────────────────────────

    @property
    def name(self) -> str:
        """Component name."""
        return self._monitor.name

    @property
    def health(self) -> dict[str, Any]:
        """Get runtime health status."""
        return self._monitor.health

    async def boot(self, app: Any, **kwargs: Any) -> None:
        """Boot runtime (injected by kernel)."""
        logger.info("[facade] Booting CognitiveRuntime")
        # Setup event bus if available
        if hasattr(app, "event_bus"):
            self._event_bus = app.event_bus
            self._execution_coordinator.set_event_bus(self._event_bus)
            self._monitor.set_event_bus(self._event_bus)

    async def shutdown(self, app: Any) -> None:
        """Shutdown runtime (graceful)."""
        logger.info("[facade] Shutting down CognitiveRuntime")

    # ── CognitiveRuntimeInterface Compliance ───────────────────────────────

    def get_society_runtime(self) -> SocietyRuntimeInterface | None:
        """Get coordinated SocietyRuntime."""
        return self._society_runtime

    def set_society_runtime(self, society: SocietyRuntimeInterface) -> None:
        """Set coordinated SocietyRuntime."""
        self._society_runtime = society
        logger.info("[facade] SocietyRuntime connected")

    async def compile_intent(self, question: str, run_id: str | None = None) -> IntentIR:
        """Compile intent from question.

        Delegates to Layer 1: IntentCompiler
        """
        logger.debug("[facade] Compiling intent: question=%s", question[:60])
        # This would be the full flow: resolve intent → resolve goal → compile
        # For now, we provide the core compilation
        raise NotImplementedError("Use compile_intent_from_resolved() instead")

    async def compile_goal(self, intent_ir: IntentIR) -> Any:
        """Compile goal from intent (not needed in current architecture)."""
        raise NotImplementedError("Goal compilation is done in intent IR building")

    async def execute_cognitive_workload(
        self,
        context: ExecutionContext,
        mongo_client: Any,
        **kwargs: Any,
    ) -> tuple[str, list, list, bool]:
        """Execute cognitive workload.

        Delegates to Layer 3: ExecutionCoordinator
        """
        start_time = time.time()
        try:
            logger.debug("[facade] Executing workload: run_id=%s", context.run_id)

            answer, semantic_hits, graph_paths, llm_answered = await self._execution_coordinator.execute_workload(
                context,
                mongo_client,
                **kwargs,
            )

            # Record success
            duration = time.time() - start_time
            self._monitor.record_execution(context.run_id, success=True, duration=duration)

            # Publish event
            await self._monitor.publish_event(
                "workload.executed",
                {"run_id": context.run_id, "llm_answered": llm_answered},
            )

            return answer, semantic_hits, graph_paths, llm_answered

        except Exception as e:
            duration = time.time() - start_time
            self._monitor.record_error(context.run_id, e)
            raise

    # ── Layer Delegation Methods ───────────────────────────────────────────

    def compile_intent_from_resolved(
        self,
        *,
        intent: dict,
        goal: Any,
        question: str,
        run_id: str | None = None,
        store: bool = True,
    ) -> IntentIR:
        """Delegate to Layer 1: IntentCompiler."""
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
        request_metadata: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        """Delegate to Layer 2: RuntimeBuilder."""
        return self._runtime_builder.build(
            intent_ir=intent_ir,
            execution_mode=execution_mode,
            user_id=user_id,
            trace_id=trace_id,
            request_metadata=request_metadata,
        )

    # ── Subsystem Access ────────────────────────────────────────────────────

    @property
    def intent_compiler(self) -> CompilerInterface:
        """Access intent compiler (testing/direct use)."""
        return self._intent_compiler

    @property
    def runtime_builder(self) -> ExecutorInterface:
        """Access runtime builder (testing/direct use)."""
        return self._runtime_builder

    @property
    def execution_coordinator(self) -> Any:
        """Access execution coordinator (testing/direct use)."""
        return self._execution_coordinator

    @property
    def monitor(self) -> HealthMonitorInterface:
        """Access runtime monitor (testing/direct use)."""
        return self._monitor

    def explore_knowledge_base(self, query: str | None = None) -> Any:
        """Explore knowledge base (stub for compatibility)."""
        logger.debug("[facade] Knowledge base exploration: query=%s", query or "all")
        # This would delegate to a KnowledgeExplorer layer in future refactoring
        return None
