from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.workorders.routers.area_room_usage_ledger import (
    router as area_room_usage_ledger_router,
)
from services.workorders.routers.batch_execution_records import (
    router as batch_execution_records_router,
)
from services.workorders.routers.batch_release_workflows import (
    router as batch_release_workflows_router,
)
from services.workorders.routers.batch_step_executions import (
    router as batch_step_executions_router,
)
from services.workorders.routers.equipment_usage_ledger import (
    router as equipment_usage_ledger_router,
)
from services.workorders.routers.executed_bmr_records import (
    router as executed_bmr_records_router,
)
from services.workorders.routers.executed_bpr_records import (
    router as executed_bpr_records_router,
)
from services.workorders.routers.executed_instruction_evidence import (
    router as executed_instruction_evidence_router,
)
from services.workorders.routers.ipc_result_records import (
    router as ipc_result_records_router,
)
from services.workorders.routers.production_batches import (
    router as production_batches_router,
)
from services.workorders.routers.work_orders import router as workorders_router
from services.workorders.routers.yield_reconciliation_records import (
    router as yield_reconciliation_records_router,
)

logger = configure_service_logging("workorders")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Workorders Service",
    description="Microservice for workorders service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "workorders")
install_route_tracing(app, "workorders")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workorders_router, prefix="/api/v1/workorders", tags=["Workorders"])
app.include_router(
    production_batches_router,
    prefix="/api/v1/production-batches",
    tags=["Production Batches"],
)
app.include_router(
    batch_execution_records_router,
    prefix="/api/v1/batch-execution-records",
    tags=["Batch Production Execution Records"],
)
app.include_router(
    executed_bmr_records_router,
    prefix="/api/v1/executed-bmr-records",
    tags=["Executed BMR Records"],
)
app.include_router(
    executed_bpr_records_router,
    prefix="/api/v1/executed-bpr-records",
    tags=["Executed BPR Records"],
)
app.include_router(
    executed_instruction_evidence_router,
    prefix="/api/v1/executed-instruction-evidence",
    tags=["Executed Instruction Evidence"],
)
app.include_router(
    ipc_result_records_router,
    prefix="/api/v1/ipc-result-records",
    tags=["IPC Result Records"],
)
app.include_router(
    batch_release_workflows_router,
    prefix="/api/v1/batch-release-workflows",
    tags=["Batch Release Workflows"],
)
app.include_router(
    batch_step_executions_router,
    prefix="/api/v1/batch-step-executions",
    tags=["Batch Step Executions"],
)
app.include_router(
    equipment_usage_ledger_router,
    prefix="/api/v1/equipment-usage-ledger",
    tags=["Equipment Usage Ledger"],
)
app.include_router(
    area_room_usage_ledger_router,
    prefix="/api/v1/area-room-usage-ledger",
    tags=["Area Room Usage Ledger"],
)
app.include_router(
    yield_reconciliation_records_router,
    prefix="/api/v1/yield-reconciliation-records",
    tags=["Yield Reconciliation Records"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
