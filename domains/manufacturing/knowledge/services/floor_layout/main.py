from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.floor_layout.routers.buildings import router as buildings_router
from services.floor_layout.routers.floors import router as floors_router
from services.floor_layout.routers.rooms import router as rooms_router
from services.floor_layout.routers.bays import router as bays_router
from services.floor_layout.routers.locations import router as locations_router

logger = configure_service_logging("floor-layout")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Floor Layout Service",
    description="Microservice for floor layout service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "floor-layout")
install_route_tracing(app, "floor-layout")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(buildings_router, prefix="/api/v1/buildings", tags=["Buildings"])
app.include_router(floors_router, prefix="/api/v1/floors", tags=["Floors"])
app.include_router(rooms_router, prefix="/api/v1/rooms", tags=["Rooms"])
app.include_router(bays_router, prefix="/api/v1/bays", tags=["Bays"])
app.include_router(locations_router, prefix="/api/v1/locations", tags=["Locations"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
