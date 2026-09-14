from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.db import close_db, connect_db
from services.taxonomy.routers.family import router as family_router
from services.taxonomy.routers.classes import router as classes_router
from services.taxonomy.routers.subclass import router as subclasses_router

logger = configure_service_logging("taxonomy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


app = FastAPI(
    title="Taxonomy Service",
    description="Microservice for taxonomy service.",
    version="1.0.0",
    lifespan=lifespan,
)
install_request_logging(app, "taxonomy")
install_route_tracing(app, "taxonomy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(family_router, prefix="/api/v1/families", tags=["Families"])
app.include_router(classes_router, prefix="/api/v1/classes", tags=["Classes"])
app.include_router(subclasses_router, prefix="/api/v1/subclasses", tags=["Subclasses"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
