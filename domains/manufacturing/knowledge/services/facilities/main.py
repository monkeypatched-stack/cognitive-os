from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.facilities.routers.plant import router as plants_router
from services.facilities.routers.lines import router as lines_router
from services.facilities.routers.stages import router as stages_router
from services.facilities.routers.workstation import router as workstations_router
from services.facilities.routers.locations import router as locations_router
from services.facilities.routers.buildings import router as buildings_router
from services.facilities.routers.floors import router as floors_router
from services.facilities.routers.rooms import router as rooms_router
from services.facilities.routers.bays import router as bays_router

logger = configure_service_logging("facilities")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Facilities Service",
    description="Microservice for facilities service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "facilities")
install_route_tracing(app, "facilities")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plants_router, prefix="/api/v1/plants", tags=["Plants"])
app.include_router(lines_router, prefix="/api/v1/lines", tags=["Lines"])
app.include_router(stages_router, prefix="/api/v1/stages", tags=["Stages"])
app.include_router(
    workstations_router, prefix="/api/v1/workstations", tags=["Workstations"]
)
app.include_router(locations_router, prefix="/api/v1/locations", tags=["Locations"])
app.include_router(buildings_router, prefix="/api/v1/buildings", tags=["Buildings"])
app.include_router(floors_router, prefix="/api/v1/floors", tags=["Floors"])
app.include_router(rooms_router, prefix="/api/v1/rooms", tags=["Rooms"])
app.include_router(bays_router, prefix="/api/v1/bays", tags=["Bays"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
