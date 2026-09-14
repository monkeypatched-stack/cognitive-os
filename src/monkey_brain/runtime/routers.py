"""Runtime routers."""

from fastapi import APIRouter, Request

router = APIRouter()


def get_mongo_client(request: Request):
    pm = getattr(request.app.state, "persistence_manager", None)
    if pm and hasattr(pm, "_adapters"):
        for adapter in pm._adapters.values():
            if hasattr(adapter, "_client"):
                return adapter._client
    return getattr(request.app.state, "mongo_client", None)
