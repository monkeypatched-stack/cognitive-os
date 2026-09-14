"""Runtime — executes capability workloads.

The Runtime is the Wolverine execution engine.
It executes workloads exactly as an OS executes machine instructions.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.monkey_brain.kernel.execute.runtime.outcome import ExecutionOutcome
from src.monkey_brain.runtime.agent_resolver import AgentResult
from src.shared.runtime_protocols import (
    ICapabilityProtocol,
    WorkloadProtocol,
    ExecutionStateProtocol,
    ExecutionOutcomeProtocol,
    OutcomeSubscriberProtocol,
    WorkloadStepProtocol,
    ResolverResultProtocol,
)
from src.monkey_brain.persistence.events import PersistenceEvent, EventType

logger = logging.getLogger("agentos.runtime")


class Runtime:
    """Capability execution runtime (Wolverine).

    Responsibilities:
    - Execute capability workloads
    - Manage execution lifecycle
    - Emit persistence events
    - Report metrics

    The Runtime never:
    - Plans workloads
    - Selects capabilities
    - Performs learning
    - Owns execution policy
    """

    def __init__(self):
        self._capabilities: dict[str, ICapabilityProtocol] = {}
        self._subscribers: list[OutcomeSubscriberProtocol] = []
        self._execution_count = 0
        self._persistence_manager = None
        self._lemon = None
        self._runtime_identity: dict | None = None  # set by set_runtime_identity()

    # this is the unified persistence manager that manages the
    # shortterm , longterm and ephermeral memory this has 4 adapters
    # 1. Elastic Search  -> Search , Logs , Audit Data
    # 2. Mem0 -> System State , Simulation State
    # 3. Influx db -> Sensor data , Event Data , Time based snapshots , Metrics from Lemon
    # 4. Redis -> Session Data
    # 5. Mongo db = Master data , Entity Embeddings , Capability Metadata Embedding , Agent Metadata Embedding
    # 6. Neo4j -> entitiy relationships
    def set_persistence_manager(self, manager) -> None:
        self._persistence_manager = manager

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def set_runtime_identity(self, identity: dict) -> None:
        """Set the agent identity record used to sign workload-scoped tokens."""
        self._runtime_identity = identity

    def subscribe(self, subscriber: OutcomeSubscriberProtocol) -> None:
        """Register a subscriber to receive ExecutionOutcome events after each step."""
        self._subscribers.append(subscriber)

    async def publish(self, outcome: ExecutionOutcomeProtocol) -> None:
        """Publish an ExecutionOutcome to all registered subscribers."""
        for sub in self._subscribers:
            try:
                await sub.on_outcome(outcome)
            except Exception as e:
                import logging

                logging.getLogger(__name__).debug("subscriber %s failed: %s", type(sub).__name__, e)

    # Capability registration stores metadata in-memory
    # (persistence to MongoDB via PersistenceManager is handled externally)
    def register(self, capability: ICapabilityProtocol) -> None:
        self._capabilities[capability.name] = capability

    def register_many(self, capabilities: list[ICapabilityProtocol]) -> None:
        for cap in capabilities:
            self.register(cap)

    def get_capability(self, name: str) -> ICapabilityProtocol | None:
        return self._capabilities.get(name)

    def has_capability(self, name: str) -> bool:
        return name in self._capabilities

    def list_capabilities(self) -> list[str]:
        """List all registered capability names."""
        return list(self._capabilities.keys())

    def search_capabilities(self, query: str) -> list[str]:
        """Search capabilities by name substring."""
        query_lower = query.lower()
        return [name for name in self._capabilities if query_lower in name.lower()]

    # Note: Runtime.execute handles workload-based execution (capability steps).
    # CognitiveRuntime.execute_cognitive_workload handles intent-based execution
    # with full goal/intent context. These are different abstraction levels:
    # - Workload: "do these steps" (list of capabilities, no context on why)
    # - Intent: "here's what the user wants, figure out the steps" (has goal, intent, run_id)
    # TODO: Workload path should be removed — intent should be the only execution path.

    async def execute(
        self,
        workload: WorkloadProtocol,
        state: ExecutionStateProtocol | None = None,
    ) -> ExecutionResult:
        """Execute a capability workload with validation."""
        from src.monkey_brain.kernel.security_boundary import ensure_governed

        async def _run() -> ExecutionResult:
            return await self._execute_workload(workload, state)

        return await ensure_governed(
            "runtime.execute",
            getattr(workload, "workload_id", "workload"),
            _run,
        )

    async def _execute_workload(
        self,
        workload: WorkloadProtocol,
        state: ExecutionStateProtocol | None = None,
    ) -> ExecutionResult:
        """Inner workload loop — entered via execute() after the commitment gate."""
        from src.monkey_brain.runtime.graph_validator import ExecutionGraphValidator

        start_time = time.monotonic()
        self._execution_count += 1

        state_dict = state.to_dict() if state else {}
        step_results = []
        current_state = dict(state_dict)

        goal = current_state.get("question", "")
        trace_id = current_state.get("trace_id", "")

        # Validate execution graph before execution
        if isinstance(workload, WorkloadProtocol) and workload.steps:
            graph = {
                "nodes": [{"id": s.step_id, "agent": s.capability_name} for s in workload.steps],
                "edges": getattr(workload, "edges", []),
            }
            validator = ExecutionGraphValidator()
            errors = validator.validate(graph)
            if errors:
                logger.warning("[runtime] Graph validation warnings: %s", errors)

        # Start trace — pass the ExecutionContext-derived trace_id through
        # Note: Runtime.execute handles workload-based execution (capability steps).
        # CognitiveRuntime.execute_cognitive_workload handles intent-based execution
        # with full goal/intent context. These are different abstraction levels:
        # - Workload: "do these steps" (list of capabilities, no context on why)
        # - Intent: "here's what the user wants, figure out the steps" (has goal, intent, run_id)

        if self._lemon:
            self._lemon.start_trace(f"runtime:{workload.workload_id}", trace_id=trace_id or None)
            self._lemon.counter("runtime.executions")

        current_state["__workload_id__"] = workload.workload_id

        # Steps execute sequentially in list order, so the list must already be
        # in dependency order. Enforce it here rather than trusting the caller:
        # a step must never run before the steps it declares as dependencies.
        for step in self._ordered_steps(workload.steps):
            is_agent_step = (
                isinstance(step, WorkloadStepProtocol) and step.metadata and step.metadata.get("step_type") == "agent"
            )
            agent_type = (
                step.metadata.get("agent", "") if (isinstance(step, WorkloadStepProtocol) and step.metadata) else ""
            )
            step_start = time.monotonic()
            state_before = dict(current_state)

            if self._lemon:
                label = f"agent:{step.capability_name}" if is_agent_step else f"capability:{step.capability_name}"
                self._lemon.start_span(label, component="runtime")

            try:
                if is_agent_step:
                    agent_result = await self._execute_agent_step(step.capability_name, current_state)
                else:
                    capability = self._capabilities.get(step.capability_name)
                    if capability is None:
                        raise LookupError(f"Capability '{step.capability_name}' not found")
                    raw = await capability.execute(current_state)
                    agent_result = self._capability_result_to_agent_result(raw, success=True)

                latency_ms = (time.monotonic() - step_start) * 1000
                current_state = self._reduce_state(current_state, agent_result, agent_type)

                step_result = StepResult(
                    step_id=step.step_id,
                    capability_name=step.capability_name,
                    success=agent_result.success,
                    output=agent_result.payload,
                    latency_ms=latency_ms,
                )

                outcome = ExecutionOutcome(
                    goal=goal,
                    workflow_id=workload.workload_id,
                    agent_type=agent_type,
                    capability_name=step.capability_name,
                    result=agent_result,
                    latency_ms=latency_ms,
                    state_before=state_before,
                    state_after=dict(current_state),
                    trace_id=trace_id,
                )
                await self.publish(outcome)

                if self._lemon:
                    self._lemon.finish_span("ok")
                    self._lemon.counter("runtime.steps_succeeded")
                    self._lemon.histogram(
                        "runtime.step_latency_ms",
                        latency_ms,
                        capability=step.capability_name,
                    )

            except Exception as e:
                latency_ms = (time.monotonic() - step_start) * 1000
                agent_result = AgentResult(
                    agent_name=(step.capability_name if hasattr(step, "capability_name") else "unknown"),
                    reward=0.0,
                    payload={
                        "error": str(e) or type(e).__name__,
                        "unimplemented": isinstance(e, (NotImplementedError, LookupError)),
                    },
                    success=False,
                )
                step_result = StepResult(
                    step_id=step.step_id,
                    capability_name=step.capability_name,
                    success=False,
                    output=agent_result.payload,
                    latency_ms=latency_ms,
                    error=str(e) or type(e).__name__,
                )
                outcome = ExecutionOutcome(
                    goal=goal,
                    workflow_id=workload.workload_id,
                    agent_type=agent_type,
                    capability_name=step.capability_name,
                    result=agent_result,
                    latency_ms=latency_ms,
                    state_before=state_before,
                    state_after=dict(current_state),
                    trace_id=trace_id,
                )
                await self.publish(outcome)

                if self._lemon:
                    self._lemon.finish_span("error")
                    self._lemon.counter("runtime.steps_failed")

            step_results.append(step_result)

            if not step_result.success:
                # Retry once: n8n auto-create → register → retry
                if not getattr(step, "_retried", False):
                    step._retried = True
                    logger.info(
                        "[runtime] Step %s failed — attempting n8n auto-creation and retry",
                        step.capability_name,
                    )
                    try:
                        from src.monkey_brain.runtime.agent_resolver import get_resolver

                        resolver = get_resolver()

                        # 1. Try n8n first
                        try:
                            from src.monkey_brain.kernel.provider_registry import (
                                init_providers,
                            )

                            registry = init_providers()
                            n8n_provider = registry.get_provider("n8n")
                            if n8n_provider and n8n_provider.available:
                                created = await resolver._create_n8n_workflow(
                                    n8n_provider,
                                    step.capability_name,
                                    current_state.get("question", ""),
                                )
                                if created:
                                    logger.info(
                                        "[runtime] Created n8n workflow for %s",
                                        step.capability_name,
                                    )
                        except Exception as e:
                            logger.debug(
                                "[runtime] n8n auto-create failed for %s: %s",
                                step.capability_name,
                                e,
                            )

                        # 2. Re-resolve (n8n provider → Broca fuzzy → SDLC build)
                        resolver._cache.pop(step.capability_name, None)
                        new_agent = await resolver.resolve(
                            step.capability_name,
                            question=current_state.get("question", ""),
                        )
                        if new_agent is not None:
                            retry_start = time.monotonic()
                            retry_state_before = dict(current_state)
                            resolver_result = await new_agent.execute(current_state)
                            # Convert resolver result → runtime AgentResult. A
                            # CapabilityResult carries its answer on .response,
                            # not .produced/.payload — without this branch the
                            # answer is dropped and success silently defaults True.
                            agent_result = self._retry_result_to_agent_result(resolver_result)
                            if agent_result.success:
                                current_state = self._reduce_state(current_state, agent_result, agent_type)
                                retry_latency_ms = (time.monotonic() - retry_start) * 1000
                                step_result = StepResult(
                                    step_id=step.step_id,
                                    capability_name=step.capability_name,
                                    success=True,
                                    output=agent_result.payload,
                                    latency_ms=retry_latency_ms,
                                )
                                step_results[-1] = step_result

                                # The failure outcome was already published above.
                                # Publish the superseding success so subscribers
                                # don't record a permanent phantom failure.
                                retry_outcome = ExecutionOutcome(
                                    goal=goal,
                                    workflow_id=workload.workload_id,
                                    agent_type=agent_type,
                                    capability_name=step.capability_name,
                                    result=agent_result,
                                    latency_ms=retry_latency_ms,
                                    state_before=retry_state_before,
                                    state_after=dict(current_state),
                                    trace_id=trace_id,
                                )
                                await self.publish(retry_outcome)
                                if self._lemon:
                                    self._lemon.counter("runtime.steps_retry_succeeded")
                                logger.info(
                                    "[runtime] Retry succeeded for %s",
                                    step.capability_name,
                                )
                    except Exception as retry_err:
                        logger.warning(
                            "[runtime] Retry failed for %s: %s",
                            step.capability_name,
                            retry_err,
                        )

                if not step_result.success:
                    break

        total_latency = (time.monotonic() - start_time) * 1000
        success = all(r.success for r in step_results) if step_results else False

        execution_result = ExecutionResult(
            workload_id=workload.workload_id,
            success=success,
            steps=step_results,
            final_state=current_state,
            total_latency_ms=total_latency,
        )

        # Emit persistence event
        if self._persistence_manager:
            event = PersistenceEvent(
                event_type=EventType.WORKLOAD_EXECUTED,
                entity_type="execution",
                entity_id=workload.workload_id,
                data={
                    "success": success,
                    "latency_ms": total_latency,
                    "steps": len(step_results),
                },
            )
            await self._persistence_manager.persist(event)

        # Finish trace
        if self._lemon:
            self._lemon.finish_trace()
            self._lemon.histogram("runtime.workload_latency_ms", total_latency)
            completed_steps = sum(1 for r in step_results if r.success)
            failed_steps = sum(1 for r in step_results if not r.success)
            self._lemon.gauge("runtime.workload.completed", float(completed_steps))
            self._lemon.gauge("runtime.workload.failed", float(failed_steps))
            self._lemon.gauge("runtime.workload.total_steps", float(len(step_results)))
            self._lemon.gauge("runtime.workload.total_latency_ms", total_latency)
            for sr in step_results:
                self._lemon.observe_pipeline_step(
                    pipeline_id=workload.workload_id,
                    step_name=sr.capability_name,
                    capability=sr.capability_name,
                    status="ok" if sr.success else "error",
                    latency_ms=sr.latency_ms,
                )

        return execution_result

    @staticmethod
    def _reduce_state(current: dict, result: AgentResult, agent_type: str = "") -> dict:
        """Merge AgentResult.payload into current state.

        Only payload updates shared state — artifacts, metrics, observations,
        and evidence are carried in the typed ExecutionOutcome for subscribers.
        Agent output is namespaced to avoid clobbering 'question'/'answer'.
        """
        merged = dict(current)
        if agent_type:
            merged[f"agent_{agent_type}"] = result.payload
        merged.update(result.payload)
        return merged

    @staticmethod
    def _ordered_steps(steps: list) -> list:
        """Topologically order steps by their declared dependencies.

        Kahn's algorithm, stable in declaration order. Steps whose dependencies
        name unknown ids are treated as satisfied (the dependency lives outside
        this workload). On a cycle, logs and returns declaration order rather
        than dropping steps — an unordered run is bad, a truncated one is worse.
        """
        known = {getattr(s, "step_id", "") for s in steps if getattr(s, "step_id", "")}
        if not known:
            return steps

        indeg: dict[str, int] = {}
        dependents: dict[str, list[str]] = {sid: [] for sid in known}
        by_id = {}
        for s in steps:
            sid = getattr(s, "step_id", "")
            if not sid:
                continue
            by_id[sid] = s
            prereqs = [d for d in (getattr(s, "dependencies", None) or []) if d in known]
            indeg[sid] = len(prereqs)
            for p in prereqs:
                dependents[p].append(sid)

        declared = [getattr(s, "step_id", "") for s in steps if getattr(s, "step_id", "")]
        ready = [sid for sid in declared if indeg[sid] == 0]
        out: list = []
        while ready:
            sid = ready.pop(0)
            out.append(by_id[sid])
            for d in dependents[sid]:
                indeg[d] -= 1
                if indeg[d] == 0:
                    ready.append(d)

        if len(out) != len(by_id):
            logger.error(
                "[runtime] workload dependency cycle (%d of %d steps ordered) — "
                "executing in declaration order; steps may run before their dependencies",
                len(out),
                len(by_id),
            )
            return steps

        # Steps without a step_id keep their relative position at the end.
        unkeyed = [s for s in steps if not getattr(s, "step_id", "")]
        return out + unkeyed

    @staticmethod
    def _retry_result_to_agent_result(resolver_result) -> AgentResult:
        """Convert a retried agent's result into a typed AgentResult.

        Mirrors the shapes _execute_agent_step already unwraps: an AgentResult
        passes through; a CapabilityResult carries its answer on .response and
        neither .produced nor .payload, so reading those would drop the answer
        and let success default to True.
        """
        if isinstance(resolver_result, AgentResult):
            return resolver_result

        if isinstance(resolver_result, ResolverResultProtocol):
            response = resolver_result.response
            answer = response.answer if hasattr(response, "answer") else str(response)
            metadata = getattr(resolver_result, "metadata", {}) or {}
            success = bool(getattr(resolver_result, "success", False))
            # Start from the agent's own payload where it survived (see
            # AgentMiddleware._extract_capability_result). Rebuilding it as just
            # {answer, success} threw away everything structured the agent produced — the rows
            # it read, and the `sources` that let the caller tell a retrieved fact from an
            # invention. The answer and the verdict are layered on top, not substituted for it.
            payload = dict(metadata.get("agent_payload") or {})
            payload["answer"] = answer
            payload["success"] = success
            if metadata.get("error"):
                payload["error"] = metadata["error"]
            if metadata.get("unimplemented"):
                payload["unimplemented"] = True
            return AgentResult(
                agent_name=getattr(resolver_result, "agent_name", "resolver"),
                reward=1.0 if success else 0.0,
                payload=payload,
                success=success,
            )

        return AgentResult(
            agent_name=getattr(resolver_result, "agent_name", "resolver"),
            reward=0.5,
            payload=getattr(resolver_result, "produced", getattr(resolver_result, "payload", {})),
            success=getattr(resolver_result, "success", True),
        )

    @staticmethod
    def _capability_result_to_agent_result(raw, success: bool) -> AgentResult:
        """Wrap a raw CapabilityResult or dict into a typed AgentResult."""
        if isinstance(raw, AgentResult):
            return raw
        output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
        cap_success = getattr(raw, "success", success)
        return AgentResult(
            agent_name=getattr(raw, "agent_name", "capability"),
            reward=1.0 if cap_success else 0.2,
            payload=output,
            success=bool(cap_success),
        )

    async def _execute_agent_step(self, agent_type: str, state: dict) -> AgentResult:
        """Resolve agent via AgentResolver, then execute with middleware.

        The planner names graph nodes freely (PriorityAgent, TaskAgent, ...)
        without knowing what, if anything, actually implements them. If a
        real implementation already exists under that exact name (checked
        read-only, no side effects — see AgentResolver.check_availability),
        use it directly, same as before. Otherwise this node's name is just
        a guess with nothing real behind it, so fall through to full
        capability classification + composition (AgentMiddleware.execute
        with agent=None) instead of accepting AgentResolver's synthetic
        CodeGen-advisory fallback: that path is what lets "PriorityAgent"
        actually resolve into a real capability (or a composed pipeline of
        them) rather than an ungrounded guess, exactly like the --act path
        already does for a bare question.

        All agents go through AgentMiddleware for:
        - Persistent state
        - Response formatting
        - JSON serialization
        """
        try:
            from src.monkey_brain.runtime.agent_resolver import get_resolver
            from src.monkey_brain.runtime.agent_runtime import AgentRuntime

            resolver = get_resolver()
            middleware = AgentRuntime(agent_type)

            availability = await resolver.check_availability(agent_type, question=state.get("question", ""))
            if availability.get("available"):
                agent = await resolver.resolve(agent_type, question=state.get("question", ""))
                resolver_result = await middleware.execute(agent, state)
            else:
                # No real implementation exists under this planner-invented
                # name — classify+route+resolve+compose its actual
                # responsibility instead. The node's own name is the only
                # signal of what that responsibility is (the planner gives
                # nodes no other description), so it's woven into a
                # synthetic per-node question alongside the original
                # request rather than reusing the original question
                # unchanged for every node (which would make every
                # unresolved node in a graph classify identically).
                humanized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", agent_type).replace("_", " ").strip()
                original_question = state.get("question", "")
                composed_state = {
                    **state,
                    "question": (f"{humanized} — for: {original_question}" if original_question else humanized),
                }
                resolver_result = await middleware.execute(None, composed_state)

            # Convert CapabilityResult to AgentResult
            if isinstance(resolver_result, ResolverResultProtocol):
                # It's a CapabilityResult — extract answer from response
                answer = (
                    resolver_result.response.answer
                    if hasattr(resolver_result.response, "answer")
                    else str(resolver_result.response)
                )
                # Start from the agent's own payload, which AgentMiddleware carried through as
                # metadata["agent_payload"]. Rebuilding it from scratch here discarded
                # everything the agent actually produced — the rows it read, and the `sources`
                # that let a caller tell a retrieved fact from an invention. The answer and the
                # verdict are layered ON TOP of that payload, not substituted for it.
                payload = dict(resolver_result.metadata.get("agent_payload") or {})
                payload.update(
                    {
                        "answer": answer,
                        "success": resolver_result.success,
                        "intent": resolver_result.metadata.get("intent", ""),
                        "knowledge_sources": resolver_result.metadata.get("knowledge_sources", []),
                        "grounding_confidence": resolver_result.metadata.get("grounding_confidence", 0.0),
                    }
                )
                if resolver_result.metadata.get("error"):
                    payload["error"] = resolver_result.metadata["error"]
                if resolver_result.metadata.get("unimplemented"):
                    payload["unimplemented"] = True
                return AgentResult(
                    agent_name=getattr(resolver_result, "agent_name", "resolver"),
                    reward=1.0 if resolver_result.success else 0.0,
                    payload=payload,
                    success=resolver_result.success,
                )
            if isinstance(resolver_result, AgentResult):
                return resolver_result
            return AgentResult(
                agent_name=getattr(resolver_result, "agent_name", "resolver"),
                reward=0.5,
                payload=getattr(resolver_result, "produced", getattr(resolver_result, "payload", {})),
                success=getattr(resolver_result, "success", True),
            )
        except Exception as e:
            return AgentResult(
                agent_name="error_handler",
                success=False,
                payload={
                    "error": str(e) or type(e).__name__,
                    "unimplemented": isinstance(e, NotImplementedError),
                },
            )

    def summary(self) -> dict:
        return {
            "capabilities": list(self._capabilities.keys()),
            "executions": self._execution_count,
            "persistence_enabled": self._persistence_manager is not None,
            "lemon_enabled": self._lemon is not None,
        }


# Re-export result types for convenience
from dataclasses import dataclass, field


@dataclass
class StepResult:
    step_id: str = ""
    capability_name: str = ""
    success: bool = True
    output: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class ExecutionResult:
    workload_id: str = ""
    success: bool = True
    steps: list[StepResult] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)
    total_latency_ms: float = 0.0

    def step_count(self) -> int:
        return len(self.steps)
