"""Keystore API — user-scoped secure key management.

Each user can only access their own keys.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Any

from src.monkey_brain.kernel.execute.keystore import SecureKeystore
from src.monkey_brain.api.audit_decorator import audited
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

router = APIRouter(tags=["Keys"])

_keystore: SecureKeystore | None = None


def get_keystore() -> SecureKeystore:
    """The process keystore.

    Requires AGENTOS_MASTER_KEY. SecureKeystore used to silently mint an ephemeral master key
    when none was configured — which meant every key stored through this route became
    permanently undecryptable on the next restart. It now refuses to start without one, so a
    misconfiguration surfaces as a clear error instead of silent data loss.
    """
    global _keystore
    if _keystore is None:
        try:
            _keystore = SecureKeystore()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"keystore unavailable: {exc}",
            ) from exc
    return _keystore


class AddKeyRequest(BaseModel):
    service: str = Field(..., description="Service name")
    key_name: str = Field(..., description="Friendly name")
    api_key: str = Field(..., description="API key (encrypted on storage)")
    api_url: str = Field(default="", description="API endpoint URL")
    config: dict[str, Any] = Field(default_factory=dict)


class KeyResponse(BaseModel):
    key_id: str
    user_id: str
    service: str
    key_name: str
    api_url: str
    created_at: str
    is_active: bool


@router.post("/keys", response_model=KeyResponse)
@audited("keys.add")
@idempotent("keys.add_key")
async def add_key(body: AddKeyRequest, user_id: str = Depends(require_permission("perm-manage-keys"))):
    """Add a new API key (user-scoped, encrypted)."""
    keystore = get_keystore()
    result = keystore.add_key(
        user_id=user_id,
        service=body.service,
        key_name=body.key_name,
        api_key=body.api_key,
        api_url=body.api_url,
        config=body.config,
    )
    return KeyResponse(**result)


@router.get("/keys", response_model=list[KeyResponse])
async def list_keys(
    user_id: str = Depends(require_permission("perm-view-keys")),
    service: str | None = None,
):
    """List keys for current user only."""
    keystore = get_keystore()
    return keystore.list_keys(user_id=user_id, service=service)


@router.get("/keys/{key_id}", response_model=KeyResponse)
async def get_key(key_id: str, user_id: str = Depends(require_permission("perm-view-keys"))):
    """Get key details (user-scoped)."""
    keystore = get_keystore()
    keys = keystore.list_keys(user_id=user_id)
    for key in keys:
        if key["key_id"] == key_id:
            return key
    raise HTTPException(status_code=404, detail="Key not found")


@router.delete("/keys/{key_id}")
@audited("keys.delete")
@idempotent("keys.delete_key")
async def delete_key(key_id: str, user_id: str = Depends(require_permission("perm-manage-keys"))):
    """Remove a key (user-scoped)."""
    keystore = get_keystore()
    if keystore.remove_key(key_id, user_id):
        return {"status": "deleted", "key_id": key_id}
    raise HTTPException(status_code=404, detail="Key not found")


@router.get("/keys/config/{service}")
async def get_config(service: str, user_id: str = Depends(require_permission("perm-view-keys"))):
    """Get config for a service (user-scoped)."""
    keystore = get_keystore()
    config = keystore.get_config(service, user_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"No config for {service}")
    return config


@router.get("/keys/summary")
async def get_summary(user_id: str = Depends(require_permission("perm-view-keys"))):
    """Get keystore summary for current user."""
    keystore = get_keystore()
    return keystore.summary(user_id=user_id)
