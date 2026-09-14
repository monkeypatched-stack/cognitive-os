from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.compat import collection_router
from services.common.db import close_db, connect_db
from services.products.routers.products import router as products_router
from services.products.routers.boms import router as boms_router
from services.products.routers.product_components import (
    router as product_components_router,
)
from services.products.routers.product_inventory import (
    router as product_inventory_router,
)
from services.products.routers.product_pricing import router as product_pricing_router
from services.products.routers.drug_research import router as drug_research_router

logger = configure_service_logging("products")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Products Service",
    description="Microservice for products service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "products")
install_route_tracing(app, "products")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(products_router, prefix="/api/v1/products", tags=["Products"])
app.include_router(boms_router, prefix="/api/v1/boms", tags=["BOMs"])
app.include_router(
    product_components_router,
    prefix="/api/v1/products/components",
    tags=["Product Components"],
)
app.include_router(
    product_inventory_router,
    prefix="/api/v1/products/inventory",
    tags=["Product Inventory"],
)
app.include_router(
    product_pricing_router, prefix="/api/v1/products/pricing", tags=["Product Pricing"]
)
app.include_router(
    drug_research_router,
    prefix="/api/v1/product-research/drugs-india",
    tags=["India Drug Formulation Research"],
)
app.include_router(
    collection_router(["/api/v1/tag-groups"]),
    tags=["Compatibility"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
