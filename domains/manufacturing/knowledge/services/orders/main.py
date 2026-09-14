from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.orders.routers.invoices import router as invoices_router
from services.orders.routers.orders import router as orders_router
from services.orders.routers.order_details import router as order_details_router
from services.orders.routers.order_customer_metrics import (
    router as order_customer_metrics_router,
)
from services.orders.routers.order_metadata import router as order_metadata_router
from services.orders.routers.order_payment_metadata import (
    router as order_payment_metadata_router,
)
from services.orders.routers.sales_orders import router as sales_orders_router

logger = configure_service_logging("orders")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Orders Service",
    description="Microservice for orders service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "orders")
install_route_tracing(app, "orders")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders_router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(invoices_router, prefix="/api/v1/invoices", tags=["Invoices"])
app.include_router(
    order_details_router, prefix="/api/v1/order-details", tags=["Order Details"]
)
app.include_router(
    order_customer_metrics_router,
    prefix="/api/v1/order-customer-metrics",
    tags=["Order Customer Metrics"],
)
app.include_router(
    order_metadata_router, prefix="/api/v1/order-metadata", tags=["Order Metadata"]
)
app.include_router(
    order_payment_metadata_router,
    prefix="/api/v1/order-payment-metadata",
    tags=["Order Payment Metadata"],
)
app.include_router(
    sales_orders_router, prefix="/api/v1/sales-orders", tags=["Sales Orders"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
