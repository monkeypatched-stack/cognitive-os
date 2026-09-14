from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.process_definitions.routers.cpp_cqa_registry import (
    router as cpp_cqa_registry_router,
)
from services.process_definitions.routers.mbmr_templates import (
    router as mbmr_templates_router,
)
from services.process_definitions.routers.packing_instructions import (
    router as packing_instructions_router,
)
from services.process_definitions.routers.process_definition import (
    router as process_definition_router,
)
from services.process_definitions.routers.process_steps import (
    router as process_steps_router,
)
from services.process_definitions.routers.process_pre_checks import (
    router as process_definition_prechecks_router,
)
from services.process_definitions.routers.process_post_checks import (
    router as process_definition_postchecks_router,
)
from services.process_definitions.routers.process_constraints import (
    router as process_constraints_router,
)
from services.process_definitions.routers.process_corrections import (
    router as process_corrections_router,
)

logger = configure_service_logging("process_definition")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Process Definition Service",
    description="Microservice for process definition service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "process_definition")
install_route_tracing(app, "process_definition")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=(
        r"^http://("
        r"192\.168\.\d+\.\d+|"
        r"10\.\d+\.\d+\.\d+|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|"
        r"[A-Za-z0-9.-]+\.local"
        r"):5173$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    process_definition_router,
    prefix="/api/v1/process-definitions",
    tags=["Process Definitions"],
)
app.include_router(
    cpp_cqa_registry_router,
    prefix="/api/v1/cpp-cqa-registry",
    tags=["CPP CQA Registry"],
)
app.include_router(
    mbmr_templates_router, prefix="/api/v1/mbmr-templates", tags=["MBMR Templates"]
)
app.include_router(
    packing_instructions_router,
    prefix="/api/v1/packing-instructions",
    tags=["Packing Instructions"],
)
app.include_router(
    process_steps_router, prefix="/api/v1/process-steps", tags=["Process Steps"]
)
app.include_router(
    process_definition_prechecks_router,
    prefix="/api/v1/process-prechecks",
    tags=["Process Prechecks"],
)
app.include_router(
    process_definition_postchecks_router,
    prefix="/api/v1/process-postchecks",
    tags=["Process Postchecks"],
)
app.include_router(
    process_constraints_router,
    prefix="/api/v1/process-constraints",
    tags=["Process Constraints"],
)
app.include_router(
    process_corrections_router,
    prefix="/api/v1/process-corrections",
    tags=["Process Corrections"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
