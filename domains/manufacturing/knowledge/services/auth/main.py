from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.mongo_cdc_watcher import (
    cdc_watcher_status,
    start_mongo_cdc_watcher,
    stop_mongo_cdc_watcher,
)
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db, get_database
from services.auth.helpers.audit_elasticsearch_sync import (
    start_audit_elasticsearch_sync,
    stop_audit_elasticsearch_sync,
)
from services.auth.helpers.graph_store import close_graph_driver
from services.auth.helpers.influx_store import ensure_event_measurements
from services.auth.helpers.nats_consumer import (
    consumer_status,
    start_influx_event_consumer,
    stop_influx_event_consumer,
)
from services.auth.helpers.nats_store import close_nats_client
from services.auth.routers.auth import router as auth_router
from services.auth.routers.me import router as me_router
from services.auth.routers.users import router as users_router
from services.auth.routers.roles import router as roles_router
from services.auth.routers.permissions import router as permissions_router
from services.auth.routers.departments import router as departments_router
from services.auth.routers.teams import router as teams_router
from services.auth.routers.team_members import router as members_router
from services.auth.routers.session import router as session_router
from services.auth.routers.websocket_events import router as websocket_events_router
from services.auth.routers.integration_config import router as integration_config_router
from services.auth.routers.agent_auth import router as agent_auth_router
from services.common.mtls import MTLSMiddleware
from services.common.trace_middleware import TraceMiddleware

logger = configure_service_logging("auth")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    await ensure_event_measurements()
    await start_audit_elasticsearch_sync(get_database())
    await start_influx_event_consumer()
    await start_mongo_cdc_watcher(get_database().raw_database)
    yield
    await stop_mongo_cdc_watcher()
    await stop_audit_elasticsearch_sync()
    await stop_influx_event_consumer()
    await close_nats_client()
    await close_graph_driver()
    await close_db()


app = FastAPI(
    title="Auth and Organization Service",
    description="Microservice for auth and organization service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "auth")
install_route_tracing(app, "auth")

app.add_middleware(
    TraceMiddleware
)  # INTRO-INV-003: propagate trace_id on every request
app.add_middleware(MTLSMiddleware)  # no-op unless MTLS_ENABLED=true
app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(
    auth_router, prefix="/api/auth", tags=["Auth-Frontend"]
)  # Frontend compatibility
app.include_router(me_router, prefix="/api/v1/me", tags=["Me"])
app.include_router(users_router, prefix="/api/v1/users", tags=["Users"])
app.include_router(roles_router, prefix="/api/v1/roles", tags=["Roles"])
app.include_router(
    permissions_router, prefix="/api/v1/permissions", tags=["Permissions"]
)
app.include_router(
    departments_router, prefix="/api/v1/departments", tags=["Departments"]
)
app.include_router(teams_router, prefix="/api/v1/teams", tags=["Teams"])
app.include_router(members_router, prefix="/api/v1/members", tags=["Members"])
app.include_router(session_router, prefix="/api/v1/session", tags=["Session"])
app.include_router(
    websocket_events_router, prefix="/api/v1/ws", tags=["WebSocket Events"]
)
app.include_router(
    integration_config_router, prefix="/integration-config", tags=["Integration Config"]
)
app.include_router(
    integration_config_router,
    prefix="/api/v1/integration-config",
    tags=["Integration Config"],
)
app.include_router(agent_auth_router, prefix="/api/v1/agent", tags=["Agent Auth"])


@app.get("/health", tags=["Health"])
async def health_check():
    nats_status = consumer_status()
    return {
        "status": "healthy",
        "nats_consumers": nats_status,
        "approval_audit_consumer": nats_status["approval_audit_consumer"],
        "audit_consumer": nats_status["audit_consumer"],
        "mongo_cdc_watcher": cdc_watcher_status(),
    }
