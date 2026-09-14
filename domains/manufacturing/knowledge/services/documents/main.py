from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.documents.routers.document_metadata import (
    router as document_metadata_router,
)
from services.documents.routers.document_workflows import (
    router as document_workflows_router,
)
from services.documents.routers.report_templates import (
    router as report_templates_router,
)

logger = configure_service_logging("documents")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Documents Service",
    description="Microservice for document metadata, storage, and document workflows.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "documents")
install_route_tracing(app, "documents")

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
    document_metadata_router,
    prefix="/api/v1/document-metadata",
    tags=["Document Metadata"],
)
app.include_router(
    document_metadata_router, prefix="/api/v1/documents", tags=["Documents"]
)
app.include_router(
    document_workflows_router,
    prefix="/api/v1/document-workflows",
    tags=["Document Workflows"],
)
app.include_router(
    report_templates_router,
    prefix="/api/v1/report-templates",
    tags=["Report Templates"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
