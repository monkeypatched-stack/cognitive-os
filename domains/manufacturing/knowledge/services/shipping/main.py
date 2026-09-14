from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.shipping.routers.carrier import router as carrier_router
from services.shipping.routers.customs_declaration import (
    router as customs_declaration_router,
)
from services.shipping.routers.delivery_note import router as delivery_note_router
from services.shipping.routers.package import router as package_router
from services.shipping.routers.pallet import router as pallet_router
from services.shipping.routers.route import router as route_router
from services.shipping.routers.shipping_information import (
    router as shipping_information_router,
)
from services.shipping.routers.shipping_provider_details import (
    router as shipping_provider_details_router,
)
from services.shipping.routers.shipping_provider_metadata import (
    router as shipping_provider_metadata_router,
)
from services.shipping.routers.vehicle import router as vehicle_router
from services.shipping.routers.waybill import router as waybill_router

logger = configure_service_logging("shipping")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Shipping Service",
    description="Microservice for shipping service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "shipping")
install_route_tracing(app, "shipping")

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
    shipping_information_router,
    prefix="/api/v1/shipping-information",
    tags=["Shipping Information"],
)
app.include_router(carrier_router, prefix="/api/v1/carriers", tags=["Carriers"])
app.include_router(vehicle_router, prefix="/api/v1/vehicles", tags=["Vehicles"])
app.include_router(pallet_router, prefix="/api/v1/pallets", tags=["Pallets"])
app.include_router(package_router, prefix="/api/v1/packages", tags=["Packages"])
app.include_router(
    delivery_note_router, prefix="/api/v1/delivery-notes", tags=["Delivery Notes"]
)
app.include_router(route_router, prefix="/api/v1/routes", tags=["Routes"])
app.include_router(
    customs_declaration_router,
    prefix="/api/v1/customs-declarations",
    tags=["Customs Declarations"],
)
app.include_router(
    shipping_provider_details_router,
    prefix="/api/v1/shipping-providers",
    tags=["Shipping Providers"],
)
app.include_router(
    shipping_provider_metadata_router,
    prefix="/api/v1/shipping-provider-metadata",
    tags=["Shipping Provider Metadata"],
)
app.include_router(waybill_router, prefix="/api/v1/waybills", tags=["Waybills"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
