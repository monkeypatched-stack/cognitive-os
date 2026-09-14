"""CodeGenRuntime — the 4th independent runtime, covering the full SDLC:

    Specification -> Requirements -> Architecture -> Design -> Implementation
    -> Review -> Testing -> Packaging -> Release

Mirrors CognitiveRuntime/SimulationRuntime/ComparatorRuntime's OS-process
analogy, but the "workload" it runs is the software development lifecycle
itself — turning a stakeholder ask into a shipped, deployed service. Each
SDLC stage is a node in an ExecutionGraph (same DAG-node-granularity model
the Process Manager already governs for the other three runtimes), and each
node's "capability" is a Broca agent's agent_type. Runs under the SAME
ProcessManager (kernel/process/manager.py) as Cognitive/Simulation/
Comparator, at zero extra cost — checkpoint/resume/compensation/human-
approval all come for free rather than being reimplemented here.

Reuses, rather than reinvents, three pieces of existing infrastructure that
must not be duplicated:

  1. Self-healing loops — GraphScheduler's expansion-policy mechanism
     (SelfHealingPolicy), already wrapped by ProcessManager's
     _TrackingExpansionPolicy for every Runtime Process regardless of which
     top-level runtime created it. CodeGenRuntime gets bounded automatic
     retry on any failed SDLC-stage node for free, same as Cognitive/
     Simulation/Comparator do.

  2. Agent + capability generation — the "Implementation" stage doesn't just
     call ServiceGenAgent/ClientGenAgent/MotorCortexAgent; it also runs the
     DDD construct agents (DomainAgent, AggregateAgent, EntityAgent,
     ValueObjectAgent, RepositoryAgent, PolicyAgent — domain consistency
     checks) and the DDD compliance-check agents (packages/broca/broca/
     agents/ddd/compliance/*.py — SOC2/GDPR/ISO27001 by default, plus
     domain-specific ones like FDA/GxP/ISO10218/IEC61508 when the caller's
     context asks for them) as parallel fan-out nodes over the generated
     code, all gating entry into Review.

  3. Auto-registration — the existing broca.registry.register_etass_agents()
     call (already invoked once during CognitiveRuntime.boot() via
     bootstrap.init_sittingface(), and independently idempotent — re-running
     it just re-registers fresh instances) plus BrocaAgentRegistry's
     __init_subclass__-driven auto-registration for any agent not in that
     explicit manifest (the Soma git agents, compliance agents, etc.).
     CodeGenRuntime.boot() calls it again defensively, so it also works
     standalone (e.g. in tests) without a full Kernel boot.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Literal

from src.monkey_brain.kernel.execute.graph import ExecutionGraph, GraphNode, GraphEdge
from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR
from src.monkey_brain.kernel.process.models import RuntimeProcessState

logger = logging.getLogger("agentos.codegen_runtime")

_REPO = Path(__file__).parents[3]

# General-purpose compliance checks that apply to any generated service by
# default. Domain-specific ones (FDA/GxP/ISO10218/IEC61508) are opt-in via
# the `compliance_domains` context key — not every generated service is
# medical/robotics, so they're not run unconditionally.
_DEFAULT_COMPLIANCE_DOMAINS = ("soc2", "gdpr", "iso27001")
_DDD_CONSTRUCT_AGENTS = (
    "domain",
    "aggregate",
    "entity",
    "value_object",
    "repository",
    "policy",
)


def _ensure_broca_on_path() -> None:
    """Same idempotent sys.path setup used by sittingface_workload.py's
    _ensure_broca() — packages/broca isn't installed in all environments.
    """
    for p in ("packages/broca", "packages/cerebellum", "packages/soma-cli"):
        full = str(_REPO / p)
        if full not in sys.path:
            sys.path.insert(0, full)


def _slugify_question(question: str, max_words: int = 4) -> str:
    """Derive a service_slug from the free-text question — api_chart,
    service_gen, seed, and serve agents (design/implementation stages) all
    require one in context and silently no-op without it (they return
    success=False with no "error" in payload, so nothing upstream ever
    surfaces why the node failed).
    """
    words = re.findall(r"[a-z0-9]+", question.lower())
    stopwords = {
        "a",
        "an",
        "the",
        "build",
        "me",
        "that",
        "to",
        "for",
        "with",
        "of",
        "and",
    }
    kept = [w for w in words if w not in stopwords][:max_words]
    return "-".join(kept) or "service"


# capability_name -> Lemon counter name, emitted once per successful dispatch.
# DDD construct checks (domain/aggregate/entity/value_object/repository/policy)
# all roll up into one "ddd_nodes_generated" counter rather than 6 separate ones.
_SDLC_METRIC_BY_CAPABILITY: dict[str, str] = {
    "prompt_compiler": "sdlc.specifications_compiled",
    "requirements": "sdlc.requirements_generated",
    "architecture_decision": "sdlc.architectures_generated",
    "api_chart": "sdlc.designs_generated",
    "service_gen": "sdlc.services_generated",
    "code_review": "sdlc.review_cycles",
    "governance": "sdlc.review_cycles",
    "test_authoring": "sdlc.tests_generated",
    "artifact_packaging": "sdlc.packages_created",
    "pull_request": "sdlc.pull_requests_created",
    "release": "sdlc.releases_published",
    **{name: "sdlc.ddd_nodes_generated" for name in _DDD_CONSTRUCT_AGENTS},
}


class BrocaCapabilityBus:
    """Adapts broca.registry's agent registry to the CapabilityBus-shaped
    interface GraphScheduler expects (`async execute(name, args) -> result
    with .success/.output/.error`), so GraphScheduler's existing dispatch
    loop needs no changes to drive Broca agents instead of the kernel's own
    CapabilityBus.
    """

    def __init__(
        self,
        registry: Any = None,
        base_context: dict[str, Any] | None = None,
        lemon: Any = None,
    ) -> None:
        self._registry = registry
        self._base_context = base_context or {}
        self._lemon = lemon

    async def execute(self, capability_name: str, args: dict[str, Any] | None = None) -> "_BusResult":
        _ensure_broca_on_path()
        from broca.registry import get_registry

        registry = self._registry or get_registry()
        agent = registry.discover(capability_name)
        if agent is None:
            return _BusResult(
                success=False,
                output={},
                error=f"no Broca agent registered for capability {capability_name!r}",
            )

        context = {**self._base_context, **(args or {})}
        try:
            result = await agent.handle(context)
        except Exception as exc:
            logger.error("[codegen_runtime] agent %s failed: %s", capability_name, exc)
            return _BusResult(success=False, output={}, error=str(exc))

        success = bool(getattr(result, "success", True))
        payload = getattr(result, "payload", result if isinstance(result, dict) else {})
        error = payload.get("error", "") if isinstance(payload, dict) else ""

        if success and self._lemon is not None:
            metric = _SDLC_METRIC_BY_CAPABILITY.get(capability_name)
            if metric:
                self._lemon.counter(metric)

        # Fan the payload back into the shared context so downstream SDLC
        # stages (e.g. architecture_decision reading requirements, or
        # test_authoring reading requirements) see prior stages' outputs —
        # ExecutionGraph tracks per-node results, but agents only see
        # `args`/base_context, so we accumulate here.
        self._base_context.update({k: v for k, v in payload.items()} if isinstance(payload, dict) else {})
        return _BusResult(success=success, output=payload, error=error)


class _BusResult:
    __slots__ = ("success", "output", "error")

    def __init__(self, success: bool, output: dict, error: str) -> None:
        self.success = success
        self.output = output
        self.error = error


class CodeGenRuntime:
    """Compile Intent (shared) -> Run (walk the SDLC ExecutionGraph via the
    Process Manager) -> Replay. Lightweight like SimulationRuntime/
    ComparatorRuntime — holds no reference to CognitiveRuntime and needs no
    heavy subsystem wiring of its own; it borrows Wolverine (for Broca
    agents' capability-lookup fallback) and the ProcessManager from the
    Kernel rather than building either itself.
    """

    def __init__(self) -> None:
        self._event_bus: Any = None
        self.lemon: Any = None
        self.persistence: Any = None
        self.process_manager: Any = None
        self.wolverine: Any = None

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    @classmethod
    async def boot(
        cls,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
        process_manager: Any = None,
        wolverine: Any = None,
    ) -> "CodeGenRuntime":
        """Attach Kernel-injected dependencies, ensure Broca is registered,
        and register on app.state. No heavy subsystem wiring — the SDLC
        ExecutionGraph is built fresh per run() call from the compiled
        question, same as Simulation/ComparatorRuntime build their result
        per call rather than at boot time.
        """
        rt = cls()
        rt.lemon = lemon
        rt.persistence = persistence
        rt._event_bus = event_bus
        rt.process_manager = process_manager
        rt.wolverine = wolverine

        _ensure_broca_on_path()
        try:
            from broca.registry import register_etass_agents

            registered = register_etass_agents(runtime=wolverine)
            logger.info("CodeGenRuntime boot: %d Broca agents registered", len(registered))
        except Exception as exc:
            logger.warning("CodeGenRuntime boot: Broca agent registration skipped: %s", exc)

        # sdlc.approvals_required/received: ProcessManager publishes these on
        # the same shared Kernel event bus regardless of which runtime owns
        # the process — subscribing here (rather than inside ProcessManager
        # itself) keeps the "sdlc.*" metric names scoped to CodeGenRuntime,
        # since approval gates are a concept only its SDLC graphs use today.
        if event_bus is not None and lemon is not None:
            event_bus.subscribe(
                "process.approval_requested",
                lambda payload: lemon.counter("sdlc.approvals_required"),
            )
            event_bus.subscribe(
                "process.approval_granted",
                lambda payload: lemon.counter("sdlc.approvals_received"),
            )

        app.state.codegen_runtime = rt
        global _booted_instance
        _booted_instance = rt
        if lemon is not None:
            lemon.info("CodeGenRuntime boot complete", component="bootstrap")
        logger.info("CodeGenRuntime boot complete")
        await rt._publish("codegen_runtime.booted", {})
        return rt

    async def shutdown(self, app: Any) -> None:
        """No subsystems of its own to tear down — see SimulationRuntime.
        shutdown()'s docstring for the rationale. Does not touch
        ProcessManager (Kernel shuts that down separately, since it's
        shared across all three lightweight runtimes plus CognitiveRuntime).
        """
        await self._publish("codegen_runtime.shutdown", {})
        logger.info("CodeGenRuntime shutdown complete")

    # ------------------------------------------------------------------
    # Compile Intent (shared with the other three runtimes)
    # ------------------------------------------------------------------

    async def compile_intent(
        self,
        question: str,
        *,
        run_id: str | None = None,
        lemon: Any = None,
        store: bool = True,
    ) -> IntentIR | None:
        from src.monkey_brain.kernel.plan.goals.compile import (
            compile_intent as _compile,
        )

        ir = await _compile(
            question,
            target="codegen",
            run_id=run_id,
            lemon=lemon or self.lemon,
            store=store,
        )
        await self._publish(
            "intent.compiled" if ir is not None else "intent.compile_failed",
            {
                "run_id": run_id,
                "target": "codegen",
                "intent_type": ir.intent_type if ir else None,
            },
        )
        return ir

    # ------------------------------------------------------------------
    # Build the SDLC ExecutionGraph
    # ------------------------------------------------------------------

    def build_sdlc_graph(
        self,
        question: str,
        *,
        compliance_domains: tuple[str, ...] | None = None,
        include_release: bool = True,
    ) -> ExecutionGraph:
        """One node per SDLC stage; Implementation fans out into the DDD
        construct agents + DDD compliance-check agents over the implementation,
        all gating entry into Review — see module docstring point 2.

        include_release=False builds the local-only variant AgentResolver's
        last-resort tier uses: pull_request/deploy(release-gate)/release are
        dropped entirely — `serve` connects straight to `packaging` instead,
        since none of the three are actual prerequisites for serving the
        generated service locally, only for shipping it externally. The
        graph also stops at register_agent rather than continuing into
        planner/simulate/execute/comparator/policy_update — closing the
        cognitive-loop learning signal is a separate concern from "is there
        now a real, callable agent," which register_agent alone answers.
        """
        domains = compliance_domains or _DEFAULT_COMPLIANCE_DOMAINS
        graph = ExecutionGraph()

        def add(node_id: str, capability: str, *deps: str) -> str:
            graph.add_node(
                GraphNode(
                    id=node_id,
                    type="step",
                    label=node_id,
                    props={"capability": capability, "question": question},
                )
            )
            for dep in deps:
                graph.add_edge(GraphEdge(src=dep, dst=node_id, rel="depends_on"))
            return node_id

        discovery = add("sdlc:specification_discovery", "specification_discovery")
        spec = add("sdlc:specification", "prompt_compiler", discovery)
        reqs = add("sdlc:requirements", "requirements", spec)
        arch = add("sdlc:architecture", "architecture_decision", reqs)
        design = add("sdlc:design", "api_chart", arch)

        service_gen = add("sdlc:implementation:service_gen", "service_gen", design)

        ddd_nodes = [add(f"sdlc:implementation:ddd_{name}", name, service_gen) for name in _DDD_CONSTRUCT_AGENTS]
        compliance_nodes = [
            add(
                f"sdlc:implementation:compliance_{domain}",
                f"compliance_{domain}",
                service_gen,
            )
            for domain in domains
        ]
        implementation_fanout = [service_gen, *ddd_nodes, *compliance_nodes]

        review = add("sdlc:review", "code_review", *implementation_fanout)
        governance = add("sdlc:review:governance", "governance", *implementation_fanout)

        test_authoring = add("sdlc:testing:authoring", "test_authoring", reqs)
        unit_testing = add("sdlc:testing:unit", "unit_testing", review, governance, test_authoring)
        test_service = add("sdlc:testing:service", "test_service", unit_testing)
        integration_test = add("sdlc:testing:integration", "integration_test", test_service)

        packaging = add("sdlc:packaging", "artifact_packaging", integration_test)

        if include_release:
            pull_request = add("sdlc:release:pull_request", "pull_request", packaging)
            deploy = add("sdlc:deploy", "deploy", pull_request)
            release = add("sdlc:release", "release", deploy)
            serve_deps = (release,)
        else:
            serve_deps = (packaging,)

        # Post-release (or, for the local-only variant, post-packaging):
        # serve the generated service, build a typed client against it,
        # charter that client into a SOMA capability chart, compile + register
        # it as a new discoverable agent.
        serve = add("sdlc:serve", "serve", *serve_deps)
        client_gen = add("sdlc:client_gen", "client_gen", serve)
        client_charter = add("sdlc:client_charter", "client_charter", client_gen)
        compile_agent_node = add("sdlc:compile_agent", "compile_agent", client_charter)
        register_agent = add("sdlc:register_agent", "client_capability_register", compile_agent_node)

        if include_release:
            # Close the cognitive loop — plan, simulate, execute for real,
            # measure the epistemic gap between predicted and actual, and
            # feed that back into the planner's Bellman/Q-learning policy.
            planner_node = add("sdlc:planner", "planner", register_agent)
            simulate_node = add("sdlc:simulate", "plan_simulate", planner_node)
            execute_real = add("sdlc:execute", "executor", simulate_node)
            comparator_node = add("sdlc:comparator", "comparator", execute_real)
            add("sdlc:policy_update", "policy_update", comparator_node)

        graph.metadata = {
            "compiler": "sdlc_builder",
            "graph_kind": "codegen_sdlc",
            "question": question,
            "include_release": include_release,
        }
        from src.monkey_brain.kernel.pipeline.graph_execution import (
            normalize_execution_graph,
        )

        return normalize_execution_graph(graph)

    # ------------------------------------------------------------------
    # Run — full lifecycle via the shared Process Manager
    # ------------------------------------------------------------------

    async def start_run(
        self,
        intent_ir: IntentIR,
        mongo_client: Any = None,
        *,
        user_id: str = "",
        compliance_domains: tuple[str, ...] | None = None,
        execution_mode: Literal["serial", "parallel"] = "serial",
        include_release: bool = True,
        **extra_context: Any,
    ) -> str:
        """Compile the SDLC graph and create + start the Runtime Process —
        returns run_id immediately, before any node has actually executed.

        Split out of run() so callers that want to poll progress (rather
        than block for however long the whole pipeline takes — several
        minutes, serial LLM calls at every stage) can get a run_id right
        away and drive tick_until_settled() themselves, e.g. as a
        fire-and-forget background task (see routes/sdlc.py's sdlc_run).
        """
        if self.process_manager is None:
            raise RuntimeError("CodeGenRuntime.run() requires a ProcessManager — boot() must inject one")

        question = intent_ir.execution_metadata.get("question", "")
        run_id = intent_ir.run_id

        context = ExecutionContext.create(
            run_id=run_id,
            execution_mode=ExecutionMode.EXECUTE,
            intent_ir=intent_ir,
            user_id=user_id,
        )
        graph = self.build_sdlc_graph(
            question,
            compliance_domains=compliance_domains,
            include_release=include_release,
        )
        service_slug = extra_context.pop("service_slug", None) or _slugify_question(question)
        bus = BrocaCapabilityBus(
            base_context={
                "question": question,
                "service_slug": service_slug,
                **extra_context,
            },
            lemon=self.lemon,
        )

        pm = self.process_manager
        rpcb = pm.get_process(run_id)
        if rpcb is None:
            rpcb = await pm.create_process(
                context,
                graph=graph,
                target="codegen",
                capability_bus=bus,
                execution_mode=execution_mode,
            )
        await pm.start_process(run_id)
        return run_id

    async def tick_until_settled(self, run_id: str, max_ticks: int = 200) -> dict[str, Any]:
        """Tick a started Runtime Process until it leaves RUNNING state
        (COMPLETED/FAILED/WAITING on an approval gate/etc.), then return the
        same summary shape run() always returned.

        `execution_mode` defaults to "serial" (set at start_run() time): the
        Implementation stage fans out into up to 9 simultaneously-runnable
        nodes (DDD construct checks + compliance checks), several of which
        call an LLM backend via BaseETASSAgent._llm_from_spec() — that
        backend is frequently a local Ollama instance, which chokes on
        concurrent generation requests.
        """
        pm = self.process_manager
        if pm is None:
            raise RuntimeError("CodeGenRuntime.tick_until_settled() requires a ProcessManager — boot() must inject one")

        for _ in range(max_ticks):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm.get_process(run_id)
        await self._publish("codegen.sdlc_run", {"run_id": run_id, "state": final.state.value})
        return {
            "run_id": run_id,
            "state": final.state.value,
            "graph_summary": final.graph.get_execution_summary(),
            "approval_pending": final.approval_gate is not None,
            "error": final.error.message if final.error else None,
        }

    async def run(
        self,
        intent_ir: IntentIR,
        mongo_client: Any = None,
        *,
        user_id: str = "",
        max_ticks: int = 60,
        compliance_domains: tuple[str, ...] | None = None,
        execution_mode: Literal["serial", "parallel"] = "serial",
        **extra_context: Any,
    ) -> dict[str, Any]:
        """Fully-blocking run: start_run() + tick_until_settled(). Matches
        SimulationRuntime/ComparatorRuntime's run(intent_ir, mongo_client)
        signature so run_store.replay()'s target="codegen" dispatch works
        identically to the other three targets. Prefer start_run() +
        tick_until_settled() directly when the caller wants to poll/report
        progress instead of blocking for the whole pipeline.
        """
        run_id = await self.start_run(
            intent_ir,
            mongo_client,
            user_id=user_id,
            compliance_domains=compliance_domains,
            execution_mode=execution_mode,
            **extra_context,
        )
        return await self.tick_until_settled(run_id, max_ticks=max_ticks)

    async def run_question(self, question: str, mongo_client: Any = None, **kwargs: Any) -> dict[str, Any]:
        """Convenience entry point for callers that don't split compile from
        run across two requests — compiles a fresh IntentIR and runs it.
        """
        ir = await self.compile_intent(question, store=True)
        if ir is None:
            return {
                "error": "could not compile intent for SDLC pipeline",
                "run_id": None,
            }
        return await self.run(ir, mongo_client, **kwargs)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    async def replay(self, run_id: str, mongo_client: Any = None) -> dict[str, Any]:
        from src.monkey_brain.kernel.plan.goals.run_store import replay as _replay

        result = await _replay(run_id, mongo_client)
        await self._publish("run.replayed", {"run_id": run_id, "target": "codegen"})
        return result

    def has_stored_run(self, run_id: str) -> bool:
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

        return get_run_store().has(run_id)


_booted_instance: "CodeGenRuntime | None" = None


def get_codegen_runtime_instance() -> "CodeGenRuntime | None":
    """The Kernel-booted singleton (with process_manager/wolverine wired), for
    callers with no FastAPI Request to pull app.state.codegen_runtime from —
    e.g. AgentResolver, several call frames below any request handler. A
    bare CodeGenRuntime() has no process_manager and start_run() raises
    immediately, so this returns None rather than a broken instance until
    boot() has actually run.
    """
    return _booted_instance
