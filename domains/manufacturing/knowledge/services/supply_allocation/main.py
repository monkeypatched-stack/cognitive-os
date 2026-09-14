from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.cors import cors_allow_origins

from services.common.db import close_db, connect_db
from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing
from services.supply_allocation.routers.allocation import router as allocation_router

logger = configure_service_logging("supply_allocation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Supply Allocation Service",
    description="Microservice for available-to-promise, reservations, commitments, and allocation arbitration across demand sources.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "supply_allocation")
install_route_tracing(app, "supply_allocation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    allocation_router, prefix="/api/v1/supply-allocation", tags=["Supply Allocation"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
