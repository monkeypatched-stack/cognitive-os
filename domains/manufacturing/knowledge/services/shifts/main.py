from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.shifts.routers.shift_templates import router as shift_templates_router
from services.shifts.routers.shifts import router as shifts_router
from services.shifts.routers.timesheets import router as timesheets_router

logger = configure_service_logging("shifts")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Shifts Service",
    description="Microservice for shifts service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "shifts")
install_route_tracing(app, "shifts")

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
    shift_templates_router, prefix="/api/v1/shift-templates", tags=["Shift Templates"]
)
app.include_router(shifts_router, prefix="/api/v1/shifts", tags=["Shifts"])
app.include_router(timesheets_router, prefix="/api/v1/timesheets", tags=["Timesheets"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
