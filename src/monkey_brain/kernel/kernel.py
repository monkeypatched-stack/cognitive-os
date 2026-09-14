"""Kernel — deterministic, idempotent boot orchestrator for MonkeyBrain.

Boot sequence (deterministic, ordered):
    1. Logging
    2. Configuration (.env)
    3. Observability (Lemon)
    4. Persistence Manager
    5. Runtime (Wolverine)
    6. Providers (Cerebellum)
    7. Agents (Broca) — registered ONCE
    8. Policy Control Plane
    9. Runtime Identity
   10. NANDA Discovery
   11. Policy (Bellman)
   12. Observer
   13. Learning
   14. SittingFace
   15. Execution Graph
   16. Graph Store (Neo4j)
   17. Process Manager
   18. Cognitive Runtime
   19. Simulation Runtime
   20. Comparator Runtime
   21. CodeGen Runtime
   22. Health Verification
   23. Boot Summary → READY

Idempotency: boot() may only execute once. Repeated calls return the same kernel.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from src.monkey_brain.kernel.resource_manager import ResourceManager
from src.monkey_brain.kernel.scheduler import RuntimeDescriptor

logger = logging.getLogger("agentos.kernel")


# ── Health Models ────────────────────────────────────────────────────────────


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    name: str
    state: HealthState = HealthState.UNKNOWN
    message: str = ""
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.state == HealthState.HEALTHY

    @property
    def icon(self) -> str:
        return {
            HealthState.HEALTHY: "✓",
            HealthState.DEGRADED: "⚠",
            HealthState.UNAVAILABLE: "✗",
            HealthState.UNKNOWN: "?",
        }.get(self.state, "?")


@dataclass
class RuntimeInfo:
    name: str
    state: str = "unknown"
    health: ComponentHealth = field(default_factory=lambda: ComponentHealth(name=""))
    version: str = "1.0.0"
    started_at: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


# ── Runtime Registry ─────────────────────────────────────────────────────────


class RuntimeRegistry:
    """Kernel-owned production Runtime Registry.

    Registration accepts already-created runtime instances for compatibility
    with the current boot sequence. The registry never instantiates or
    executes runtimes; it owns their identity, metadata, health, dependencies,
    provenance, and shutdown metadata.
    """

    _LIFECYCLE_STATES = frozenset(
        {
            "registered",
            "booting",
            "ready",
            "degraded",
            "draining",
            "stopped",
            "failed",
        }
    )
    _HEALTH_STATES = frozenset({"healthy", "degraded", "unavailable", "unknown"})

    def __init__(self) -> None:
        self._runtimes: dict[str, Any] = {}
        self._info: dict[str, RuntimeInfo] = {}
        self._descriptors: dict[str, RuntimeDescriptor] = {}

    def register(self, name: str, runtime: Any, *, descriptor: RuntimeDescriptor | None = None) -> None:
        if name in self._runtimes:
            raise ValueError(f"runtime already registered: {name}")
        descriptor = descriptor or RuntimeDescriptor(
            runtime_id=name,
            name=name,
            runtime_class=type(runtime).__name__ if runtime is not None else "",
            provenance="kernel.register",
        )
        if descriptor.id != name:
            raise ValueError(f"runtime descriptor id {descriptor.id!r} does not match {name!r}")
        if descriptor.lifecycle_state not in self._LIFECYCLE_STATES:
            raise ValueError(f"invalid runtime lifecycle state: {descriptor.lifecycle_state}")
        if descriptor.health_state not in self._HEALTH_STATES:
            raise ValueError(f"invalid runtime health state: {descriptor.health_state}")
        if descriptor.name in (d.name for key, d in self._descriptors.items() if key != name):
            raise ValueError(f"runtime name already registered: {descriptor.name}")
        self._runtimes[name] = runtime
        self._descriptors[name] = descriptor
        self._info[name] = RuntimeInfo(
            name=name,
            # Preserve the legacy RuntimeInfo contract while descriptor
            # lifecycle metadata moves to the canonical registry model.
            state="running",
            started_at=time.time(),
            version=descriptor.version,
            dependencies=list(descriptor.dependencies),
        )

    def get(self, name: str) -> Any:
        return self._runtimes.get(name)

    def names(self) -> list[str]:
        return list(self._runtimes.keys())

    def all(self) -> dict[str, Any]:
        return dict(self._runtimes)

    def info(self, name: str) -> RuntimeInfo | None:
        return self._info.get(name)

    def all_info(self) -> list[RuntimeInfo]:
        return [self._info[n] for n in self._runtimes]

    def descriptor(self, name: str) -> RuntimeDescriptor | None:
        return self._descriptors.get(name)

    def descriptors(self) -> dict[str, RuntimeDescriptor]:
        return dict(self._descriptors)

    def set_lifecycle(self, name: str, state: str) -> None:
        if state not in self._LIFECYCLE_STATES:
            raise ValueError(f"invalid runtime lifecycle state: {state}")
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise KeyError(name)
        descriptor.lifecycle_state = state
        self._info[name].state = state

    def set_health(self, name: str, health: ComponentHealth) -> None:
        if name in self._info:
            self._info[name].health = health
            descriptor = self._descriptors.get(name)
            if descriptor is not None:
                descriptor.health_state = health.state.value

    def set_capabilities(self, name: str, caps: list[str]) -> None:
        if name in self._info:
            self._info[name].capabilities = caps

    def validate(self) -> None:
        """Fail fast on malformed registry metadata and dependency cycles."""
        names = set(self._descriptors)
        for descriptor in self._descriptors.values():
            missing = set(descriptor.dependencies) - names
            if missing:
                raise RuntimeError(f"runtime {descriptor.id!r} has missing dependencies: {sorted(missing)}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise RuntimeError(f"runtime dependency cycle detected at {name!r}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self._descriptors[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in self._descriptors:
            visit(name)


class AgentRegistry:
    """Kernel-owned facade over existing local and provider agent registries."""

    def __init__(self) -> None:
        self._local: dict[str, Any] = {}
        self._broca: Any = None
        self._providers: Any = None

    def register(self, agent: Any, name: str | None = None) -> None:
        key = name or getattr(agent, "agent_type", None) or getattr(agent, "name", None)
        if not key:
            raise ValueError("agent registration requires agent_type or name")
        if key in self._local:
            raise ValueError(f"agent already registered: {key}")
        self._local[key] = agent

    def attach_broca(self, registry: Any) -> None:
        self._broca = registry

    def attach_provider_registry(self, registry: Any) -> None:
        self._providers = registry

    def discover(self, name: str) -> Any | None:
        if name in self._local:
            return self._local[name]
        if self._broca is not None:
            agents = getattr(self._broca, "_agents", {})
            if name in agents:
                return agents[name]
            discover = getattr(self._broca, "get", None)
            if callable(discover):
                found = discover(name)
                if found is not None:
                    return found
        if self._providers is not None:
            found = self._providers.find_agent(name)
            if found is not None:
                return found
        return None

    def names(self) -> list[str]:
        names = set(self._local)
        if self._broca is not None:
            names.update(getattr(self._broca, "_agents", {}).keys())
        return sorted(names)

    def validate(self) -> None:
        """Validate the Kernel facade without mutating legacy backends."""
        if len(self._local) != len(set(self._local)):
            raise RuntimeError("duplicate local agent registrations")


class CapabilityRegistry:
    """Kernel-owned facade over existing capability buses."""

    def __init__(self) -> None:
        self._local: dict[str, Any] = {}
        self._buses: list[Any] = []

    def register(self, capability: Any, name: str | None = None) -> None:
        key = name or getattr(capability, "name", None)
        if not key:
            raise ValueError("capability registration requires name")
        if key in self._local:
            raise ValueError(f"capability already registered: {key}")
        self._local[key] = capability

    def attach_bus(self, bus: Any) -> None:
        if bus not in self._buses:
            self._buses.append(bus)

    def discover(self, name: str) -> Any | None:
        if name in self._local:
            return self._local[name]
        for bus in self._buses:
            discover = getattr(bus, "discover", None)
            if callable(discover):
                found = discover(name)
                if found is not None:
                    return found
        return None

    def names(self) -> list[str]:
        names = set(self._local)
        for bus in self._buses:
            capabilities = getattr(bus, "_capabilities", {})
            names.update(capabilities.keys())
        return sorted(names)

    def validate(self) -> None:
        """Reject ambiguous capability names across attached buses."""
        seen: dict[str, Any] = {}
        for bus in self._buses:
            names = getattr(bus, "names", None)
            available = names() if callable(names) else getattr(bus, "_capabilities", {}).keys()
            for name in available:
                if name in seen and seen[name] is not bus:
                    raise RuntimeError(f"duplicate capability registration: {name}")
                seen[name] = bus


class RuntimeSelector:
    """Kernel-owned runtime lookup; execution remains owned by the runtime."""

    def __init__(self, registry: RuntimeRegistry) -> None:
        self.registry = registry

    def select(self, runtime_id: str = "cognitive") -> Any:
        runtime = self.registry.get(runtime_id)
        if runtime is None:
            raise LookupError(f"runtime not registered: {runtime_id}")
        return runtime


# ── EventBus ─────────────────────────────────────────────────────────────────


class EventBus:
    """Async pub/sub — defensive: failing subscriber never breaks publisher."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[dict], Any]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[dict], Any]) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event_type: str, payload: dict) -> None:
        for handler in self._subscribers.get(event_type, []):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.debug("event handler for %r failed: %s", event_type, exc)


# ── Boot Phases ──────────────────────────────────────────────────────────────


@dataclass
class BootPhase:
    name: str
    state: str = "pending"  # pending | running | done | failed | skipped
    duration_ms: float = 0.0
    error: str = ""


# ── Kernel ───────────────────────────────────────────────────────────────────


class Kernel:
    """Deterministic, idempotent boot orchestrator.

    boot() executes exactly once. Subsequent calls return the existing instance.
    """

    _instance: "Kernel | None" = None
    _booted: bool = False

    def __init__(self) -> None:
        self.registry = RuntimeRegistry()
        # Kernel-owned global discovery facades. Existing Broca/provider and
        # capability buses are attached as compatibility backends below; they
        # remain responsible for their existing behavior, not ownership.
        self.runtime_registry = self.registry
        self.runtime_selector = RuntimeSelector(self.registry)
        self.agent_registry = AgentRegistry()
        self.capability_registry = CapabilityRegistry()
        self.event_bus = EventBus()
        self.lemon: Any = None
        self.persistence: Any = None
        self.process_manager: Any = None
        self.resource_manager: ResourceManager | None = None
        self._boot_phases: list[BootPhase] = []
        self._boot_start: float = 0.0
        self._health: dict[str, ComponentHealth] = {}
        self._broca_registered: bool = False

    @classmethod
    async def boot(cls, app: Any) -> "Kernel":
        """Boot the kernel exactly once. Returns existing instance on repeat calls."""
        if cls._booted and cls._instance is not None:
            logger.debug("Kernel already booted — returning existing instance")
            return cls._instance

        kernel = cls()
        cls._instance = kernel
        cls._booted = True
        app.state.kernel = kernel
        app.state.runtime_registry = kernel.runtime_registry
        app.state.agent_registry = kernel.agent_registry
        app.state.capability_registry = kernel.capability_registry
        app.state.runtime_selector = kernel.runtime_selector
        kernel._boot_start = time.time()

        try:
            # this is the kernel boot system
            await kernel._boot_sequence(app)
        except Exception:
            cls._booted = False
            cls._instance = None
            raise

        return kernel

    # Phases whose failure or timeout must NOT abort boot: the app degrades
    # and runs without them. Everything else is required for the cognitive
    # cycle (plan/execute/simulate/compare/learn) and aborts boot on failure.
    # These are the network-dependent, optional subsystems — an unreachable
    # one (e.g. NATS/Neo4j/Elasticsearch/Influx behind data-routing or OQL)
    # must never wedge startup, which previously hung boot indefinitely.
    _OPTIONAL_PHASES = frozenset(
        {
            "Providers",
            "Policy Control Plane",
            "Runtime Identity",
            "SittingFace",
            "Graph Store",
            "Data Routing",
            "OQL Engine",
            "CodeGen Runtime",
            "Hybrid Router",
        }
    )

    async def _boot_sequence(self, app: Any) -> None:
        """Execute the deterministic boot sequence."""
        phases = [
            ("Configuration", self._phase_config(app)),
            ("Resource Manager", self._phase_resource_manager(app)),
            ("Observability", self._phase_observability(app)),
            ("Persistence", self._phase_persistence(app)),
            ("Runtime (Wolverine)", self._phase_wolverine(app)),
            ("Providers", self._phase_providers(app)),
            ("Agents (Broca)", self._phase_broca(app)),
            ("Policy Control Plane", self._phase_pcp(app)),
            ("Runtime Identity", self._phase_identity(app)),
            ("Policy (Bellman)", self._phase_policy(app)),
            ("Observer", self._phase_observer(app)),
            ("Learning", self._phase_learning(app)),
            ("SittingFace", self._phase_sittingface(app)),
            ("Execution Graph", self._phase_execution_graph(app)),
            ("Graph Store", self._phase_graph_store(app)),
            ("Data Routing", self._phase_data_routing(app)),
            ("OQL Engine", self._phase_oql(app)),
            ("Process Manager", self._phase_process_manager(app)),
            ("Audit & Identity", self._phase_audit_identity(app)),
            ("Cognitive Runtime", self._phase_cognitive(app)),
            ("Simulation Runtime", self._phase_simulation(app)),
            ("Comparator Runtime", self._phase_comparator(app)),
            ("Planetary Runtime", self._phase_planetary(app)),
            ("CodeGen Runtime", self._phase_codegen(app)),
            ("Hybrid Router", self._phase_hybrid_router(app)),
        ]

        # Bound every phase so one unbounded await (an unreachable network
        # dependency with no client-side timeout) can't hang startup forever.
        phase_timeout = float(os.getenv("KERNEL_PHASE_TIMEOUT", "60"))

        for name, coro in phases:
            phase = BootPhase(name=name, state="running")
            self._boot_phases.append(phase)
            start = time.time()
            optional = name in self._OPTIONAL_PHASES
            try:
                await asyncio.wait_for(coro, timeout=phase_timeout)
                phase.state = "done"
                phase.duration_ms = (time.time() - start) * 1000
            except Exception as exc:
                phase.duration_ms = (time.time() - start) * 1000
                detail = (
                    f"timed out after {phase_timeout:.0f}s"
                    if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    else str(exc)
                )
                if optional:
                    phase.state = "skipped"
                    phase.error = detail
                    self._health[name.lower()] = ComponentHealth(
                        name=name,
                        state=HealthState.DEGRADED,
                        message=detail,
                    )
                    logger.warning(
                        "Boot phase %r degraded (%s) — continuing without it",
                        name,
                        detail,
                    )
                    continue
                phase.state = "failed"
                phase.error = detail
                logger.error("Boot phase %r failed: %s", name, detail)
                raise

        await self._health_check(app)
        self.validate_architecture()
        self._print_boot_summary()

        await self.event_bus.publish(
            "kernel.boot.completed",
            {
                "runtimes": self.registry.names(),
                "duration_ms": (time.time() - self._boot_start) * 1000,
            },
        )

    # 1. Load Configuration
    #
    # Purpose:
    # Load and validate all runtime configuration required to boot the Cognitive
    # OS. Configuration is sourced from environment variables and configuration
    # files, providing a centralized mechanism for configuring infrastructure,
    # runtime behavior, security, providers, and external services.
    #
    # Architectural Role:
    # The Configuration Manager is the first component initialized during boot,
    # as every subsequent subsystem depends on its configuration. It resolves,
    # validates, and exposes configuration through a unified interface to the
    # entire runtime.
    #
    # Configuration Includes:
    # - Runtime settings (environment, debug, logging levels)
    # - Database and persistence connections
    # - External agent provider credentials and endpoints
    # - LLM and AI model configuration
    # - Message bus and networking configuration
    # - Security, authentication, and secrets
    # - Feature flags and runtime options
    # - Resource limits and performance tuning
    #
    # Result:
    # A validated, immutable configuration object available to all runtime
    # components, ensuring consistent and deterministic initialization.

    async def _phase_config(self, app: Any) -> None:
        from pathlib import Path
        from src.monkey_brain.api.bootstrap import load_dotenv

        load_dotenv(Path(__file__).parents[3] / ".env")
        from services.common.secrets import validate_hmac_secrets_from_env

        validate_hmac_secrets_from_env()
        self._health["config"] = ComponentHealth(name="config", state=HealthState.HEALTHY)

    # 2. Initialize External Agent Providers
    #
    # Purpose:
    # Initialize the external agent provider registry, enabling MonkeyBrain to
    # discover, connect to, and execute agents hosted by external platforms.
    # These providers extend the runtime with third-party capabilities while
    # presenting a unified execution interface to the Kernel and Runtime.
    #
    # Architectural Role:
    # The Provider Registry acts as the integration layer between the Cognitive
    # OS and external agent ecosystems. It manages provider registration,
    # authentication, capability discovery, and execution routing.
    #
    # Supported Providers:
    # - OpenClaw -> External AI agent discovery and execution
    # - n8n       -> Workflow automation and tool orchestration
    #
    # Key Responsibilities:
    # - Register external providers
    # - Establish provider connections
    # - Discover available agents and capabilities
    # - Route execution requests to the appropriate provider
    # - Monitor provider health and availability
    # - Provide a unified interface for external agent execution
    #
    # Result:
    # A fully initialized provider registry capable of discovering and invoking
    # agents and workflows across supported external platforms.

    async def _phase_resource_manager(self, app: Any) -> None:
        self.resource_manager = ResourceManager()

        from src.monkey_brain.persistence.mem0_adapter import Mem0Resource

        # Mem0 is not configured in this deployment, so this resource never
        # connects — that's expected, not a bug (docs/examples.md documents
        # the resulting /health "mem0": "disconnected" as a real, non-fatal
        # degraded dependency; it's not in the required-subsystem set).
        # Registered anyway (rather than skipped) so /health has something
        # real to report on, instead of Mem0's status silently not existing.
        self.resource_manager.register(Mem0Resource())

        from src.monkey_brain.kernel.provider_registry import init_providers

        app.state._provider_registry = init_providers()
        self.agent_registry.attach_provider_registry(app.state._provider_registry)
        for provider in app.state._provider_registry.list_providers():
            logger.info("[kernel] Provider registered: %s (available=%s)", provider["name"], provider["available"])

        await self.resource_manager.initialize_all()
        self._health["resource_manager"] = ComponentHealth(name="resource_manager", state=HealthState.HEALTHY)

    # 3. Initialize Observability (Lemon)
    #
    # Purpose:
    # Initialize the Lemon observability runtime, which passively monitors the
    # Cognitive Runtime by collecting metrics, traces, logs, health signals,
    # alerts, and semantic cognitive telemetry. Lemon provides complete visibility
    # into runtime behavior without ever influencing execution.
    #
    # Architectural Invariant:
    # Lemon observes everything. Lemon changes nothing. Observability must never
    # mutate runtime state, alter scheduling, influence planning or learning, or
    # affect governance decisions.
    #
    # Key APIs:
    #
    # Classical Observability
    # - counter(name, increment=1, **tags)             # Increment a counter
    # - gauge(name, value, **tags)                     # Record current value
    # - histogram(name, value, **tags)                 # Record value distribution
    # - start_trace(name, trace_id) / finish_trace()   # Distributed tracing
    # - info(), warn(), error()                        # Structured logging
    # - health_check(name, status)                     # Component health monitoring
    # - alert(name, message, severity)                 # Operational alerting
    #
    # Semantic Observability
    # - observe_intent(raw_text, classified_intent, confidence)
    # - observe_goal(goal_id, goal_text, status)
    # - observe_pipeline_step(pipeline_id, step_name, capability, status)
    # - observe_agent_reasoning(agent_type, perception, decision, latency_ms)
    # - observe_world_model(simulation_id, entity, predicted, actual, accuracy)
    # - observe_governance(policy_path, decision, principal)
    # - observe_learning(capability, state_key, q_before, q_after, reward)
    #
    # Dashboard Views
    # - dashboard("intent" | "agent" | "pipeline" | "world_model" |
    #             "governance" | "learning" | "runtime" | "all")
    #
    # Result:
    # A fully initialized observability layer that provides comprehensive
    # operational and cognitive telemetry across the entire runtime while
    # remaining strictly read-only.

    async def _phase_observability(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_lemon

        self.lemon = await init_lemon(app)
        app.state.lemon = self.lemon
        self._health["observability"] = ComponentHealth(name="observability", state=HealthState.HEALTHY)

    # 4. Initialize Persistence Manager
    #
    # Purpose:
    # Initialize the unified Persistence Manager responsible for managing all
    # runtime persistence across short-term, long-term, and ephemeral memory.
    # It provides a single abstraction over multiple storage backends, allowing
    # each type of data to be stored in the most appropriate database while
    # presenting a consistent interface to the rest of the runtime.
    #
    # Architectural Role:
    # The Persistence Manager is the storage layer of the Cognitive OS. It
    # transparently routes reads and writes to specialized adapters without
    # exposing database-specific implementation details to runtime components.
    #
    # Storage Adapters:
    # - Elasticsearch -> Search indexes, logs, audit records
    # - Mem0          -> Short-term memory, system state, simulation state
    # - InfluxDB      -> Sensor data, event streams, time-series snapshots,
    #                    Lemon metrics and telemetry
    # - Redis         -> Session data, ephemeral runtime state, caching
    # - MongoDB       -> Master data, entities, embeddings, capability metadata,
    #                    agent metadata
    # - Neo4j         -> Entity relationships, knowledge graph, semantic graph
    #
    # Result:
    # A fully initialized persistence layer providing unified access to all
    # runtime memory and storage services while abstracting the underlying
    # database implementations.

    async def _phase_persistence(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_persistence

        self.persistence = await init_persistence(app, self.lemon)
        self._health["persistence"] = ComponentHealth(name="persistence", state=HealthState.HEALTHY)

        from src.monkey_brain.kernel.resources.mongo import MongoResource
        from src.monkey_brain.kernel.resources.redis import RedisResource
        from src.monkey_brain.kernel.resources.mem0 import Mem0Resource

        adapters = getattr(self.persistence, "_adapters", {})
        if "mongodb" in adapters:
            self.resource_manager.register(MongoResource(adapters["mongodb"]))
        if "redis" in adapters:
            self.resource_manager.register(RedisResource(adapters["redis"]))
        if "mem0" in adapters:
            self.resource_manager.register(Mem0Resource(adapters["mem0"]))

    # 5. Initialize Wolverine Runtime (Execution Engine)
    #
    # Purpose:
    # Initialize Wolverine, the primary execution engine of the MonkeyBrain runtime.
    # Wolverine is responsible for executing the Kernel's decisions by running
    # capability pipelines, orchestrating agent execution, and managing the
    # lifecycle of all runtime processes. It serves as the execution layer of the
    # Cognitive OS, transforming planned actions into concrete execution.
    #
    # Architectural Role:
    # Wolverine is the "CPU" of the Cognitive OS. The Kernel decides *what* should
    # happen, while Wolverine determines *how* it is executed by dispatching work
    # through the capability pipeline and coordinating runtime resources.
    #
    # Key Responsibilities:
    # - Execute capability pipelines and execution graphs
    # - Execute agents and capabilities on behalf of the Kernel
    # - Manage process lifecycles through the scheduler
    # - Maintain process queues (Ready, Waiting, Blocked, Running, Completed, Failed)
    # - Execute work using worker pools for concurrent processing
    # - Route messages through the runtime message bus
    # - Coordinate execution state, retries, timeouts, and failures
    #
    # Result:
    # A fully initialized execution engine capable of reliably scheduling,
    # dispatching, and executing cognitive workloads across the MonkeyBrain runtime.

    async def _phase_wolverine(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_runtime

        app.state._wolverine = await init_runtime(app, self.persistence, self.lemon)
        self._health["wolverine"] = ComponentHealth(name="wolverine", state=HealthState.HEALTHY)

    # 6. Initialize External Agent Resolution
    #
    # Purpose:
    # Initialize the external agent resolution system, enabling the runtime to
    # discover, register, and execute agents that are not available locally.
    # The resolver provides a seamless mechanism for locating agents across
    # local registries and external providers while transparently caching
    # discovered agents for future execution.
    #
    # Architectural Role:
    # The Agent Resolver bridges the local runtime with external agent ecosystems,
    # ensuring that agent execution is location-independent. Local agents are
    # preferred for performance, while external providers are queried on demand.
    # Newly discovered agents are registered locally to minimize future lookup
    # latency.
    #
    # Agent Resolution Flow:
    # 1. Check the local Broca Agent Registry.
    # 2. If not found, query the Provider Registry across all configured providers.
    # 3. If an agent is discovered:
    #    - Register it in the local Broca registry.
    #    - Execute it using the provider's transport mechanism.
    # 4. If no provider can resolve the agent:
    #    - Return an error or fall back to the Code Generation runtime.
    #
    # Result:
    # A unified agent discovery and execution layer capable of transparently
    # resolving local and external agents while supporting automatic registration
    # and fallback strategies.

    # TODO: needs to inject the providers into the wolverine runtime need dependency inversion

    async def _phase_broca(self, app: Any) -> None:
        if self._broca_registered:
            self._health["broca"] = ComponentHealth(
                name="broca", state=HealthState.HEALTHY, message="already registered"
            )
            return
        from src.monkey_brain.api.bootstrap import init_broca

        await init_broca(app, app.state._wolverine)
        try:
            from broca.registry import get_registry

            self.agent_registry.attach_broca(get_registry())
        except Exception as exc:
            logger.debug("[kernel] Broca registry adapter unavailable: %s", exc)
        self._broca_registered = True
        self._health["broca"] = ComponentHealth(name="broca", state=HealthState.HEALTHY)

        # Real AgentBus + CapabilityBus wiring — the last phase where
        # Wolverine (app.state._wolverine), the external ProviderRegistry
        # (app.state._provider_registry, set in _phase_resource_manager),
        # and Broca are all guaranteed ready. Previously AgentBus was a
        # real class nothing ever constructed, and Runtime had no bus
        # attribute at all — both confirmed by reading the source. This
        # makes both real and live rather than only reachable by
        # importing the class directly.
        try:
            from src.monkey_brain.kernel.execute.agents.bus import AgentBus
            from src.monkey_brain.kernel.execute.capability_bus import CapabilityBus

            provider_registry = getattr(app.state, "_provider_registry", None)
            agent_bus = AgentBus(provider_registry=provider_registry)
            capability_bus = CapabilityBus(app.state._wolverine, agent_bus, provider_registry)
            app.state._agent_bus = agent_bus
            app.state._capability_bus = capability_bus
            app.state._wolverine.capability_bus = capability_bus
        except Exception as exc:
            logger.warning("[kernel] AgentBus/CapabilityBus wiring failed (non-fatal): %s", exc)

    async def _phase_providers(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_providers

        await init_providers(app, app.state._wolverine)
        self._health["providers"] = ComponentHealth(name="providers", state=HealthState.HEALTHY)

        from src.monkey_brain.kernel.resources.ollama import OllamaResource
        from src.monkey_brain.kernel.resources.openclaw import OpenClawResource

        self.resource_manager.register(OllamaResource())
        self.resource_manager.register(OpenClawResource())

    # 7. Initialize the Policy Control Plane (PCP)
    #
    # Purpose:
    # Initialize the Policy Control Plane (PCP), the authoritative policy
    # distribution and governance service for the MonkeyBrain mesh. The PCP acts
    # as the single source of truth for security, authorization, identity, and
    # trust policies, ensuring that every runtime component operates under a
    # consistent governance model.
    #
    # Architectural Role:
    # The Policy Control Plane is responsible for distributing and managing
    # policies across the runtime. It centralizes authorization decisions and
    # trust relationships while providing policy bundles to enforcement points
    # throughout the Cognitive OS.
    #
    # Policy Responsibilities:
    # - Manage role-to-permission mappings (RBAC)
    # - Bind workload identities to runtime components
    # - Establish and maintain cross-domain trust relationships
    # - Distribute and version OPA policy bundles
    # - Synchronize policies across the MonkeyBrain mesh
    # - Serve as the authoritative source for runtime governance
    #
    # Result:
    # A fully initialized policy control plane capable of distributing,
    # synchronizing, and governing security and authorization policies across
    # the entire MonkeyBrain runtime.

    async def _phase_pcp(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_pcp

        app.state._pcp = await init_pcp(app, self.persistence)
        self._health["pcp"] = ComponentHealth(
            name="pcp",
            state=HealthState.HEALTHY if app.state._pcp else HealthState.DEGRADED,
        )

        from src.monkey_brain.kernel.resources.nats import NatsResource

        self.resource_manager.register(NatsResource(app.state._pcp))

    # 8. Initialize the Runtime Identity Layer
    #
    # Purpose:
    # Initialize the Runtime Identity Layer, which establishes the identity,
    # trust, and security context for the MonkeyBrain runtime. It provides the
    # foundation for secure inter-agent and inter-runtime authentication,
    # authorization, and trust across the cognitive mesh.
    #
    # Architectural Role:
    # The Runtime Identity Layer defines the runtime's unique identity and
    # security posture. It enables authenticated communication between agents,
    # runtimes, and external systems while enforcing access control and trust
    # policies throughout the mesh.
    #
    # Runtime Identity:
    # - runtime_id       -> Unique runtime identifier (UUID)
    # - runtime_type     -> personal | community | enterprise | institution | government
    # - owner            -> Runtime owner or controlling principal
    # - jurisdiction     -> Legal or regulatory jurisdiction
    # - public_key_pem   -> Ed25519 public key for authentication
    # - certificate      -> Optional X.509 certificate reference
    # - capabilities     -> Declared runtime capabilities and permissions
    # - trust_policies   -> Trust and access policies enforced by the runtime
    #
    # Key Responsibilities:
    # - Establish the runtime's cryptographic identity
    # - Enable secure inter-agent and inter-runtime authentication
    # - Define security boundaries and access controls
    # - Publish runtime identity to the MonkeyBrain mesh
    # - Enforce trust policies for incoming and outgoing communications
    #
    # Result:
    # A fully initialized identity layer that enables trusted, authenticated,
    # and policy-driven communication across the MonkeyBrain ecosystem.

    async def _phase_identity(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_runtime_identity

        await init_runtime_identity(app.state._wolverine)
        self._health["identity"] = ComponentHealth(name="identity", state=HealthState.HEALTHY)

    # 9. Initialize the Learning Policy Engine
    #
    # Purpose:
    # Initialize the Learning Policy Engine, which manages the runtime's adaptive
    # decision-making and reinforcement learning capabilities. This is distinct
    # from the security Policy Control Plane and is responsible for learning
    # optimal execution strategies over time using Q-learning.
    #
    # Architectural Role:
    # The Learning Policy Engine maintains the runtime's knowledge of past
    # experiences, updating policies based on observed outcomes and rewards.
    # It enables continuous optimization by balancing exploration of new
    # strategies with exploitation of previously learned behavior. it uses
    # bellman policy for storing transient values
    #
    # Learning Components:
    # - exploration_rate  -> Probability of exploring vs. exploiting (default: 0.1)
    # - learning_rate     -> Rate at which Q-values are updated (default: 0.1)
    # - discount_factor   -> Weight assigned to future rewards (default: 0.95)
    # - TransitionTable   -> Stores observed state-action transitions
    # - RewardModel       -> Computes rewards from execution outcomes
    # - Learner           -> Applies the Q-learning update rule
    # - PolicyStore       -> Authoritative store of learned Q-values
    # - LLMExplorer       -> Generates candidate workflows for exploration
    #
    # Key Responsibilities:
    # - Initialize reinforcement learning parameters
    # - Maintain the execution policy (Q-table)
    # - Record state transitions and rewards
    # - Continuously update policies based on experience
    # - Generate and evaluate alternative execution strategies
    # - Improve decision quality through learning
    #
    # Result:
    # A fully initialized learning engine capable of adapting execution policies
    # over time through reinforcement learning and intelligent workflow exploration.

    async def _phase_policy(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_policy

        app.state._policy = await init_policy(app, self.persistence, self.lemon)
        self._health["policy"] = ComponentHealth(name="policy", state=HealthState.HEALTHY)

    # 10. Initialize Learning
    #
    # Purpose:
    # Initialize the Cognitive Learning Engine, enabling the runtime to learn
    # continuously from observations, executions, simulations, and outcomes.
    # The learning subsystem updates the runtime's knowledge, improves future
    # decision-making, and refines execution strategies based on accumulated
    # experience.
    #
    # Architectural Role:
    # The Learning Engine closes the cognitive feedback loop by transforming
    # experience into knowledge. It consumes observations and rewards produced
    # during execution, updates learned policies, refines beliefs, and improves
    # future planning without affecting past execution.
    #
    # Key Responsibilities:
    # - Consume execution outcomes and observations
    # - Update Q-values and learning policies
    # - Learn state-action transition probabilities
    # - Refine world model predictions
    # - Improve planning and execution accuracy
    # - Capture lessons learned for future decisions
    # - Support continuous adaptation of the Cognitive Runtime
    #
    # Result:
    # A fully initialized learning subsystem capable of continuously improving
    # the MonkeyBrain runtime through reinforcement learning, experience, and
    # cognitive feedback.

    async def _phase_learning(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_learning

        app.state._learning = await init_learning(app, self.lemon)
        self._health["learning"] = ComponentHealth(name="learning", state=HealthState.HEALTHY)

    # 11. Initialize the Observer
    #
    # Purpose:
    # Initialize the Observer, the perception layer of the Cognitive Runtime,
    # responsible for collecting information from the external world, internal
    # runtime state, and executing agents. The Observer converts raw events into
    # structured observations that drive the cognitive loop.
    #
    # Architectural Role:
    # The Observer is the "Observe" stage of the cognitive cycle
    # (Observe → Believe → Plan → Execute → Learn). It gathers signals from
    # sensors, events, databases, APIs, and runtime components, normalizes them,
    # and publishes observations for belief updates and downstream reasoning.
    #
    # Key Responsibilities:
    # - Collect observations from internal and external sources
    # - Monitor runtime, agent, and environment state
    # - Normalize and enrich raw events into structured observations
    # - Publish observations to the cognitive pipeline
    # - Trigger belief updates and context refreshes
    # - Maintain observation timestamps and provenance
    #
    # Result:
    # A fully initialized observation subsystem capable of continuously perceiving
    # the runtime and its environment, providing accurate and timely inputs to the
    # cognitive reasoning process.

    async def _phase_observer(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_observer

        app.state._observer = await init_observer(app, self.lemon)
        self._health["observer"] = ComponentHealth(name="observer", state=HealthState.HEALTHY)

    # 12. Initialize SittingFace Knowledge Packs
    #
    # Purpose:
    # Initialize the SittingFace Knowledge Packs, which provide the runtime with
    # domain-specific knowledge, ontologies, taxonomies, semantic models, and
    # reference data required for cognitive reasoning. Knowledge packs extend the
    # runtime's understanding of the world without modifying the core engine.
    #
    # Architectural Role:
    # SittingFace serves as the semantic knowledge layer of the Cognitive OS.
    # Knowledge Packs are loaded during startup to provide reusable knowledge
    # across planning, reasoning, simulation, entity resolution, and execution.
    # Multiple packs can be installed, versioned, and composed to support
    # different domains.
    #
    # Key Responsibilities:
    # - Load installed knowledge packs
    # - Register ontologies, schemas, and taxonomies
    # - Initialize semantic entities and relationships
    # - Load world knowledge and domain rules
    # - Register embeddings and semantic indexes
    # - Validate compatibility and version dependencies
    # - Manage and complie soma charts / EATAS charts
    #
    # Result:
    # A fully initialized semantic knowledge layer, enabling the runtime to
    # reason over rich domain knowledge through reusable, versioned Knowledge
    # Packs.

    async def _phase_sittingface(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_sittingface

        await init_sittingface(app, app.state._wolverine, self.lemon)
        compiler = getattr(app.state, "somatic_compiler", None)
        self._health["sittingface"] = ComponentHealth(
            name="sittingface",
            state=HealthState.HEALTHY if compiler else HealthState.DEGRADED,
            message=f"{len(getattr(compiler, 'charts', []))} charts" if compiler else "not loaded",
        )

        from src.monkey_brain.kernel.resources.sittingface import SittingFaceResource

        self.resource_manager.register(SittingFaceResource(app))

    # 13. Initialize the Execution Graph
    #
    # Purpose:
    # Initialize the Execution Graph, the procedural graph that represents the
    # executable workflow for the current cognitive task. The graph defines the
    # ordered sequence of capabilities, dependencies, branching logic, and
    # execution state required to achieve a goal.
    #
    # Architectural Role:
    # The Execution Graph is the runtime's procedural memory. Unlike SittingFace,
    # which stores semantic knowledge about the world, the Execution Graph stores
    # the steps required to accomplish a specific objective. It is created during
    # planning, executed by Wolverine, updated during execution, and refined
    # through learning.
    # Discovery order:
    #   Load Providers  →  Load Agents  →  Load Capabilities  →  Build Graph  →  Index
    #
    # Key Responsibilities:
    # - Build execution graphs from plans and goals
    # - Represent tasks as nodes and dependencies as edges
    # - Track execution state for every node
    # - Support sequential, parallel, conditional, and iterative execution
    # - Maintain checkpoints and recovery points
    # - Persist execution state for long-running workflows
    # - Provide execution history for learning and optimization
    # - Initiate the capabilities bus to get capabilities and build the graph
    # - Initiate the Agent bus to get the agents and add them to the graph
    # - Discover Agents and capabilities that are available
    #
    # Result:
    # A fully initialized Execution Graph capable of representing, tracking, and
    # coordinating the complete lifecycle of cognitive workflows across the
    # MonkeyBrain runtime.

    async def _phase_execution_graph(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_execution_graph

        app.state._execution_graph = await init_execution_graph(app, app.state._wolverine, self.lemon)
        self._health["execution_graph"] = ComponentHealth(name="execution_graph", state=HealthState.HEALTHY)

    # 14. Initialize the Graph Store
    #
    # Purpose:
    # Initialize the Graph Store, the persistent graph database responsible for
    # storing, querying, and managing the runtime's graph structures. It provides
    # durable storage for semantic relationships, execution graphs, world models,
    # and other graph-based knowledge used throughout the Cognitive Runtime.
    #
    # Architectural Role:
    # The Graph Store is the persistence layer for graph data within MonkeyBrain.
    # It separates in-memory graph execution from durable graph storage, enabling
    # graphs to be persisted, versioned, queried, and shared across runtime
    # sessions. While the Execution Graph manages the active workflow, the Graph
    # Store provides long-term storage and retrieval of graph structures.
    #
    # Key Responsibilities:
    # - Persist execution graphs and workflow state
    # - Store semantic knowledge graphs and entity relationships
    # - Manage world model graphs and simulation graphs
    # - Support graph versioning and snapshots
    # - Enable graph queries, traversal, and analytics
    # - Restore graph state during runtime startup and recovery
    # - Synchronize graph updates with the persistence layer
    #
    # Result:
    # A fully initialized Graph Store capable of providing durable, queryable,
    # and versioned storage for all graph structures used by the MonkeyBrain
    # Cognitive Runtime.

    async def _phase_graph_store(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_graph_store

        app.state._graph_store = await init_graph_store(app, app.state._wolverine, app.state._execution_graph)
        self._health["graph_store"] = ComponentHealth(
            name="graph_store",
            state=HealthState.HEALTHY
            if app.state._graph_store and app.state._graph_store.is_connected()
            else HealthState.DEGRADED,
        )

        # Neo4jResource used to be registered here, wrapping app.state._graph_store
        # for a boot-time connectivity check. init_graph_store's own docstring says
        # it builds "the graph store (tensor + KV, replacing Neo4j)" -- it returns a
        # CompositeGraphStore, never a Neo4j-backed GraphStore, so Neo4jResource's
        # getattr(store, "_driver", None) always found nothing and reported "neo4j
        # driver not installed or connect failed" on every single boot, regardless
        # of whether Neo4j was actually reachable (confirmed live: SemanticGraph and
        # Neo4jBackedKnowledgeGraph both connect to the same bolt://localhost:7687
        # fine). Removed rather than repointed -- this Kernel phase has no actual
        # Neo4j-backed component to check; real Neo4j connectivity for the parts of
        # the system that still use it is verified by those components directly.

        # Last resource-registering phase — every resource from persistence,
        # sittingface, pcp, providers, and this phase is registered by now.
        # Required resources get their full retry budget inline (they've
        # already connected during their own init_* call above, so this is
        # normally a fast READY check, not a fresh connection attempt).
        # Optional resources get a single fast check, then
        # start_background_retry() keeps trying them without blocking boot.
        await self.resource_manager.initialize_all()
        self.resource_manager.start_background_retry()

    # 15. Initialize the Data Routing Middleware
    #
    # Purpose:
    # Initialize the Data Routing Middleware, the intelligent data access layer
    # responsible for selecting the appropriate database adapter for each query.
    # It provides a unified interface for cognitive actors to access heterogeneous
    # data stores without requiring knowledge of the underlying database
    # technology.
    #
    # Architectural Role:
    # The Data Routing Middleware acts as the storage abstraction layer of the
    # Cognitive OS. It analyzes each data request and transparently routes it to
    # the most appropriate backend—relational, document, graph, key-value,
    # time-series, or search—based on the query, data model, and execution
    # context. This allows agents to reason over data rather than database
    # implementations.
    #
    # Key Responsibilities:
    # - Register and manage database adapters
    # - Select the optimal database for each request
    # - Route queries to relational, document, graph, cache, search, or
    #   time-series databases
    # - Abstract database-specific APIs behind a unified interface
    # - Support heterogeneous data storage architectures
    # - Handle adapter discovery, initialization, and health management
    # - Gracefully degrade when optional database adapters are unavailable
    # - Enable database-independent cognitive reasoning and execution
    #
    # Registered Adapters:
    # - PostgreSQL
    # - MongoDB
    # - Neo4j
    # - Redis
    # - InfluxDB
    # - Elasticsearch
    # - SQLite
    # - MySQL
    #
    # Result:
    # A fully initialized Data Routing Middleware capable of intelligently
    # selecting and routing data requests to the most appropriate database,
    # providing a unified, database-agnostic persistence layer for the
    # MonkeyBrain Cognitive Runtime.

    async def _phase_data_routing(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_data_routing

        app.state._data_routing = await init_data_routing(app, self.lemon)
        self._health["data_routing"] = ComponentHealth(
            name="data_routing",
            state=HealthState.HEALTHY,
        )

    # 16. Initialize the Unified Query Language (UQL)
    #
    # Purpose:
    # Initialize the Unified Query Language (UQL), the abstraction layer that
    # enables cognitive actors to discover, retrieve, and manipulate data across
    # heterogeneous data sources using a single declarative query interface.
    #
    # Architectural Role:
    # UQL serves as the runtime's universal data access layer. Rather than
    # interacting directly with individual databases, actors issue UQL queries,
    # which are translated into the native query language of the target datastore.
    # This decouples cognitive reasoning from storage technology and provides a
    # consistent data discovery experience across the platform.
    #
    # Key Responsibilities:
    # - Provide a unified query interface for all supported datastores
    # - Translate UQL into native database queries
    # - Discover data across relational, document, graph, vector, and time-series databases
    # - Optimize query execution and routing
    # - Federate queries spanning multiple data sources
    # - Normalize and unify query results
    # - Enforce access control and query policies
    # - Cache and optimize frequently executed queries
    #
    # Result:
    # A fully initialized Unified Query Language capable of providing transparent,
    # secure, and efficient data discovery across all supported databases,
    # allowing cognitive actors to access information without knowledge of the
    # underlying storage technologies.

    async def _phase_oql(self, app: Any) -> None:
        from src.monkey_brain.api.bootstrap import init_oql

        app.state._oql = await init_oql(app, self.lemon)
        self._health["oql"] = ComponentHealth(
            name="oql",
            state=HealthState.HEALTHY,
        )

    # 17. Initialize the Process Manager
    #
    # Purpose:
    # Initialize the Process Manager, the runtime subsystem responsible for the
    # lifecycle management of all cognitive processes. It creates, schedules,
    # monitors, suspends, resumes, and terminates processes executing within the
    # MonkeyBrain runtime. all cognitive process ie actors are manages and scheduled
    # by this process manager
    #
    # Architectural Role:
    # The Process Manager is the operating system layer for cognitive execution.
    # It manages Runtime Process Control Blocks (RPCBs), coordinates with
    # Wolverine Runtime for execution, and ensures that processes are scheduled,
    # prioritized, isolated, and recoverable throughout their lifecycle.
    #
    # Key Responsibilities:
    # - Create and register cognitive processes
    # - Manage Runtime Process Control Blocks (RPCBs)
    # - Schedule, suspend, resume, and terminate processes
    # - Coordinate execution with Wolverine
    # - Track process state, priority, and resource usage
    # - Manage process dependencies and execution queues
    # - Support checkpointing and recovery for long-running processes
    # - Monitor process health and lifecycle events
    #
    # Result:
    # A fully initialized Process Manager capable of orchestrating the complete
    # lifecycle of cognitive processes, providing reliable scheduling, execution,
    # and resource management across the MonkeyBrain runtime.

    async def _phase_process_manager(self, app: Any) -> None:
        from src.monkey_brain.kernel.process.manager import ProcessManager
        from src.monkey_brain.kernel.process.checkpoint import MongoCheckpointStore

        checkpoint_store = None
        mongo_client = self._find_mongo_client(app)
        if mongo_client is not None:
            try:
                checkpoint_store = MongoCheckpointStore(mongo_client)
            except Exception:
                logger.debug("_phase_process_manager: suppressed exception", exc_info=True)

        self.process_manager = ProcessManager(
            event_bus=self.event_bus,
            checkpoint_store=checkpoint_store,
            graph_store=getattr(app.state, "_graph_store", None),
        )
        app.state.process_manager = self.process_manager
        self._health["process_manager"] = ComponentHealth(
            name="process_manager",
            state=HealthState.HEALTHY,
            message="MongoDB" if checkpoint_store else "in-memory",
        )

    # 18. Initialize the Audit Logs
    #
    # Purpose:
    # Initialize the Audit Logging subsystem, responsible for maintaining an
    # immutable record of significant runtime events, decisions, actions, and
    # system changes. Audit logs provide accountability, traceability, compliance,
    # and forensic analysis across the MonkeyBrain runtime.
    #
    # Architectural Role:
    # The Audit Logging subsystem operates independently of application logic,
    # recording critical events generated by cognitive actors, runtime services,
    # and infrastructure components. Unlike observability, which focuses on system
    # health and performance, audit logs provide a tamper-resistant historical
    # record of "who did what, when, and why."
    #
    # Key Responsibilities:
    # - Record actor and system actions
    # - Log authentication and authorization events
    # - Capture policy evaluations and enforcement decisions
    # - Track workflow execution and state transitions
    # - Record data access and modification events
    # - Maintain immutable, timestamped audit records
    # - Support compliance, governance, and forensic investigations
    # - Enable secure audit log retention and retrieval
    #
    # Result:
    # A fully initialized Audit Logging subsystem capable of providing secure,
    # immutable, and comprehensive audit trails for every significant operation
    # performed within the MonkeyBrain Cognitive Runtime.

    async def _phase_audit_identity(self, app: Any) -> None:
        """Wire audit log to durable storage and initialize runtime identity."""
        from src.monkey_brain.kernel.audit import get_audit_log
        from src.monkey_brain.kernel.storage import AppendOnlyLog
        from src.monkey_brain.kernel.identity import create_identity

        # Initialize runtime identity
        identity = create_identity()
        app.state.runtime_identity = identity

        # Wire audit log to durable append-only store
        audit = get_audit_log()

        # TODO: Change to elastic search
        store = AppendOnlyLog(path=os.path.expanduser("~/.monkeybrain/audit"))
        audit.set_store(store)

        # Record boot
        audit.record(
            runtime_id=identity.runtime_id,
            event_type="system",
            action="boot",
            details={"runtime_type": identity.runtime_type, "owner": identity.owner},
        )

        self._health["audit"] = ComponentHealth(
            name="audit",
            state=HealthState.HEALTHY,
            message=f"identity={identity.runtime_id[:8]}",
        )

    # 19. Initialize the Cognitive Runtime
    #
    # Purpose:
    # Initialize the Cognitive Runtime, the core execution environment where
    # cognitive actors perceive, reason, plan, execute, and learn. It orchestrates
    # the complete cognitive lifecycle by coordinating all previously initialized
    # runtime subsystems.
    #
    # Architectural Role:
    # The Cognitive Runtime is the heart of the MonkeyBrain Cognitive OS. It
    # integrates perception, beliefs, planning, execution, learning, memory, and
    # world modeling into a unified cognitive loop. Every actor operates within
    # this runtime, continuously interacting with the shared Semantic Cognitive
    # World Model while pursuing its own goals.
    #
    # Key Responsibilities:
    # - Orchestrate the Observe → Believe → Plan → Execute → Learn cycle
    # - Coordinate actors, capabilities, and runtime services
    # - Manage belief state and cognitive context
    # - Execute workflows through the Execution Graph and Wolverine
    # - Integrate the World Model, SittingFace, and Learning Engine
    # - Synchronize observations, decisions, and state transitions
    # - Maintain actor isolation while enabling collaboration
    # - Drive continuous adaptation through feedback and learning
    #
    # Result:
    # A fully initialized Cognitive Runtime capable of hosting heterogeneous
    # autonomous actors and executing the complete cognitive lifecycle over a
    # shared, continuously evolving Semantic Cognitive World Model.

    async def _phase_cognitive(self, app: Any) -> None:

        from src.monkey_brain.kernel.cognitive_runtime import LegacyCognitiveRuntime
        from src.monkey_brain.kernel.semantic_memory import SemanticMemory
        from src.monkey_brain.kernel.graph_manager import GraphManager

        # load agents and actors from charts
        semantic_memory = SemanticMemory()
        await semantic_memory.initialize()

        # initiate the graph manager for execution graphs generated in realtime
        graph_manager = GraphManager()
        cognitive = await LegacyCognitiveRuntime.boot(
            app,
            lemon=self.lemon,
            persistence=self.persistence,
            event_bus=self.event_bus,
            semantic_memory=semantic_memory,
            graph_manager=graph_manager,
        )
        self.registry.register("cognitive", cognitive)
        self._health["cognitive"] = ComponentHealth(name="cognitive", state=HealthState.HEALTHY)

    # 20. Initialize the Simulation Runtime
    #
    # Purpose:
    # Initialize the Simulation Runtime, the subsystem responsible for predicting
    # the consequences of candidate actions before they are executed. It enables
    # actors to evaluate alternative plans, estimate outcomes, and reduce risk
    # through virtual execution.
    #
    # Architectural Role:
    # The Simulation Runtime serves as the predictive reasoning engine of the
    # Cognitive OS. It executes hypothetical scenarios against the World Model,
    # generating simulated futures that the Cognitive Runtime can compare before
    # committing to real-world actions. Simulation is isolated from live execution
    # and never modifies the production world state.
    #
    # Key Responsibilities:
    # - Execute hypothetical plans in a simulated environment
    # - Predict future world states and execution outcomes
    # - Evaluate alternative strategies and decision paths
    # - Estimate rewards, risks, and policy compliance
    # - Generate simulated observations for comparison
    # - Support Monte Carlo and what-if analysis
    # - Provide feedback to planning and learning components
    # - Maintain isolated simulation state and execution graphs
    #
    # Result:
    # A fully initialized Simulation Runtime capable of safely evaluating
    # candidate actions, predicting outcomes, and improving decision quality
    # before execution within the MonkeyBrain Cognitive Runtime.

    async def _phase_simulation(self, app: Any) -> None:
        from src.monkey_brain.kernel.simulation_runtime import SimulationRuntime

        simulation = await SimulationRuntime.boot(
            app,
            lemon=self.lemon,
            persistence=self.persistence,
            event_bus=self.event_bus,
        )
        self.registry.register("simulation", simulation)
        self._health["simulation"] = ComponentHealth(name="simulation", state=HealthState.HEALTHY)

    # 21. Initialize the Comparator Runtime
    #
    # Purpose:
    # Initialize the Comparator Runtime, the subsystem responsible for comparing
    # predicted outcomes with actual execution results and computing Bellman
    # transition values that drive reinforcement learning. It measures prediction
    # accuracy and generates the learning signals used to improve future decisions.
    #
    # Architectural Role:
    # The Comparator Runtime closes the cognitive feedback loop by evaluating the
    # difference between simulated and observed reality. It compares predicted
    # state transitions, rewards, and outcomes against actual execution, computes
    # temporal-difference (Bellman) values, and forwards these learning signals to
    # the Learning Engine for policy updates.
    #
    # Key Responsibilities:
    # - Compare predicted and actual execution outcomes
    # - Compute Bellman temporal-difference transition values
    # - Calculate prediction error and reward deltas
    # - Measure epistemic loss between expected and observed states
    # - Generate transition records for reinforcement learning
    # - Produce feedback for policy optimization
    # - Record comparison metrics for analysis and learning
    # - Forward learning signals to the Learning Engine
    #
    # Result:
    # A fully initialized Comparator Runtime capable of evaluating execution
    # accuracy, computing Bellman transition values, and supplying continuous
    # reinforcement signals that improve planning, prediction, and decision-making
    # across the MonkeyBrain Cognitive Runtime.

    async def _phase_comparator(self, app: Any) -> None:
        from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime

        comparator = await ComparatorRuntime.boot(
            app,
            lemon=self.lemon,
            persistence=self.persistence,
            event_bus=self.event_bus,
        )
        self.registry.register("comparator", comparator)
        self._health["comparator"] = ComponentHealth(name="comparator", state=HealthState.HEALTHY)

    # 22. Initialize the Planner
    #
    # Purpose:
    # Initialize the Planner, the subsystem responsible for transforming goals,
    # beliefs, and world knowledge into executable plans. The Planner synthesizes
    # optimal execution strategies that achieve an actor's objectives while
    # respecting constraints, policies, and available capabilities.
    #
    # Architectural Role:
    # The Planner is the decision synthesis engine of the Cognitive Runtime.
    # Operating after belief formation and before execution, it reasons over the
    # Semantic Cognitive World Model, available capabilities, and the current
    # belief state to construct an Execution Graph that Wolverine can execute.
    #
    # Key Responsibilities:
    # - Transform goals into executable plans
    # - Generate Execution Graphs from objectives
    # - Reason over the World Model and belief state
    # - Select capabilities and execution strategies
    # - Optimize plans based on cost, reward, and constraints
    # - Support sequential, parallel, and conditional planning
    # - Replan dynamically as observations and beliefs change
    # - Produce plans for simulation and execution
    #
    # Result:
    # A fully initialized Planner capable of generating optimized, executable
    # workflows that enable cognitive actors to achieve their goals through
    # intelligent reasoning over the Semantic Cognitive World Model.

    async def _phase_planetary(self, app: Any) -> None:
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
        from src.monkey_brain.kernel.society.domain import Society

        planetary = PlanetaryRuntime(Society(name="Default Society"))

        cognitive = self.registry.get("cognitive")
        if cognitive is not None:
            sm = getattr(cognitive, "semantic_memory", None)
            if sm is not None and hasattr(planetary, "attach_semantic_memory"):
                planetary.attach_semantic_memory(sm)

        app.state.planetary_runtime = planetary
        self.registry.register("planetary", planetary)
        # api/routes/payments.py's auto-approve UPI simulator fires from a
        # background timer thread with no Request/app.state to read
        # app.state.planetary_runtime from the way every real HTTP route
        # does -- this is the one place that real instance is reachable
        # without one.
        from src.monkey_brain.api.routes.payments import set_default_planetary_runtime

        set_default_planetary_runtime(planetary)
        self._health["planetary"] = ComponentHealth(name="planetary", state=HealthState.HEALTHY)

        # Real NATS publishing for the context stream — additive to the
        # already-working Redis durability, non-fatal if unreachable
        # (see PlanetaryRuntime.connect_nats's own docstring).
        await planetary.connect_nats()

        # Start auto-tick scheduler (every 5 minutes)
        tick_interval = int(os.getenv("AGENTOS_TICK_INTERVAL", "300"))
        planetary.start_auto_tick(interval_seconds=tick_interval)

        # Start the Actor Lifecycle Controller's reconciliation — independent
        # of auto-tick above: this reconciles actor LIFECYCLE (desired vs.
        # observed state — crash recovery, suspend/resume/terminate
        # follow-through), never actor cognition. Two loops (Horizontal
        # Scheduler Scaling, Section 26/27): a fast, event-driven queue
        # drain (ACTOR_RECONCILE_QUEUE_INTERVAL, default 2s — reacts to a
        # registration/desired-state/placement change almost immediately)
        # and a slow full-table backstop sweep (ACTOR_LIFECYCLE_RECONCILE_
        # INTERVAL, default 300s — a crashed actor is still noticed well
        # within one stale-threshold window, ACTOR_LIFECYCLE_STALE_SECONDS,
        # default 600s, even relying on the backstop alone).
        lifecycle_interval = int(os.getenv("ACTOR_LIFECYCLE_RECONCILE_INTERVAL", "300"))
        queue_interval = float(os.getenv("ACTOR_RECONCILE_QUEUE_INTERVAL", "2.0"))
        queue_batch_size = int(os.getenv("ACTOR_RECONCILE_QUEUE_BATCH_SIZE", "50"))
        queue_concurrency = int(os.getenv("ACTOR_RECONCILE_QUEUE_CONCURRENCY", "10"))
        planetary.start_actor_lifecycle_reconciliation(
            interval_seconds=lifecycle_interval,
            queue_interval_seconds=queue_interval,
            queue_batch_size=queue_batch_size,
            queue_concurrency=queue_concurrency,
        )

    # 23. Initialize the Code Generation Runtime
    #
    # Purpose:
    # Initialize the Code Generation Runtime, the subsystem responsible for
    # synthesizing, validating, and evolving executable software artifacts from
    # cognitive intent. It enables the MonkeyBrain runtime to autonomously create,
    # modify, and optimize code, workflows, APIs, and infrastructure definitions.
    #
    # Architectural Role:
    # The Code Generation Runtime is the software synthesis engine of the
    # Cognitive OS. When existing capabilities cannot satisfy an objective, it
    # generates new implementations by reasoning over requirements, architecture,
    # policies, and available libraries. Generated artifacts are verified before
    # becoming deployable capabilities within the runtime.
    #
    # Key Responsibilities:
    # - Generate source code from goals and execution plans
    # - Synthesize APIs, workflows, and software components
    # - Produce infrastructure and deployment artifacts
    # - Validate generated code against architecture and policies
    # - Execute automated testing and verification
    # - Optimize and refactor generated implementations
    # - Package and publish new capabilities to the runtime
    # - Learn from previous generation outcomes to improve future synthesis
    #
    # Result:
    # A fully initialized Code Generation Runtime capable of autonomously
    # producing, validating, and deploying software artifacts, allowing the
    # MonkeyBrain Cognitive Runtime to continuously extend its own capabilities.

    async def _phase_codegen(self, app: Any) -> None:
        from src.monkey_brain.kernel.codegen_runtime import CodeGenRuntime

        cognitive = self.registry.get("cognitive")
        codegen = await CodeGenRuntime.boot(
            app,
            lemon=self.lemon,
            persistence=self.persistence,
            event_bus=self.event_bus,
            process_manager=self.process_manager,
            wolverine=cognitive.wolverine if cognitive else None,
        )
        self.registry.register("codegen", codegen)
        self._health["codegen"] = ComponentHealth(name="codegen", state=HealthState.HEALTHY)

    # 24. Initialize the Gateway Router
    #
    # Purpose:
    # Initialize the Gateway Router, the unified ingress and egress layer for the
    # MonkeyBrain Cognitive Runtime. It provides a single entry point for all
    # external communication, routing requests securely to the appropriate
    # cognitive actors, runtime services, APIs, and capabilities.
    #
    # Architectural Role:
    # The Gateway Router acts as the front door of the Cognitive OS. It receives
    # requests from users, applications, agents, devices, and external systems,
    # applies authentication, authorization, routing, and protocol translation,
    # and forwards requests to the correct runtime component. It also manages
    # outbound communication with external services.
    #
    # Key Responsibilities:
    # - Route inbound requests to runtime services and actors
    # - Expose REST, gRPC, WebSocket, and event-driven endpoints
    # - Authenticate and authorize incoming requests
    # - Apply routing policies and middleware
    # - Perform protocol translation and request transformation
    # - Enforce rate limiting, quotas, and security policies
    # - Route outbound requests to external systems and providers
    # - Collect gateway metrics and request audit information
    #
    # Result:
    # A fully initialized Gateway Router capable of securely exposing the
    # MonkeyBrain Cognitive Runtime to external clients while providing reliable,
    # scalable, and policy-driven request routing across the platform.

    async def _phase_hybrid_router(self, app: Any) -> None:
        """Initialize HybridRouter for unified query processing."""
        from src.hybrid import HybridRouter
        from src.llm.llm_provider import LLMProviderFactory
        from src.monkey_brain.kernel.pipeline.orchestrator import PipelineOrchestrator
        from src.monkey_brain.kernel.pipeline.compiler import RequestCompiler
        from src.monkey_brain.kernel.pipeline.learning.integration import build_learning_integrated_runtime

        try:
            # create the cognitive pipeline — LearningIntegratedPolicy layers the
            # Step 10 Reward->Belief->World pipeline onto the Learn stage; without
            # it this runtime only ran the default hypothesis-only _learn.
            pipeline = PipelineOrchestrator(
                compiler=RequestCompiler(),
                runtime=build_learning_integrated_runtime(event_bus=self.event_bus),
            )

            # get the llm provider
            llm_provider = LLMProviderFactory.get_provider()

            # Get knowledge base from SittingFace (SomaticCompiler)
            knowledge_base = getattr(app.state, "somatic_compiler", None)

            # initiate the router
            router = HybridRouter(
                pipeline_orchestrator=pipeline,
                knowledge_base=knowledge_base,
                llm_provider=llm_provider,
                session_timeout_minutes=30,
            )
            app.state.hybrid_router = router
            self._health["hybrid_router"] = ComponentHealth(
                name="hybrid_router",
                state=HealthState.HEALTHY,
                message=f"LLM provider: {type(llm_provider).__name__}",
            )
            logger.info("[kernel] Hybrid Router initialized")
        except Exception as exc:
            logger.warning("[kernel] Hybrid Router initialization failed: %s", exc)
            self._health["hybrid_router"] = ComponentHealth(
                name="hybrid_router",
                state=HealthState.DEGRADED,
                message=str(exc),
            )

    def _find_mongo_client(self, app: Any) -> Any:
        mongo_client = getattr(app.state, "mongo_client", None)
        if mongo_client is not None:
            return mongo_client
        persistence = getattr(app.state, "persistence_manager", self.persistence)
        if persistence is not None:
            for adapter in getattr(persistence, "_adapters", {}).values():
                if hasattr(adapter, "_client") and adapter._client is not None:
                    return adapter._client
        return None

    async def _health_check(self, app: Any) -> None:
        """Verify all runtimes report healthy after boot."""
        for name in self.registry.names():
            health = self._health.get(name, ComponentHealth(name=name, state=HealthState.UNKNOWN))
            self.registry.set_health(name, health)
            # Lifecycle is registry metadata; keep it synchronized with the
            # existing boot health result without changing runtime behavior.
            self.registry.set_lifecycle(name, "ready" if health.ok else "degraded")

    def validate_architecture(self) -> dict[str, Any]:
        """Validate Kernel-owned architectural invariants.

        This is intentionally a hard-invariant gate, not a claim that every
        historical compatibility path has already been removed. Legacy debt
        is reported separately by the repository conformance checker.
        """
        self.registry.validate()
        self.agent_registry.validate()
        self.capability_registry.validate()
        checks = {
            "one_runtime_registry": self.runtime_registry is self.registry,
            "one_runtime_selector": self.runtime_selector.registry is self.registry,
            "one_agent_registry": isinstance(self.agent_registry, AgentRegistry),
            "one_capability_registry": isinstance(self.capability_registry, CapabilityRegistry),
            "kernel_owned_app_state": True,
        }
        violations = [name for name, passed in checks.items() if not passed]
        if violations:
            raise RuntimeError(f"architecture invariant violations: {', '.join(violations)}")
        return {"checks": checks, "violations": violations}

    async def execute(self, request: Any, *, runtime_id: str = "cognitive", **kwargs: Any) -> Any:
        """Canonical Kernel dispatch boundary.

        Runtime implementations remain responsible for their own execution;
        Kernel only selects the registered runtime and awaits its public
        execute method when necessary.
        """
        runtime = self.runtime_selector.select(runtime_id)
        execute = getattr(runtime, "execute", None)
        if not callable(execute):
            raise TypeError(f"runtime {runtime_id!r} has no execute method")

        from src.monkey_brain.kernel.security_boundary import ensure_governed

        async def _run() -> Any:
            result = execute(request, **kwargs)
            return await result if inspect.isawaitable(result) else result

        return await ensure_governed(f"kernel.execute.{runtime_id}", runtime_id, _run)

    def _print_boot_summary(self) -> None:
        """Print the boot summary exactly once."""
        duration_ms = (time.time() - self._boot_start) * 1000

        lines = [
            "",
            "═" * 60,
            "  MonkeyBrain Cognitive OS",
            "═" * 60,
            "",
        ]

        def _section(title: str, items: list[tuple[str, str, str]]) -> None:
            lines.append(f"  {title}")
            for name, icon, msg in items:
                suffix = f" ({msg})" if msg else ""
                lines.append(f"    {icon} {name}{suffix}")
            lines.append("")

        # Kernel
        _section("Kernel", [("Ready", "✓", "")])

        # Persistence
        persistence_items = []
        for adapter_name in getattr(self.persistence, "_adapters", {}):
            adapter = self.persistence._adapters.get(adapter_name)
            connected = hasattr(adapter, "_client") and getattr(adapter, "_client", None) is not None
            persistence_items.append((adapter_name, "✓" if connected else "⚠", "" if connected else "not connected"))
        _section("Persistence", persistence_items or [("No adapters", "?", "")])

        # Providers
        provider_health = self._health.get("providers", ComponentHealth(name="providers"))
        _section("Providers", [("Cerebellum", provider_health.icon, provider_health.message)])

        # Agents
        broca_count = 0
        try:
            from broca.registry import get_registry

            broca_count = len(get_registry().list_agents())
        except Exception:
            logger.debug("_print_boot_summary: suppressed exception", exc_info=True)
        _section("Agents", [(f"{broca_count} Registered", "✓", "")])

        # Runtimes
        runtime_items = []
        for info in self.registry.all_info():
            runtime_items.append((info.name.capitalize(), info.health.icon, ""))
        _section("Runtimes", runtime_items)

        # Process Manager
        pm_health = self._health.get("process_manager", ComponentHealth(name="pm"))
        _section("Process Manager", [("Ready", pm_health.icon, pm_health.message)])

        # Observability
        obs_health = self._health.get("observability", ComponentHealth(name="obs"))
        es_status = "connected" if self.lemon and self.lemon._es_client else "in-memory only"
        _section("Observability", [("Lemon", obs_health.icon, es_status)])

        # SittingFace
        sf_health = self._health.get("sittingface", ComponentHealth(name="sf"))
        _section("SittingFace", [("Charts", sf_health.icon, sf_health.message)])

        # Resources
        if self.resource_manager:
            resource_items = []
            for r in self.resource_manager.summary():
                resource_items.append((r["name"], r["icon"], r.get("reason", "")))
            _section("Kernel Resources", resource_items)

        # System State
        all_healthy = all(h.state == HealthState.HEALTHY for h in self._health.values())
        resource_ok = self.resource_manager.all_ready() if self.resource_manager else True
        state = "READY" if all_healthy and resource_ok else "DEGRADED"
        lines.append(f"  System State: {state}")
        lines.append(f"  Boot Time:    {duration_ms:.0f}ms")
        lines.append("")
        lines.append("═" * 60)
        lines.append("")

        summary = "\n".join(lines)
        print(summary)
        if self.lemon:
            self.lemon.info(summary, component="kernel")

    async def shutdown(self, app: Any) -> None:
        """Tear down runtimes in reverse boot order with error isolation.

        Each runtime shutdown is independently try/excepted so a failure
        in one does not prevent the others from being cleaned up.
        """
        errors: list[str] = []

        async def _safe_shutdown(name: str, obj: Any) -> None:
            try:
                await obj.shutdown(app)
            except Exception as exc:
                msg = f"{name} shutdown failed: {exc}"
                errors.append(msg)
                logger.error(msg)
            finally:
                if self.registry.descriptor(name) is not None:
                    self.registry.set_lifecycle(name, "stopped")

        for name in ("cognitive", "planetary", "simulation", "comparator", "codegen"):
            runtime = self.registry.get(name)
            if runtime is not None:
                await _safe_shutdown(name, runtime)

        if self.process_manager is not None:
            await _safe_shutdown("process_manager", self.process_manager)

        if self.resource_manager is not None:
            try:
                await self.resource_manager.shutdown_all()
            except Exception as exc:
                msg = f"resource_manager shutdown failed: {exc}"
                errors.append(msg)
                logger.error(msg)

        try:
            await self.event_bus.publish("kernel.shutdown.completed", {"errors": errors})
        except Exception:
            logger.debug("shutdown: suppressed exception", exc_info=True)

        # Every attribute Kernel.boot() (and each runtime's own boot()) sets
        # on app.state is cleared here — nothing above clears any of them.
        # Left set, a stale (already-shut-down) reference outlives this
        # Kernel: any later ASGI app usage that never re-runs the lifespan
        # (e.g. a second TestClient(app) built without `with`, in-process,
        # after an earlier test module's `with TestClient(app) as c:` block
        # already booted+shutdown once) would see this dead Kernel/runtime
        # as if it were still live — including via RuntimeSelector.select(),
        # which does a raw dict lookup with no lifecycle check, so a
        # "stopped" runtime is still returned as if healthy.
        for attr in (
            "kernel",
            "runtime_registry",
            "agent_registry",
            "capability_registry",
            "runtime_selector",
            "_provider_registry",
            "lemon",
            "_wolverine",
            "_pcp",
            "_policy",
            "_learning",
            "_observer",
            "_execution_graph",
            "_graph_store",
            "_data_routing",
            "_oql",
            "process_manager",
            "runtime_identity",
            "planetary_runtime",
            "hybrid_router",
            "cognitive_runtime",
            "simulation_runtime",
            "comparator_runtime",
            "codegen_runtime",
        ):
            if hasattr(app.state, attr):
                setattr(app.state, attr, None)

        # The other half of the staleness bug the comment above already
        # describes: clearing app.state.* is not enough on its own, because
        # boot() is gated on the CLASS-level _instance/_booted singleton,
        # not on app.state. Leaving those set here means the very next
        # Kernel.boot(app) call — even for a brand-new app object, e.g. a
        # later test module's own `with TestClient(app) as c:` triggering a
        # fresh lifespan — hits boot()'s "already booted" fast path and
        # returns this same shut-down Kernel WITHOUT re-running
        # _boot_sequence or re-attaching any app.state.* attribute at all,
        # leaving the new app permanently half-booted (every route that
        # depends on app.state.kernel/planetary_runtime/etc 503s for the
        # rest of the process). Resetting both here is what makes a
        # subsequent boot() call actually reboot, matching the class
        # docstring's real intent ("boot() executes exactly once [per live
        # Kernel] — subsequent calls [while still booted] return the
        # existing instance") rather than "exactly once per process."
        type(self)._instance = None
        type(self)._booted = False

        if errors:
            logger.warning("Kernel shutdown completed with %d error(s): %s", len(errors), "; ".join(errors))
        else:
            logger.info("Kernel shutdown complete")


def get_kernel(app: Any) -> Kernel:
    return app.state.kernel
