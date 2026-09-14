from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.changeover.routers.changeover_matrix import (
    router as changeover_matrix_router,
)
from services.changeover.routers.changeover_procedures import (
    router as changeover_procedures_router,
)
from services.changeover.routers.changeover_windows import (
    router as changeover_windows_router,
)
from services.changeover.routers.changeover_kpis import router as changeover_kpis_router
from services.changeover.routers.changeover_events import (
    router as changeover_events_router,
)

logger = configure_service_logging("changeover")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Changeover Service",
    description="Microservice for changeover service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "changeover")
install_route_tracing(app, "changeover")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    changeover_matrix_router,
    prefix="/api/v1/changeovers/matrix",
    tags=["Changeover Matrix"],
)
app.include_router(
    changeover_procedures_router,
    prefix="/api/v1/changeovers/procedures",
    tags=["Changeover Procedures"],
)
app.include_router(
    changeover_windows_router, prefix="/api/v1/changeovers", tags=["Changeover Windows"]
)
app.include_router(
    changeover_kpis_router, prefix="/api/v1/changeovers", tags=["Changeover KPIs"]
)
app.include_router(
    changeover_events_router, prefix="/api/v1/changeovers", tags=["Changeover Events"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
