from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.cors import cors_allow_origins

from services.common.db import close_db, connect_db
from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing
from services.replenishment.routers.replenishment import router as replenishment_router

logger = configure_service_logging("replenishment")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Replenishment Service",
    description="Microservice for reorder planning, safety stock checks, lead-time planning, purchase proposals, and transfer proposals.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "replenishment")
install_route_tracing(app, "replenishment")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    replenishment_router, prefix="/api/v1/replenishment", tags=["Replenishment"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
