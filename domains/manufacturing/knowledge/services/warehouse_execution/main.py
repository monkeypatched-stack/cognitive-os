from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.cors import cors_allow_origins

from services.common.db import close_db, connect_db
from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing
from services.warehouse_execution.routers.execution import router as execution_router

logger = configure_service_logging("warehouse_execution")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Warehouse Execution Service",
    description="Microservice for warehouse task execution, scanner validation, exception resolution, and WMS workflow orchestration.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "warehouse_execution")
install_route_tracing(app, "warehouse_execution")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    execution_router, prefix="/api/v1/warehouse-execution", tags=["Warehouse Execution"]
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
