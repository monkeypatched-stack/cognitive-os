from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.db import close_db, connect_db
from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing
from services.module_control.routers.module_control import (
    router as module_control_router,
)

logger = configure_service_logging("module_control")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Module Control Service",
    description="Control plane for module activation, endpoint access, customer limits, and integration activation.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "module_control")
install_route_tracing(app, "module_control")

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
    module_control_router, prefix="/api/v1/module-control", tags=["Module Control"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
