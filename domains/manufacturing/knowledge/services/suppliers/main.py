from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.suppliers.routers.supplier_details import (
    router as supplier_details_router,
)
from services.suppliers.routers.supplier_capabilities import (
    router as supplier_capabilities_router,
)
from services.suppliers.routers.supplier_certifications import (
    router as supplier_certifications_router,
)
from services.suppliers.routers.supplier_financials import (
    router as supplier_financials_router,
)
from services.suppliers.routers.supplier_inventory import (
    router as supplier_inventory_router,
)
from services.suppliers.routers.supplier_locations import (
    router as supplier_locations_router,
)
from services.suppliers.routers.supplier_pricing import (
    router as supplier_pricing_router,
)
from services.suppliers.routers.supplier_quality import (
    router as supplier_quality_router,
)
from services.suppliers.routers.supplier_shipping import (
    router as supplier_shipping_router,
)

logger = configure_service_logging("suppliers")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Suppliers Service",
    description="Microservice for suppliers service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "suppliers")
install_route_tracing(app, "suppliers")

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
    supplier_details_router,
    prefix="/api/v1/supplier-details",
    tags=["Supplier Details"],
)
app.include_router(
    supplier_capabilities_router,
    prefix="/api/v1/supplier-capabilities",
    tags=["Supplier Capabilities"],
)
app.include_router(
    supplier_certifications_router,
    prefix="/api/v1/supplier-certifications",
    tags=["Supplier Certifications"],
)
app.include_router(
    supplier_financials_router,
    prefix="/api/v1/supplier-financials",
    tags=["Supplier Financials"],
)
app.include_router(
    supplier_inventory_router,
    prefix="/api/v1/supplier-inventory",
    tags=["Supplier Inventory"],
)
app.include_router(
    supplier_locations_router,
    prefix="/api/v1/supplier-locations",
    tags=["Supplier Locations"],
)
app.include_router(
    supplier_pricing_router,
    prefix="/api/v1/supplier-pricing",
    tags=["Supplier Pricing"],
)
app.include_router(
    supplier_quality_router,
    prefix="/api/v1/supplier-quality",
    tags=["Supplier Quality"],
)
app.include_router(
    supplier_shipping_router,
    prefix="/api/v1/supplier-shipping",
    tags=["Supplier Shipping"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
