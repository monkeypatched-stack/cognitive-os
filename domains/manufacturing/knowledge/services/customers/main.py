from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.customers.routers.customer_details import (
    router as customer_details_router,
)
from services.customers.routers.customer_metadata import (
    router as customer_metadata_router,
)
from services.customers.routers.customer_order_metrics import (
    router as customer_order_metrics_router,
)
from services.customers.routers.customer_payment_data import (
    router as customer_payment_data_router,
)

logger = configure_service_logging("customers")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Customers Service",
    description="Microservice for customers service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "customers")
install_route_tracing(app, "customers")

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
    customer_details_router,
    prefix="/api/v1/customer-details",
    tags=["Customer Details"],
)
app.include_router(
    customer_metadata_router,
    prefix="/api/v1/customer-metadata",
    tags=["Customer Metadata"],
)
app.include_router(
    customer_order_metrics_router,
    prefix="/api/v1/customer-order-metrics",
    tags=["Customer Order Metrics"],
)
app.include_router(
    customer_payment_data_router,
    prefix="/api/v1/customer-payment-data",
    tags=["Customer Payment Data"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
