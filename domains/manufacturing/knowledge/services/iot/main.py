from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.iot.helpers.mqtt_bridge import start_mqtt_bridge, stop_mqtt_bridge
from services.iot.routers.ble_device import router as ble_devices_router
from services.iot.routers.sensors import router as sensors_router
from services.iot.routers.servers import router as servers_router
from services.iot.routers.uwb_device import router as uwb_devices_router

logger = configure_service_logging("iot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    await start_mqtt_bridge()
    yield
    await stop_mqtt_bridge()
    await close_db()


app = FastAPI(
    title="IoT Service",
    description="Microservice for iot service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "iot")
install_route_tracing(app, "iot")

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
    ble_devices_router, prefix="/api/v1/ble-devices", tags=["BLE Devices"]
)
app.include_router(sensors_router, prefix="/api/v1/sensors", tags=["Sensors"])
app.include_router(servers_router, prefix="/api/v1/servers", tags=["Servers"])
app.include_router(
    uwb_devices_router, prefix="/api/v1/uwb-devices", tags=["UWB Devices"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
