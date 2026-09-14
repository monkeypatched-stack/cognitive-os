from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.pm.routers.maintainance import router as maintenance_router
from services.pm.routers.calibrations import router as calibrations_router
from services.pm.routers.calibration_point import router as calibration_points_router
from services.pm.routers.cleaning import router as cleaning_router
from services.pm.routers.downtime import router as downtime_router
from services.pm.routers.checklists import router as checklists_router
from services.pm.routers.sop import router as sop_router
from services.pm.routers.weekly_schedules import router as weekly_schedules_router
from services.pm.routers.calendar_bookings import router as calendar_bookings_router
from services.pm.routers.kanban_boards import router as kanban_boards_router

logger = configure_service_logging("pm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Preventive Maintenance Service",
    description="Microservice for preventive maintenance service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "pm")
install_route_tracing(app, "pm")

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
    maintenance_router, prefix="/api/v1/maintenance", tags=["Maintenance"]
)
app.include_router(
    calibrations_router, prefix="/api/v1/calibrations", tags=["Calibrations"]
)
app.include_router(
    calibration_points_router,
    prefix="/api/v1/calibrations/{calibration_id}/points",
    tags=["Calibration Points"],
)
app.include_router(cleaning_router, prefix="/api/v1/cleaning", tags=["Cleaning"])
app.include_router(downtime_router, prefix="/api/v1/downtime", tags=["Downtime"])
app.include_router(checklists_router, prefix="/api/v1/checklists", tags=["Checklists"])
app.include_router(sop_router, prefix="/api/v1/sops", tags=["SOPs"])
app.include_router(
    weekly_schedules_router,
    prefix="/api/v1/weekly-schedules",
    tags=["Weekly Schedules"],
)
app.include_router(
    calendar_bookings_router,
    prefix="/api/v1/calendar-bookings",
    tags=["Calendar Bookings"],
)
app.include_router(
    kanban_boards_router, prefix="/api/v1/kanban-boards", tags=["Kanban Boards"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
