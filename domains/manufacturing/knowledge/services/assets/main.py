from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.compat import collection_router
from services.common.db import close_db, connect_db
from services.assets.routers.device import router as devices_router
from services.assets.routers.equipment import router as equipment_router
from services.assets.routers.machines import router as machines_router
from services.assets.routers.materials import router as materials_router
from services.assets.routers.parts import router as parts_router
from services.assets.routers.plc import router as plc_router
from services.assets.routers.tags import router as tags_router
from services.assets.routers.tools import router as tools_router
from services.assets.routers.instruments import router as instruments_router
from services.assets.routers.families import router as families_router
from services.assets.routers.classes import router as classes_router
from services.assets.routers.subclasses import router as subclasses_router

logger = configure_service_logging("assets")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Assets Service",
    description="Microservice for assets service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "assets")
install_route_tracing(app, "assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(devices_router, prefix="/api/v1/devices", tags=["Devices"])
app.include_router(equipment_router, prefix="/api/v1/equipment", tags=["Equipment"])
app.include_router(machines_router, prefix="/api/v1/machines", tags=["Machines"])
app.include_router(materials_router, prefix="/api/v1/chemicals", tags=["Chemicals"])
app.include_router(parts_router, prefix="/api/v1/parts", tags=["Parts"])
app.include_router(plc_router, prefix="/api/v1/plcs", tags=["PLCs"])
app.include_router(tags_router, prefix="/api/v1/rfid-tags", tags=["RFID Tags"])
app.include_router(tools_router, prefix="/api/v1/tools", tags=["Tools"])
app.include_router(
    instruments_router, prefix="/api/v1/instruments", tags=["Instruments"]
)
app.include_router(families_router, prefix="/api/v1/families", tags=["Families"])
app.include_router(classes_router, prefix="/api/v1/classes", tags=["Classes"])
app.include_router(subclasses_router, prefix="/api/v1/subclasses", tags=["Subclasses"])
app.include_router(
    collection_router(
        [
            "/api/v1/equipment-groups",
            "/api/v1/machine-groups",
            "/api/v1/raw-material-groups",
        ]
    ),
    tags=["Compatibility"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
