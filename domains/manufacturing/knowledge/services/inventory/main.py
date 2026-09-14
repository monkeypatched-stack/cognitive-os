from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.inventory.routers.inventory_allocations import (
    router as inventory_allocations_router,
)
from services.inventory.routers.inventory_item_counts import (
    router as inventory_item_counts_router,
)
from services.inventory.routers.inventory_item_quantities import (
    router as inventory_item_quantities_router,
)
from services.inventory.routers.inventory_items import router as inventory_items_router
from services.inventory.routers.inventory_stage_outputs import (
    router as inventory_stage_outputs_router,
)
from services.inventory.routers.stock_adjustment_requests import (
    router as stock_adjustment_requests_router,
)
from services.inventory.routers.inventory_transactions import (
    router as inventory_transactions_router,
)
from services.inventory.routers.warehouse_regulated_records import (
    router as warehouse_regulated_records_router,
)
from services.inventory.routers.warehouse_cycle_counts import (
    router as warehouse_cycle_counts_router,
)
from services.inventory.routers.warehouse_locations import (
    router as warehouse_locations_router,
)

logger = configure_service_logging("inventory")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Inventory Service",
    description="Microservice for inventory service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "inventory")
install_route_tracing(app, "inventory")

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
    inventory_allocations_router,
    prefix="/api/v1/inventory-allocations",
    tags=["Inventory Allocations"],
)
app.include_router(
    inventory_items_router, prefix="/api/v1/inventory-items", tags=["Inventory Items"]
)
app.include_router(
    inventory_item_counts_router,
    prefix="/api/v1/inventory-item-counts",
    tags=["Inventory Item Counts"],
)
app.include_router(
    inventory_item_quantities_router,
    prefix="/api/v1/inventory-item-quantities",
    tags=["Inventory Item Quantities"],
)
app.include_router(
    inventory_stage_outputs_router,
    prefix="/api/v1/inventory-stage-outputs",
    tags=["Inventory Stage Outputs"],
)
app.include_router(
    stock_adjustment_requests_router,
    prefix="/api/v1/stock-adjustment-requests",
    tags=["Stock Adjustment Requests"],
)
app.include_router(
    inventory_transactions_router,
    prefix="/api/v1/inventory-transactions",
    tags=["Inventory Transactions"],
)
app.include_router(
    warehouse_cycle_counts_router,
    prefix="/api/v1/warehouse-cycle-counts",
    tags=["Warehouse Cycle Counts"],
)
app.include_router(
    warehouse_locations_router,
    prefix="/api/v1/warehouse-locations",
    tags=["Warehouse Locations"],
)
app.include_router(
    warehouse_regulated_records_router,
    prefix="/api/v1/warehouse",
    tags=["Warehouse Regulated Records"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
