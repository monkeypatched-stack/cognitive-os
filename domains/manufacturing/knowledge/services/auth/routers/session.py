import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from services.auth.helpers import graph_store
from services.auth.helpers import store
from services.auth.models.session import (
    SessionBulkGetRequest,
    SessionBulkResponse,
    SessionBulkSaveRequest,
    SessionValueRequest,
    SessionValueResponse,
)
from services.common.auth import get_current_user

router = APIRouter()

ALLOWED_KEYS = {
    "indus_access_token",
    "indus_refresh_token",
    "indus_user",
    "permitted_pages",
    "sidebar_collapsed_sections",
    "factory-layout-symbol-canvas",
    "factory-layout-symbol-canvas:seed-version",
    "document-management-cad-canvas",
    "pid-symbol-canvas-nodes",
    "indus-dashboard-materials",
    "indus-dashboard-orders",
    "indus-dashboard-document-metadata",
    "indus-dashboard-document-workflows",
    "indus-dashboard-calibrations",
    "indus-dashboard-cleaning",
    "indus-dashboard-maintenance-logs",
    "indus-dashboard-downtime",
    "indus-dashboard-inventory-allocations",
    "operations-timesheet-v2",
    "indus.customerMetadata",
    "indus.customerDetails",
    "indus.shippingProviderDetails",
    "indus.integrationConfigs",
    "indus.integrationActivation",
    "indus-dashboard-process-definition-steps",
    "indus-dashboard-process-definition-prechecks",
    "indus-dashboard-process-definition-postchecks",
    "indus-dashboard-process-definition-constraints",
    "indus-dashboard-process-prechecks",
    "indus-dashboard-process-postchecks",
    "indus-dashboard-process-constraints",
    "indus-dashboard-corrective-actions",
    "indus-dashboard-process-steps-canvas-positions",
    "indus-dashboard-process-steps-canvas-links",
    "indus-dashboard-process-steps-canvas-visual-nodes",
    "indus-dashboard-process-steps-canvas-imported-nodes",
    "indus-dashboard-process-steps-canvas-snapshot",
    "indus-dashboard-process-steps-factory-canvas-positions",
    "indus-dashboard-process-steps-factory-canvas-links",
    "indus-dashboard-process-steps-factory-canvas-visual-nodes",
    "indus-dashboard-process-steps-factory-canvas-imported-nodes",
    "indus-dashboard-process-steps-factory-canvas-snapshot",
    "supply-chain-graph-tabs",
    "supply-chain-graph-rows",
    "supply-chain-graph-canvas",
    "supply-chain-graph-canvas-positions",
    "supply-chain-graph-canvas-links",
    "supply-chain-graph-canvas-visual-nodes",
}

_KEY_PART = r"[A-Za-z0-9_.:-]+"
ALLOWED_KEY_PATTERNS = [
    re.compile(rf"^indus-dashboard-{_KEY_PART}$"),
    re.compile(rf"^indus-dashboard-{_KEY_PART}-{_KEY_PART}$"),
    re.compile(rf"^{_KEY_PART}-canvas-positions$"),
    re.compile(rf"^{_KEY_PART}-canvas-links$"),
    re.compile(rf"^{_KEY_PART}-canvas-visual-nodes$"),
    re.compile(rf"^{_KEY_PART}-canvas-imported-nodes$"),
    re.compile(rf"^{_KEY_PART}-canvas-snapshot$"),
    re.compile(rf"^supply-chain-graph-rows-{_KEY_PART}$"),
    re.compile(rf"^supply-chain-graph-{_KEY_PART}-canvas-positions$"),
    re.compile(rf"^supply-chain-graph-{_KEY_PART}-canvas-links$"),
    re.compile(rf"^supply-chain-graph-{_KEY_PART}-canvas-visual-nodes$"),
    re.compile(rf"^supply-chain-graph-{_KEY_PART}-canvas-imported-nodes$"),
    re.compile(rf"^supply-chain-graph-{_KEY_PART}-canvas-snapshot$"),
    re.compile(
        rf"^indus-dashboard-process-definition-steps(?:-[A-Za-z0-9_.:-]+)*-canvas-(positions|links|visual-nodes|imported-nodes|snapshot)$"
    ),
    re.compile(rf"^indus-dashboard-{_KEY_PART}s$"),
]


def _user_id(current_user: dict) -> str:
    user_id = current_user.get("sub") or current_user.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user id is missing",
        )
    return str(user_id)


def _validate_key(key: str) -> str:
    if not key or len(key) > 256:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session key must be between 1 and 256 characters",
        )

    if key in ALLOWED_KEYS or any(
        pattern.match(key) for pattern in ALLOWED_KEY_PATTERNS
    ):
        return key

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Session key '{key}' is not allowed",
    )


def _serialize(value: Any) -> str:
    try:
        return json.dumps(value)
    except TypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Session value is not JSON serializable: {exc}",
        ) from exc


def _deserialize(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


async def _get_graph_or_redis(user_id: str, key: str) -> str | None:
    if not graph_store.is_graph_key(key):
        return await store.get_session_value(user_id, key)
    try:
        value = await graph_store.get_graph_state(user_id, key)
    except HTTPException:
        value = None
    return value if value is not None else await store.get_session_value(user_id, key)


async def _save_graph_and_redis(user_id: str, key: str, value: Any) -> None:
    serialized = _serialize(value)
    await store.save_session_value(user_id, key, serialized)
    if graph_store.is_graph_key(key):
        await graph_store.save_graph_state(user_id, key, value)


async def _delete_graph_and_redis(user_id: str, key: str) -> None:
    await store.delete_session_value(user_id, key)
    if graph_store.is_graph_key(key):
        try:
            await graph_store.delete_graph_state(user_id, key)
        except HTTPException:
            return


@router.post("/bulk/get", response_model=SessionBulkResponse)
async def get_session_values(
    body: SessionBulkGetRequest,
    current_user: dict = Depends(get_current_user),
):
    keys = [_validate_key(key) for key in body.keys]
    user_id = _user_id(current_user)
    redis_keys = [key for key in keys if not graph_store.is_graph_key(key)]
    graph_keys = [key for key in keys if graph_store.is_graph_key(key)]
    raw_items = await store.get_session_values(user_id, redis_keys)
    for key in graph_keys:
        raw_items[key] = await _get_graph_or_redis(user_id, key)
    items = {key: _deserialize(raw_items.get(key)) for key in keys}
    missing = [key for key in keys if raw_items.get(key) is None]
    return SessionBulkResponse(items=items, missing=missing)


@router.put("/bulk", response_model=SessionBulkResponse)
async def save_session_values(
    body: SessionBulkSaveRequest,
    current_user: dict = Depends(get_current_user),
):
    items = {_validate_key(key): value for key, value in body.items.items()}
    user_id = _user_id(current_user)
    redis_items = {key: _serialize(value) for key, value in items.items()}
    await store.save_session_values(user_id, redis_items)
    for key, value in items.items():
        if graph_store.is_graph_key(key):
            await graph_store.save_graph_state(user_id, key, value)
    return SessionBulkResponse(items=items)


@router.get("/{key}", response_model=SessionValueResponse)
async def get_session_value(
    key: str,
    current_user: dict = Depends(get_current_user),
):
    valid_key = _validate_key(key)
    user_id = _user_id(current_user)
    raw_value = await _get_graph_or_redis(user_id, valid_key)
    if raw_value is None:
        return SessionValueResponse(key=valid_key, value=None, found=False)
    return SessionValueResponse(key=valid_key, value=_deserialize(raw_value))


@router.put("/{key}", response_model=SessionValueResponse)
async def save_session_value(
    key: str,
    body: SessionValueRequest,
    current_user: dict = Depends(get_current_user),
):
    valid_key = _validate_key(key)
    user_id = _user_id(current_user)
    await _save_graph_and_redis(user_id, valid_key, body.value)
    return SessionValueResponse(key=valid_key, value=body.value)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_value(
    key: str,
    current_user: dict = Depends(get_current_user),
):
    valid_key = _validate_key(key)
    user_id = _user_id(current_user)
    await _delete_graph_and_redis(user_id, valid_key)
