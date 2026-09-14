from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.procurement.routers.goods_receipts import router as goods_receipts_router
from services.procurement.routers.purchase_orders import (
    router as purchase_orders_router,
)
from services.procurement.routers.shipping_information import (
    router as purchase_order_shipping_router,
)
from services.procurement.routers.quality_control import (
    router as quality_control_router,
)
from services.procurement.routers.sampling import router as sampling_router

logger = configure_service_logging("procurement")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Procurement Service",
    description="Microservice for procurement service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "procurement")
install_route_tracing(app, "procurement")

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
    purchase_orders_router, prefix="/api/v1/purchase-orders", tags=["Purchase Orders"]
)
app.include_router(
    purchase_order_shipping_router,
    prefix="/api/v1/purchase-order-shipping-information",
    tags=["Purchase Order Shipping Information"],
)
app.include_router(
    goods_receipts_router, prefix="/api/v1/goods-receipts", tags=["Goods Receipts"]
)
app.include_router(sampling_router, prefix="/api/v1/sampling", tags=["Sampling"])
app.include_router(
    quality_control_router, prefix="/api/v1/quality-control", tags=["Quality Control"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
