from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.common.logging import configure_service_logging, install_request_logging
from services.common.tracing import install_route_tracing

from services.common.compat import empty_collection_router
from services.events.routers.count_events import router as count_events_router
from services.events.routers.events import router as events_router

logger = configure_service_logging("events")

app = FastAPI(
    title="Events Service",
    description="Microservice for events service.",
    version="1.0.0",
)
install_request_logging(app, "events")
install_route_tracing(app, "events")

app.add_middleware(
    CORSMiddleware,
    allow_origins=__import__(
        "services.common.config", fromlist=["cors_allow_origins"]
    ).cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events_router, prefix="/api/v1/events", tags=["Events"])
app.include_router(count_events_router, prefix="/api/v1", tags=["Count Events"])
app.include_router(
    empty_collection_router(["/api/v1/rfid-tags"]),
    tags=["Compatibility"],
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}
