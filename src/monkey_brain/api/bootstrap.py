"""Application bootstrap — one async initializer per subsystem.

Each function is independently testable. Failures in optional subsystems
are logged and return None; failures in required subsystems raise RuntimeError.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos")


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def load_dotenv(env_file: Path) -> None:
    """Load .env file into os.environ without overwriting existing vars."""
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv as _load

        _load(env_file, override=False)
    except ImportError:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw_value = line.partition("=")
            os.environ.setdefault(key.strip(), raw_value.strip().strip("\"'"))


def _env_url_or_warn(name: str, default: str) -> str:
    """Read a connection-URL env var, warning loudly when it's unset.

    An env var that's simply forgotten (vs. deliberately pointed at localhost)
    should not silently target localhost with no signal — that's a classic
    prod-misdeploy trap. Explicitly setting the var to the same value as the
    default is treated as intentional and doesn't warn.
    """
    value = os.getenv(name)
    if value is None:
        logger.warning(
            "%s is unset — defaulting to %r. If this is a real deployment, "
            "set %s explicitly (even to this value) to confirm it's intentional.",
            name,
            default,
            name,
        )
        return default
    return value


# ---------------------------------------------------------------------------
# Required subsystems
# ---------------------------------------------------------------------------


async def init_lemon(app: Any) -> Any:
    from src.introspection.lemon import Lemon, set_lemon

    lemon = Lemon()
    set_lemon(lemon)

    es_url = _env_url_or_warn("ELASTICSEARCH_URL", "http://localhost:9200")
    try:
        await lemon.connect_elasticsearch()
        lemon.info("Lemon connected to Elasticsearch", component="bootstrap")
        # Redacted: an ES URL conventionally embeds credentials (https://user:pass@host:9200).
        from src.monkey_brain.persistence.client_options import redact_url

        logger.info("Lemon connected to Elasticsearch: %s", redact_url(es_url))
    except Exception as exc:
        logger.warning(
            "Lemon Elasticsearch connection failed (metrics will be in-memory only): %s",
            exc,
        )

    lemon.info("Lemon initialized", component="bootstrap")
    logger.info("Lemon initialized")
    return lemon


async def init_persistence(app: Any, lemon: Any) -> Any:
    from src.monkey_brain.persistence.manager import PersistenceManager
    from src.monkey_brain.persistence.mongodb_adapter import MongoDBAdapter
    from src.monkey_brain.persistence.redis_adapter import RedisAdapter
    from src.monkey_brain.persistence.mem0_adapter import Mem0Adapter

    pm = PersistenceManager()
    pm.set_lemon(lemon)
    pm.register_adapter(
        MongoDBAdapter(
            url=_env_url_or_warn("MONGODB_URL", "mongodb://localhost:27017"),
            database=os.getenv("DB_NAME", "agentos"),
        )
    )
    pm.register_adapter(RedisAdapter(url=_env_url_or_warn("REDIS_URL", "redis://localhost:6379")))
    pm.register_adapter(Mem0Adapter())

    try:
        await pm.connect_all()
        logger.info("Persistence Manager initialized")
    except Exception as exc:
        logger.error("Persistence Manager connect failed: %s", exc)
        raise RuntimeError(
            f"Startup aborted — primary persistence unavailable: {exc}. "
            "Set MONGODB_URL and REDIS_URL or fix the connection before starting."
        ) from exc

    lemon.info("Persistence Manager initialized", component="bootstrap")

    try:
        from src.monkey_brain.memory.persistence_integration import register_manager

        register_manager(pm, lemon)
    except Exception as exc:
        logger.warning("persistence_integration.register_manager failed: %s", exc)

    return pm


async def init_runtime(app: Any, pm: Any, lemon: Any) -> Any:
    from src.monkey_brain.runtime.runtime import Runtime

    runtime = Runtime()
    runtime.set_persistence_manager(pm)
    runtime.set_lemon(lemon)

    # Capability registration for `runtime` is done downstream by
    # init_providers() (cerebellum providers) and init_broca() (ETASS
    # agents) — both called right after this by CognitiveRuntime.boot().

    lemon.info("Runtime initialized", component="bootstrap")
    logger.info("Runtime initialized")
    return runtime


# ---------------------------------------------------------------------------
# Optional subsystems — failures are logged, not raised
# ---------------------------------------------------------------------------


async def init_providers(app: Any, runtime: Any) -> None:
    try:
        from cerebellum.providers import load_all_providers

        names = load_all_providers(runtime)
        logger.info("Providers loaded: %s", names)
    except Exception as exc:
        logger.warning("Provider bootstrap skipped: %s", exc)


async def init_broca(app: Any, runtime: Any) -> None:
    # Live Deployment Validation finding: this module's own section header
    # ("Optional subsystems — failures are logged, not raised") was
    # violated by this function alone — every sibling init_* function
    # (init_providers above, init_pcp below) wraps its own top-level
    # import in try/except, but this one didn't. Confirmed live: a real
    # container missing the `broca` package (packages/broca/ was never
    # copied into docker/services/agentos/Dockerfile) crashed the entire
    # application at startup with an uncaught ModuleNotFoundError instead
    # of degrading, taking down every OTHER subsystem this boot sequence
    # still needed to initialize.
    try:
        from broca.registry import get_registry
    except Exception as exc:
        logger.warning("Broca unavailable, skipping (%s)", exc)
        return
    registry = get_registry()

    if registry._bootstrapped:
        logger.info("Broca already registered (%d agents) — skipping", len(registry._agents))
        return

    try:
        if hasattr(registry, "set_runtime"):
            registry.set_runtime(runtime)
        else:
            registry._runtime = runtime
        registry._bootstrap()
        logger.info("Broca agents auto-registered: %s", registry.agent_types())
    except Exception as exc:
        logger.warning("Broca agent auto-registration skipped: %s", exc)

    try:
        from broca.agents.auth_policy import AuthPolicyAgent
        from broca.agents.ddd.policy import PolicyAgent

        for cls in (AuthPolicyAgent, PolicyAgent):
            if not registry._agents.get(cls.agent_type):
                registry.register(cls(runtime=runtime))
        logger.info("Auth + Policy agents registered")
    except Exception as exc:
        logger.warning("Auth/policy agent registration skipped: %s", exc)


async def init_pcp(app: Any, pm: Any) -> Any:
    try:
        from services.common.policy_control_plane import get_pcp

        # Find MongoDB adapter and get its database connection
        mongo_adapter = None
        for adapter in pm._adapters.values():
            if hasattr(adapter, "_db") and adapter._db is not None:
                mongo_adapter = adapter
                break

        pcp_db = mongo_adapter._db if mongo_adapter else None
        pcp = await get_pcp(db=pcp_db)
        # pcp_db is a Motor AsyncIOMotorDatabase -- like pymongo, it raises
        # NotImplementedError on bool() rather than supporting truthiness, by
        # design (a Database with zero collections must not read as falsy).
        # "if pcp_db" here previously raised on this line, one statement
        # AFTER get_pcp() had already started PCP successfully -- the
        # surrounding try/except then discarded that already-initialized PCP
        # and returned None, reporting "Policy Control Plane skipped" for a
        # PCP that had, in fact, started fine.
        logger.info(
            "Policy Control Plane started (db=%s)",
            "connected" if pcp_db is not None else "none",
        )
        return pcp
    except Exception as exc:
        logger.warning("Policy Control Plane skipped: %s", exc)
        return None


async def init_runtime_identity(runtime: Any) -> None:
    try:
        spiffe_id = os.getenv("SPIFFE_ID", "spiffe://monkeybrain/runtime/agentos")
        runtime.set_runtime_identity(
            {
                "spiffe_id": spiffe_id,
                "client_id": "runtime",
                "agent_type": "runtime",
                "scopes": ["execute:workload"],
                "role_ids": [],
            }
        )
        logger.info("Runtime identity set: %s", spiffe_id)
    except Exception as exc:
        logger.warning("Runtime identity skipped: %s", exc)


async def init_nanda(app: Any) -> None:
    _NANDA_DOMAINS = ("software_engineering", "manufacturing", "default")
    try:
        from cerebellum.capabilities.agent.nanda import NANDACapability

        nanda = NANDACapability()
        if not nanda._available:
            logger.info("NANDA not configured — skipping startup discovery")
            return

        from broca.registry import get_registry
        from broca.agents.nanda import NANDAProxyAgent

        reg = get_registry()
        discovered = 0
        for domain in _NANDA_DOMAINS:
            res = await nanda.execute({"operation": "discover", "domain": domain})
            for card in res.get("agents", []):
                proxy = NANDAProxyAgent(card)
                if not reg._agents.get(proxy.agent_type):
                    reg.register(proxy)
                    discovered += 1
        logger.info("NANDA startup discovery: %d remote agents registered", discovered)
    except Exception as exc:
        logger.warning("NANDA startup discovery skipped: %s", exc)

    """
    Policy Lifecycle
    1. Kernel Boot
    └── _phase_policy()
        └── init_policy()
            └── BellmanPolicy(exploration_rate, learning_rate, discount_factor)
                ├── TransitionTable()
                ├── RewardModel()
                ├── Learner(lr, discount)
                ├── PolicyStore(lr, discount)
                └── LLMExplorer()

    2. Runtime
    ├── Policy.select(pipelines, state) → select pipeline
    ├── Policy.update(transition) → learn from transition
    └── Policy.value(state, action) → estimate Q-value
    
    3. Persistence
    ├── policy.set_persistence_manager(pm) → save/load Q-values
    └── policy.set_lemon(lemon) → observability
    """


async def init_policy(app: Any, pm: Any, lemon: Any) -> Any:
    from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy

    policy = BellmanPolicy(
        exploration_rate=float(os.getenv("POLICY_EXPLORATION_RATE", "0.1")),
        learning_rate=float(os.getenv("POLICY_LEARNING_RATE", "0.1")),
        discount_factor=float(os.getenv("POLICY_DISCOUNT_FACTOR", "0.95")),
    )
    policy.set_persistence_manager(pm)
    policy.set_lemon(lemon)
    lemon.info("Policy initialized (Bellman)", component="bootstrap")
    logger.info("Policy initialized")
    return policy


# ---------------------------------------------------------------------------
# Initiate the observer for lemon
# ---------------------------------------------------------------------------


async def init_observer(app: Any, lemon: Any) -> Any:
    from src.monkey_brain.kernel.learn.observer.observer import Observer

    observer = Observer()
    lemon.info("Observer initialized", component="bootstrap")
    logger.info("Observer initialized")
    return observer


async def init_learning(app: Any, lemon: Any) -> Any:
    from src.monkey_brain.kernel.learn.learning import Learning

    learning = Learning(learning_rate=float(os.getenv("LEARNING_RATE", "0.1")))
    lemon.info("Learning initialized", component="bootstrap")
    logger.info("Learning initialized")
    return learning


async def init_sittingface(app: Any, runtime: Any, lemon: Any) -> None:
    try:
        from src.sittingface.somatic_compiler import SomaticCompiler

        # this initiates the EATAS charts and promptsß
        compiler = SomaticCompiler()

        try:
            # SomaticCompiler.__init__ already auto-detects charts_dir by
            # walking up from cwd looking for a real "somatic/" directory
            # (repo-root somatic/charts/*, 25+ real chart dirs) and load_all()
            # itself already handles a missing directory gracefully (returns
            # an empty list, never raises). The previous check here looked
            # for a nonexistent repo_root/"sittingface" directory (no such
            # path exists — only src/sittingface, src/sittingface_charts, and
            # packages/sittingface do, none of which hold chart data) and so
            # always raised, meaning load_all() never actually ran at boot
            # even though the real chart directory was there the whole time.
            compiler.load_all()
            logger.info("SittingFace charts loaded")
        except Exception as exc:
            logger.warning("SittingFace charts load failed: %s", exc)

        try:
            prompts = compiler.compile_prompts()
        except Exception as exc:
            logger.warning("SittingFace prompts compilation failed: %s", exc)
            prompts = []

        try:
            cap_names = compiler.register_capabilities(runtime)
        except Exception as exc:
            logger.warning("SittingFace capability registration failed: %s", exc)
            cap_names = []

        try:
            from broca.registry import register_etass_agents

            register_etass_agents(runtime=runtime)
        except Exception as exc:
            logger.warning("Broca ETASS agent registration skipped: %s", exc)

        # Data-backed domain agents. Registered AFTER the ETASS/DDD agents so that where the
        # two overlap, the one that actually reads the database wins the registry slot — the
        # stubs return success unconditionally and query nothing.
        try:
            from domains.manufacturing.agents import register_manufacturing_agents

            register_manufacturing_agents(runtime=runtime)
        except Exception as exc:
            logger.warning("Manufacturing domain agent registration skipped: %s", exc)

        app.state.somatic_compiler = compiler
        try:
            from src.monkey_brain.kernel.plan.intents.intent_registry import (
                set_somatic_compiler,
            )

            set_somatic_compiler(compiler)
        except Exception as exc:
            logger.warning("Intent registry soma bridge skipped: %s", exc)
        summary = compiler.summary()
        lemon.info(
            "SittingFace bootstrap: %s charts, %s capabilities, %s prompts"
            % (summary["total_charts"], len(cap_names), len(prompts)),
            component="sittingface",
        )
        logger.info(
            "SittingFace: %d charts, %d capabilities, %d prompts",
            summary["total_charts"],
            len(cap_names),
            len(prompts),
        )
    except Exception as exc:
        logger.warning("SittingFace bootstrap skipped: %s", exc)
        app.state.somatic_compiler = None


def _graph_node(node_id: str, node_type: str, label: str, props: dict | None = None):
    from src.monkey_brain.kernel.execute.graph import GraphNode

    return GraphNode(id=node_id, type=node_type, label=label, props=props or {})


def _graph_edge(src: str, dst: str, rel: str):
    from src.monkey_brain.kernel.execute.graph import GraphEdge

    return GraphEdge(src=src, dst=dst, rel=rel)


def _load_workloads(graph: Any) -> None:
    """Load workload templates and add Workload + Step nodes to the ExecutionGraph."""
    from src.monkey_brain.kernel.fix.self_healing.workload import (
        create_self_healing_workload,
    )

    workloads = [
        create_self_healing_workload(),
    ]

    for wl in workloads:
        wid = f"workload:{wl.workload_id}"
        graph.add_node(
            _graph_node(
                wid,
                "workload",
                wl.workload_id,
                {
                    "description": wl.metadata.get("description", ""),
                    "steps": len(wl.steps),
                },
            )
        )

        for step in wl.steps:
            sid = f"step:{step.step_id}"
            graph.add_node(
                _graph_node(
                    sid,
                    "step",
                    step.step_id,
                    {
                        "capability": step.capability_name,
                        "inputs": step.inputs,
                        "outputs": step.outputs,
                    },
                )
            )
            graph.add_edge(_graph_edge(wid, sid, "contains"))

            # Wire step dependencies (step depends_on dependency)
            for dep in step.dependencies:
                dep_sid = f"step:{dep}"
                graph.add_edge(_graph_edge(sid, dep_sid, "depends_on"))

            # Wire step → capability
            cid = f"capability:{step.capability_name}"
            if graph.get_node(cid) is not None:
                graph.add_edge(_graph_edge(sid, cid, "uses"))


async def init_execution_graph(app: Any, runtime: Any, lemon: Any = None) -> Any:
    """Build the canonical ExecutionGraph once during bootstrap.

    Discovery order:
        Load Providers  →  Load Agents  →  Load Capabilities  →  Build Graph  →  Index

    The graph is the single source of truth for runtime metadata.
    Execution queries the graph. Execution never rebuilds the graph.
    """
    from src.monkey_brain.kernel.execute.graph import ExecutionGraph

    graph = ExecutionGraph()

    # Execution-boundary hardening fix: this previously read
    # `runtime.capability_bus`, an attribute `Runtime` (runtime/runtime.py)
    # never sets — always None, so this whole block silently no-op'd every
    # boot (confirmed live: "No CapabilityBus — building ExecutionGraph
    # without capability/provider nodes" logged unconditionally).
    # `Runtime` itself is the real capability registry (`register()`/
    # `list_capabilities()`, runtime.py:89-104) — but its shape is much
    # thinner than what the block below originally assumed: it exposes
    # only capability NAMES (`list_capabilities() -> list[str]`), not rich
    # descriptor objects with `.modality`/`.confidence`/`.preconditions`/
    # `.produces`/`.effects`, and it has no `.agent_bus`/
    # `.contribute_to_graph`/`.set_graph` methods at all (confirmed by
    # reading runtime.py in full — no such API exists anywhere on it).
    # Rather than fabricate metadata that was never real or crash calling
    # methods that don't exist, this builds one real Capability node per
    # actually-registered capability name -- everything else stays an
    # honest no-op instead of a fictional value.
    bus = runtime if runtime is not None and hasattr(runtime, "list_capabilities") else None
    if bus is None:
        logger.info("No capability registry on runtime — building ExecutionGraph without capability nodes")

    if bus is not None:
        # 1. The real capability registry contributes Capability nodes.
        # No precondition/effect/modality/confidence data exists on this
        # registry's entries (only names) — not fabricated here.
        for name in bus.list_capabilities():
            cid = f"capability:{name}"
            graph.add_node(_graph_node(cid, "capability", name))

        # 2/3. Agent/Provider node contribution: no `agent_bus`/
        # `contribute_to_graph` exists on Runtime -- guarded, not invented.
        if hasattr(bus, "agent_bus") and hasattr(bus.agent_bus, "contribute_to_graph"):
            bus.agent_bus.contribute_to_graph(graph)
        if hasattr(bus, "contribute_to_graph"):
            bus.contribute_to_graph(graph)

    # 4. Load workload templates and add Workload + Step nodes
    _load_workloads(graph)

    # 5. Also populate the legacy cerebellum CapabilityGraph
    if bus is not None:
        try:
            from cerebellum.graph import get_global_graph
            from cerebellum.descriptor import CapabilityDescriptor

            cgraph = get_global_graph()
            for name in bus.list_capabilities():
                cgraph.add(CapabilityDescriptor(name=name))
            logger.info(
                "Cerebellum CapabilityGraph populated: %d nodes",
                len(cgraph._descriptors),
            )
        except Exception as exc:
            logger.debug("Cerebellum CapabilityGraph skip: %s", exc)

    # 5. Wire the graph to the bus, if it exposes a way to receive one --
    # Runtime does not (confirmed by reading runtime.py in full); guarded
    # rather than assumed, same principle as the agent_bus/contribute_to_graph
    # guards above.
    if bus is not None and hasattr(bus, "set_graph"):
        bus.set_graph(graph)

    # 6. Track the graph via Lemon observability
    if lemon is not None:
        lemon.observe_execution_graph(graph)

    logger.info("ExecutionGraph built: %d nodes, %d edges", graph.node_count, graph.edge_count)
    return graph


async def init_graph_store(app: Any, runtime: Any, graph: Any = None) -> Any:
    """Initialize the graph store (tensor + KV, replacing Neo4j).

    The graph is built once by init_execution_graph and passed in here to
    persist it — this function never reaches into app.state for it.
    """
    from src.monkey_brain.persistence.composite_graph_store import CompositeGraphStore
    import src.monkey_brain.persistence.graph_store as _graph_store_module

    store = CompositeGraphStore()
    _graph_store_module._booted_instance = store

    logger.info("CompositeGraphStore initialized (tensor + KV)")
    return store


async def wire_subscribers(runtime: Any, policy: Any, observer: Any, learning: Any, lemon: Any) -> None:
    try:
        for subscriber in (policy, observer, learning, lemon):
            if subscriber is not None:
                runtime.subscribe(subscriber)
        logger.info("ExecutionOutcome subscribers wired")
    except Exception as exc:
        logger.warning("Subscriber wiring skipped: %s", exc)


async def init_domain_registry(app: Any) -> Any:
    try:
        from domains.package import DomainRegistry

        registry = DomainRegistry.instance()
        domains_dir = Path(__file__).parents[3] / "domains"
        registry.load_from_directory(domains_dir)
        logger.info("Domain packages loaded: %s", registry.list_all())
        return registry
    except Exception as exc:
        logger.warning("Domain registry skipped: %s", exc)
        return None


async def run_health_checks(app: Any, pm: Any, lemon: Any, runtime: Any = None, policy: Any = None) -> None:
    try:
        results = await pm.health()
        for store_name, store_health in results.items():
            status = store_health.get("status", "error") if isinstance(store_health, dict) else "error"
            lemon.health_check(store_name, status)
        lemon.info("Health checks complete", component="bootstrap")
    except Exception as exc:
        logger.warning("Health checks skipped: %s", exc)

    # Report actual initialized state — never hard-code "healthy".
    if lemon is not None:
        lemon.health_check("runtime", "healthy" if runtime is not None else "degraded")
        lemon.health_check("policy", "healthy" if policy is not None else "degraded")


async def shutdown(
    app: Any,
    pm: Any,
    lemon: Any,
    pcp: Any = None,
    graph_store: Any = None,
    semantic_graph: Any = None,
) -> None:
    try:
        if pcp:
            await pcp.stop()
    except Exception as exc:
        logger.warning("PCP stop failed: %s", exc)

    try:
        if graph_store:
            await graph_store.close()
    except Exception as exc:
        logger.warning("GraphStore close failed: %s", exc)

    try:
        if semantic_graph:
            await semantic_graph.close()
    except Exception as exc:
        logger.warning("SemanticGraph close failed: %s", exc)

    await pm.disconnect_all()
    lemon.info("Persistence stores disconnected", component="shutdown")
    logger.info("Persistence stores disconnected")


async def init_data_routing(app: Any, lemon: Any) -> Any:
    """Initialize data routing middleware with all available database adapters."""
    from src.monkey_brain.routing.middleware import DataRoutingMiddleware
    from src.monkey_brain.routing.adapters import (
        PostgreSQLAdapter,
        MongoDBAdapter,
        Neo4jAdapter,
        RedisAdapter,
        InfluxDBAdapter,
        ElasticsearchAdapter,
        SQLiteAdapter,
        MySQLAdapter,
    )

    middleware = DataRoutingMiddleware()

    adapters_to_register = [
        PostgreSQLAdapter(os.getenv("POSTGRES_URL", "")),
        MongoDBAdapter(os.getenv("MONGODB_URL", "")),
        Neo4jAdapter(os.getenv("NEO4J_URI", "")),
        RedisAdapter(os.getenv("REDIS_URL", "")),
        InfluxDBAdapter(os.getenv("INFLUXDB_URL", "")),
        ElasticsearchAdapter(os.getenv("ELASTICSEARCH_URL", os.getenv("AUDIT_ELASTICSEARCH_URL", ""))),
        SQLiteAdapter(os.getenv("SQLITE_PATH", "")),
        MySQLAdapter(os.getenv("MYSQL_URL", "")),
    ]

    for adapter in adapters_to_register:
        try:
            await middleware.register_adapter(adapter)
        except Exception as exc:
            logger.debug("Data routing adapter %s failed: %s", adapter.adapter_type, exc)

    app.state.data_routing = middleware
    lemon.info("Data routing initialized", component="bootstrap")
    logger.info("Data routing initialized")
    return middleware


async def init_oql(app: Any, lemon: Any) -> Any:
    """Initialize OQL engine with data routing middleware."""
    from src.monkey_brain.oql.engine import OQLEngine

    routing = getattr(app.state, "data_routing", None)
    engine = OQLEngine(routing_middleware=routing)

    # Register known entity types
    # TODO: need to figure how to do this better and generize this so that any data can be found
    # we should have something like add table infact this whole data abstraction idea needs to be expanded
    known_entities = {
        "Customer",
        "Order",
        "Product",
        "Inventory",
        "Invoice",
        "Payment",
        "Shipment",
        "Employee",
        "Account",
        "Transaction",
        "Ticket",
        "Case",
        "Permit",
        "Claim",
        "Policy",
        "Quote",
        "Proposal",
        "Campaign",
        "Lead",
        "Opportunity",
        "Robot",
        "Fleet",
        "Mission",
        "Grid",
        "Asset",
        "Patient",
        "Appointment",
        "Prescription",
        "Lab",
        "Flight",
        "Crew",
        "Aircraft",
        "Vehicle",
        "Site",
        "Equipment",
        "WorkOrder",
        "Batch",
        "Contract",
        "Clause",
        "Loan",
        "Risk",
    }
    for entity in known_entities:
        engine.register_entity(entity)

    app.state.oql = engine
    lemon.info("OQL engine initialized", component="bootstrap")
    logger.info("OQL engine initialized")
    return engine
