"""MonkeyBrain Runtime — FastAPI application.

Kernel
    |
    +-- Runtime Registry   (name -> booted runtime instance)
    +-- Runtime Lifecycle  (Kernel.boot() / Kernel.shutdown())
    +-- Event Bus          (publish/subscribe across runtimes)

The app boots the Kernel; the Kernel boots three independent runtimes
(Compile | Run | Replay):

    Cognitive Runtime  | /plan | /execute  | /replay  (app.state.cognitive_runtime)
    Simulation Runtime | /plan | /simulate | /replay  (app.state.simulation_runtime)
    Comparator Runtime | /plan | /compare  | /replay  (app.state.comparator_runtime)

Lemon (observability) and PersistenceManager (memory management) are the
Kernel's own dependencies — constructed once and injected into every
runtime's boot(), rather than each runtime building its own. CognitiveRuntime
additionally owns Wolverine, Providers, Broca, PCP, Policy, Observer,
Learning, ExecutionGraph, GraphStore, DomainRegistry — subsystems no other
runtime needs. SittingFace stays an external knowledge-base repo queried via
explore_knowledge_base() rather than owned by any runtime. SimulationRuntime/
ComparatorRuntime are lightweight and hold no reference to CognitiveRuntime or
each other — they share only the stateless intent compiler
(plan.goals.compile.compile_intent) and the process-local run store
(plan.goals.run_store), plus whatever the Kernel injects.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# packages/cerebellum is not installed in all environments; insert it so
# `import cerebellum` resolves to the full implementation (graph, bus, descriptor, …).
# IMPROVEMENT: pip install -e packages/cerebellum and remove this sys.path mutation.
# NOTE: parents[3] is the repo root from src/monkey_brain/api/main.py (api ->
# monkey_brain -> src -> repo root) — this was parents[4] (one level ABOVE the
# repo root) for an unknown period, silently making both .exists() checks
# below False and skipping both sys.path insertions without ever raising.
_pkg_cerebellum = Path(__file__).parents[3] / "packages" / "cerebellum"
if _pkg_cerebellum.exists() and str(_pkg_cerebellum) not in sys.path:
    sys.path.insert(0, str(_pkg_cerebellum))

_pkg_services = Path(__file__).parents[3] / "domains" / "manufacturing" / "knowledge"
if _pkg_services.exists() and str(_pkg_services) not in sys.path:
    sys.path.insert(0, str(_pkg_services))

# packages/broca is not installed in all environments; insert it so
# `import broca` resolves (used by init_broca/init_pcp/CodeGenRuntime for
# ETASS agent registration) — same pattern as _pkg_cerebellum above.
_pkg_broca = Path(__file__).parents[3] / "packages" / "broca"
if _pkg_broca.exists() and str(_pkg_broca) not in sys.path:
    sys.path.insert(0, str(_pkg_broca))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from services.common.logging import configure_service_logging

logger = configure_service_logging("agentos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot the Kernel; the Kernel boots the runtimes; shut down on exit.

    We boot the Kernel — everything else gets initialized as a consequence:

        Kernel.boot(app)
            |
            +-- Lemon (observability) + PersistenceManager (memory management)
            |   constructed once, injected into every runtime below
            |
            +-- CognitiveRuntime.boot(app, lemon, persistence, event_bus)
            |     owns Wolverine, Policy, Observer, Learning, ExecutionGraph,
            |     GraphStore, DomainRegistry (SittingFace stays external —
            |     explore_knowledge_base(), not an owned subsystem)
            +-- SimulationRuntime.boot(app, lemon, persistence, event_bus)
            +-- ComparatorRuntime.boot(app, lemon, persistence, event_bus)

    The Kernel's Runtime Registry and Event Bus let the three runtimes stay
    fully independent of each other (no runtime imports another) while still
    sharing observability/memory-management dependencies and being able to
    publish/subscribe to each other's lifecycle events.

    Required subsystems (Persistence, Runtime, Broca) raise RuntimeError on
    failure. Optional subsystems (Providers, PCP, Runtime Identity,
    SittingFace, Graph Store, Data Routing, OQL Engine, CodeGen Runtime,
    Hybrid Router — see kernel.py's `_OPTIONAL_PHASES`) log and continue.
    """
    from src.monkey_brain.kernel.kernel import Kernel

    # Kernel handles .env loading in its boot sequence.
    kernel = await Kernel.boot(app)

    from src.monkey_brain.api.routes.memberships import init_memberships_store

    init_memberships_store()

    # Gate 3 (ADR-010) — after bootstrap: validate the freshly-booted world
    # once before serving traffic. Logs loudly rather than blocking startup
    # (a non-critical seed issue must not take the whole app down, matching
    # every other optional-phase failure mode this function already uses) —
    # see docs/adr/010-world-validation-engine.md for why this is a log-only
    # gate here but a hard gate before /prompt and /actors/{id}/execute.
    planetary_runtime = getattr(app.state, "planetary_runtime", None)
    if planetary_runtime is not None:
        try:
            from src.monkey_brain.kernel.validation.world_validator import (
                validate_world,
            )

            report = validate_world(planetary_runtime)
            if report["ok"]:
                logger.info("world validation (post-bootstrap): OK, 0 violations")
            else:
                logger.warning(
                    "world validation (post-bootstrap): %d violations across categories %s",
                    report["violation_count"],
                    report["categories"],
                )
        except Exception as exc:
            logger.warning("world validation (post-bootstrap) failed to run: %s", exc)

    yield

    await kernel.shutdown(app)


app = FastAPI(
    title="MonkeyBrain Runtime",
    description="Cognitive Operating System Runtime",
    version="2.0.0",
    lifespan=lifespan,
)

try:
    from services.common.trace_middleware import TraceMiddleware

    app.add_middleware(TraceMiddleware)
except ImportError:
    logger.warning("TraceMiddleware not available — distributed tracing disabled")

try:
    from services.common.mtls import MTLSMiddleware

    app.add_middleware(MTLSMiddleware)
except ImportError:
    # Fallback: extract mTLS client cert info from request state
    # when the real MTLSMiddleware isn't available.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    class FallbackMTLSMiddleware(BaseHTTPMiddleware):
        """Extracts client certificate info from the request's TLS state.

        In production, the real MTLSMiddleware (or a reverse proxy like
        nginx/envoy) terminates TLS and passes cert info via headers.
        This fallback reads those headers so downstream code can access
        client cert details uniformly.
        """

        async def dispatch(self, request: Request, call_next):
            # Read client cert info from reverse-proxy headers
            cert_subject = request.headers.get("x-client-cert-subject", "")
            cert_fingerprint = request.headers.get("x-client-cert-fingerprint", "")
            mtls_verified = bool(cert_subject)

            # Store on request.state for downstream access
            request.state.mtls_cert = (
                {
                    "subject": cert_subject,
                    "fingerprint": cert_fingerprint,
                }
                if mtls_verified
                else None
            )
            request.state.mtls_verified = mtls_verified

            return await call_next(request)

    app.add_middleware(FallbackMTLSMiddleware)
    logger.info("Fallback mTLS middleware active — reads cert info from proxy headers")

_DEFAULT_CORS_ORIGINS = "http://localhost:3000"
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiting middleware ────────────────────────────────────────────────
# RateLimiter (kernel/security.py) is a first-party, dependency-free module — it
# should always import cleanly in a correctly-packaged deployment. Only ImportError
# (a genuine packaging problem) degrades gracefully; a bad RATE_LIMIT_RPS/BURST env
# value or any other construction error fails boot loudly instead of silently
# disabling rate limiting (fail closed, not fail open, for a security control).
try:
    from src.monkey_brain.kernel.security import RateLimiter
except ImportError as exc:
    logger.error("Rate limiting not available — RateLimiter failed to import: %s", exc)
else:
    _rps_raw = os.getenv("RATE_LIMIT_RPS", "100")
    _burst_raw = os.getenv("RATE_LIMIT_BURST", "200")
    try:
        _rps = float(_rps_raw)
        _burst = float(_burst_raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid rate-limit configuration: RATE_LIMIT_RPS={_rps_raw!r}, "
            f"RATE_LIMIT_BURST={_burst_raw!r} must both be numbers. Boot aborted "
            f"(fail closed) rather than silently disabling rate limiting."
        ) from exc
    _rate_limiter = RateLimiter(rate=_rps, burst=_burst)

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    class RateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            client_ip = request.client.host if request.client else "unknown"
            if not _rate_limiter.allow(client_ip):
                return JSONResponse(status_code=429, content={"error": "rate_limited"})
            return await call_next(request)

    app.add_middleware(RateLimitMiddleware)
    logger.info(
        "Rate limiting enabled: %s rps, %s burst",
        os.getenv("RATE_LIMIT_RPS", "100"),
        os.getenv("RATE_LIMIT_BURST", "200"),
    )

from src.monkey_brain.api.gateway_boundary import ApiGatewayBoundaryMiddleware

app.add_middleware(ApiGatewayBoundaryMiddleware)

from src.monkey_brain.api.routes.predict import router as simulate_router
from src.monkey_brain.api.routes.prompt import router as prompt_router
from src.monkey_brain.api.routes.plan import router as plan_router
from src.monkey_brain.api.routes.execute import router as execute_router
from src.monkey_brain.api.routes.query import router as query_router
from src.monkey_brain.api.routes.agents import router as agents_router
from src.monkey_brain.api.routes.capabilities import router as capabilities_router
from src.monkey_brain.api.routes.codegen import router as codegen_router
from src.monkey_brain.api.routes.keys import router as keys_router
from src.monkey_brain.api.routes.policy import router as policy_router
from src.monkey_brain.api.routes.knowledge import router as knowledge_router
from src.monkey_brain.api.routes.process import router as process_router
from src.monkey_brain.api.routes.fleet import router as fleet_router
from src.monkey_brain.api.routes.metrics import router as metrics_router
from src.monkey_brain.api.routes.observability import router as observability_router
from src.monkey_brain.api.routes.sittingface import router as sittingface_router
from src.monkey_brain.api.routes.qa import router as qa_router
from src.monkey_brain.api.routes.state import router as state_router
from src.monkey_brain.api.routes.metadata import router as metadata_router
from src.monkey_brain.api.routes.workloads import router as workloads_router
from src.monkey_brain.api.routes.sdlc import router as sdlc_router
from src.monkey_brain.dashboard.routes import router as dashboard_router
from src.monkey_brain.routing.routes import router as data_routing_router
from src.monkey_brain.oql.routes import router as oql_router
from src.monkey_brain.api.routes.planet import router as planet_router
from src.monkey_brain.api.routes.societies import router as societies_router
from src.monkey_brain.api.routes.actors import router as actors_router
from src.monkey_brain.api.routes.memberships import router as memberships_router
from src.monkey_brain.api.routes.runtime_gateway import router as runtime_gw_router
from src.monkey_brain.api.routes.simulation_gateway import (
    router as simulation_gw_router,
)
from src.monkey_brain.api.routes.comparator_gateway import (
    router as comparator_gw_router,
)
from src.monkey_brain.api.routes.learning_gateway import router as learning_gw_router
from src.monkey_brain.api.routes.world import router as world_router
from src.monkey_brain.api.routes.discovery import router as discovery_router
from src.monkey_brain.api.routes.security import router as security_router
from src.monkey_brain.api.routes.admin import router as admin_router
from src.monkey_brain.api.routes.knowledge_graph import router as knowledge_graph_router
from src.monkey_brain.api.routes.actor_profile import router as actor_profile_router
from src.monkey_brain.api.routes.commerce import router as commerce_router
from src.monkey_brain.api.routes.orders import router as orders_router
from src.monkey_brain.api.routes.fulfillment import router as fulfillment_router
from src.monkey_brain.api.routes.events import router as events_router
from src.monkey_brain.api.routes.presence import router as presence_router
from src.monkey_brain.api.routes.verify import router as verify_router
from src.monkey_brain.api.routes.ws import router as ws_router
from src.monkey_brain.api.routes.approval import router as approval_router
from src.monkey_brain.api.routes.livekit import router as livekit_router
from src.monkey_brain.api.routes.negotiation import router as negotiation_router
from src.monkey_brain.api.routes.payments import router as payments_router
from src.monkey_brain.api.routes.edge import router as edge_router

app.include_router(simulate_router, prefix="/api/v1/agentos", tags=["Simulate"])
app.include_router(prompt_router, prefix="/api/v1/agentos", tags=["Prompt"])
app.include_router(plan_router, prefix="/api/v1/agentos", tags=["Plan"])
app.include_router(execute_router, prefix="/api/v1/agentos", tags=["Execute"])
app.include_router(query_router, prefix="/api/v1/agentos", tags=["Query"])
app.include_router(agents_router, prefix="/api/v1/agentos", tags=["Agents"])
app.include_router(capabilities_router, prefix="/api/v1/agentos", tags=["Capabilities"])
app.include_router(codegen_router, prefix="/api/v1/codegen", tags=["Codegen"])
app.include_router(keys_router, prefix="/api/v1/agentos", tags=["Keys"])
app.include_router(knowledge_router, prefix="/api/v1/agentos", tags=["Knowledge"])
app.include_router(process_router, prefix="/api/v1/agentos", tags=["Process"])
app.include_router(fleet_router, prefix="/api/v1/agentos", tags=["Fleet"])
app.include_router(metrics_router, tags=["Metrics"])
app.include_router(policy_router, prefix="/api/v1/agentos", tags=["Policy"])
app.include_router(observability_router, prefix="/api/v1/agentos", tags=["Observability"])
app.include_router(sittingface_router, prefix="/api/v1/agentos", tags=["SittingFace"])
app.include_router(qa_router, prefix="/api/v1/agentos", tags=["Q&A"])
app.include_router(state_router, prefix="/api/v1/agentos", tags=["State"])
app.include_router(metadata_router, prefix="/api/v1/agentos", tags=["Metadata"])
app.include_router(workloads_router, prefix="/api/v1/agentos", tags=["Workloads"])
app.include_router(sdlc_router, prefix="/api/v1/agentos", tags=["SDLC"])
app.include_router(dashboard_router, prefix="/api/v1/agentos", tags=["Dashboard"])
app.include_router(data_routing_router, prefix="/api/v1/agentos", tags=["Data Routing"])
app.include_router(oql_router, prefix="/api/v1/agentos", tags=["OQL"])

# ── Runtime Gateway — unified public façade ─────────────────────────────────
app.include_router(planet_router, prefix="/api/v1/agentos", tags=["Planet"])
app.include_router(societies_router, prefix="/api/v1/agentos", tags=["Societies"])
app.include_router(actors_router, prefix="/api/v1/agentos", tags=["Actors"])
app.include_router(memberships_router, prefix="/api/v1/agentos", tags=["Memberships"])
app.include_router(runtime_gw_router, prefix="/api/v1/agentos", tags=["Runtime"])
app.include_router(simulation_gw_router, prefix="/api/v1/agentos", tags=["Simulate"])
app.include_router(comparator_gw_router, prefix="/api/v1/agentos", tags=["Compare"])
app.include_router(learning_gw_router, prefix="/api/v1/agentos", tags=["Learning"])
app.include_router(world_router, prefix="/api/v1/agentos", tags=["World"])
app.include_router(discovery_router, prefix="/api/v1/agentos", tags=["Discovery"])
app.include_router(security_router, prefix="/api/v1/agentos", tags=["Security"])
app.include_router(admin_router, prefix="/api/v1/agentos", tags=["Admin"])
app.include_router(knowledge_graph_router, tags=["KnowledgeGraph"])
app.include_router(actor_profile_router, tags=["ActorProfile"])
app.include_router(commerce_router, prefix="/api/v1/agentos", tags=["Commerce"])
app.include_router(orders_router, prefix="/api/v1/agentos", tags=["Orders"])
app.include_router(fulfillment_router, prefix="/api/v1/agentos", tags=["Fulfillment"])
app.include_router(events_router, prefix="/api/v1/agentos", tags=["Events"])
app.include_router(presence_router, prefix="/api/v1/agentos", tags=["Presence"])
app.include_router(verify_router, prefix="/api/v1/agentos", tags=["Verify"])
app.include_router(ws_router, prefix="/api/v1/agentos", tags=["WebSocket"])
app.include_router(approval_router, prefix="/api/v1/agentos", tags=["Approval"])
app.include_router(livekit_router, prefix="/api/v1/agentos", tags=["LiveKit"])
app.include_router(negotiation_router, prefix="/api/v1/agentos", tags=["Negotiation"])
app.include_router(payments_router, prefix="/api/v1/agentos", tags=["Payments"])
app.include_router(edge_router, prefix="/api/v1/agentos", tags=["Edge"])

# ── Exchange Server (network transport for knowledge proposals) ──────────────
try:
    from src.monkey_brain.kernel.compile.network import (
        configure_exchange,
        secure_mode_preflight,
    )

    # Surface the security posture at boot (and hard-fail with AGENTOS_SECURE_MODE=1 if the
    # deployment is configured to run open — a pilot must not silently be insecure).
    secure_mode_preflight()

    # CA-backed identity, revocation (CRL), transport auth and bounded queue are wired from
    # env inside configure_exchange (see AGENTOS_CA_STORE / AGENTOS_EXCHANGE_TOKEN).
    _exchange, _exchange_server, _exchange_flusher = configure_exchange()
    app.include_router(_exchange_server.create_router(), prefix="/api/v1/agentos", tags=["Exchange"])
    logger.info("Exchange server registered — network transport enabled")
except RuntimeError:
    raise  # strict secure-mode failure — do not swallow
except Exception as exc:
    logger.warning("Exchange server not available: %s", exc)


def _overall_health(request: Request) -> str:
    lemon = getattr(request.app.state, "lemon", None)
    return lemon.overall_health() if lemon else "unknown"


@app.get("/health", tags=["Health"])
async def health_check(request: Request):
    """LIVENESS — is this process alive? No auth required.

    Deliberately does NOT gate on external dependencies: a Mongo/Neo4j blip must not make
    every pod fail its liveness probe and get restarted. Use /ready for traffic decisions.

    `status` now reflects the ACTUAL health. It used to be hardcoded "healthy" — the handler
    computed overall_health() and then ignored it, so a node with Mongo and Neo4j down (every
    request hanging for minutes) still answered 200 "healthy" to its load balancer.
    """
    try:
        lemon = getattr(request.app.state, "lemon", None)
        health = lemon.overall_health() if lemon else "unknown"
        checks = {}
        if lemon:
            for name, check in lemon.health._checks.items():
                entry = {"status": check.status}
                if check.message:
                    entry["message"] = check.message
                checks[name] = entry
        result = {"status": health, "service": "monkeybrain-runtime", "health": health}
        if checks:
            result["checks"] = checks
        return result
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "unhealthy", "error": str(exc)})


@app.get("/live", tags=["Health"])
async def liveness_check() -> dict:
    """LIVENESS, minimal — Gate 8 (Operations).

    /health already documents itself as "LIVENESS — is this process
    alive?" and deliberately avoids gating on external dependencies —
    but it still calls lemon.overall_health() and walks lemon.health.
    _checks, i.e. it depends on Lemon having booted correctly. This
    endpoint depends on nothing at all beyond the ASGI server itself
    being able to route a request to a handler and return — the truest
    possible liveness signal, and the correct one for a Kubernetes
    livenessProbe: if EVEN THIS hangs or fails, the process is
    genuinely wedged and restarting the pod is the right call; a Lemon
    internals problem is a different, less severe failure that
    shouldn't by itself trigger a restart loop. /health remains
    available as the more detailed status endpoint.
    """
    return {"status": "alive"}


@app.get("/ready", tags=["Health"])
async def readiness_check(request: Request):
    """READINESS — should this instance receive traffic? No auth required.

    Returns 503 when a required dependency is down, so the load balancer takes the instance
    out of rotation instead of routing requests that will block on an unreachable datastore.
    "degraded" still serves (impaired, not broken); "unhealthy" does not.
    """
    try:
        health = _overall_health(request)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "health": "unhealthy", "error": str(exc)},
        )
    if health == "unhealthy":
        return JSONResponse(status_code=503, content={"ready": False, "health": health})
    return {"ready": True, "health": health, "service": "monkeybrain-runtime"}
