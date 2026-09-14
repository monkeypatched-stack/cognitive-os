from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.config import settings
from services.common.db import get_database
from services.common.module_control import (
    evaluate_module_control,
    get_module_control_policy,
    save_module_control_policy,
)
from services.module_control.helpers.security import (
    create_activation_challenge,
    require_activation_session,
    validate_activation_session,
    verify_activation,
)
from services.module_control.helpers.integration_config import (
    get_integration_config,
    list_integration_configs,
    parse_manifest_bytes,
    publish_inbound_integration_webhook,
    upsert_integration_config_manifest,
)
from services.module_control.models.module_control import (
    ActivationChallengeResponse,
    ActivationVerifyRequest,
    ActivationVerifyResponse,
    EvaluationRequest,
    IntegrationManifestRequest,
    ModuleControlPolicyResponse,
    ModuleStateRequest,
    PolicyPatchRequest,
)

router = APIRouter()


async def active_module_control_session(
    token: str = Depends(require_activation_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await validate_activation_session(db, token)


def _actor(session: dict[str, Any] | None) -> str:
    if not session:
        return "module-control"
    return str(session.get("actor") or "module-control")


@router.get("/status")
async def status(db: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    policy = await get_module_control_policy(db)
    return {
        "status": "healthy",
        "policy_version": policy["version"],
        "modules_configured": len(policy.get("modules") or {}),
        "integrations_configured": len(policy.get("integrations") or {}),
        "activation_required": True,
    }


@router.post("/activation/challenge", response_model=ActivationChallengeResponse)
async def activation_challenge(
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await create_activation_challenge(db)


@router.post("/activation/verify", response_model=ActivationVerifyResponse)
async def activation_verify(
    request: ActivationVerifyRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await verify_activation(
        db,
        challenge_id=request.challenge_id,
        otp_code=request.otp_code,
        password=request.password,
    )


@router.get("/policy", response_model=ModuleControlPolicyResponse)
async def read_policy(
    _: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await get_module_control_policy(db)


@router.patch("/policy", response_model=ModuleControlPolicyResponse)
async def patch_policy(
    request: PolicyPatchRequest,
    session: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    patch = request.model_dump(exclude={"reason"})
    patch["reason"] = request.reason
    return await save_module_control_policy(db, patch, actor=_actor(session))


@router.patch("/modules/{module_id}", response_model=ModuleControlPolicyResponse)
async def set_module_state(
    module_id: str,
    request: ModuleStateRequest,
    session: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await save_module_control_policy(
        db,
        {"modules": {module_id: request.enabled}, "reason": request.reason},
        actor=_actor(session),
    )


@router.patch(
    "/integrations/{integration_id}", response_model=ModuleControlPolicyResponse
)
async def set_integration_state(
    integration_id: str,
    request: ModuleStateRequest,
    session: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await save_module_control_policy(
        db,
        {"integrations": {integration_id: request.enabled}, "reason": request.reason},
        actor=_actor(session),
    )


@router.get("/integration-config")
async def read_integration_configs(
    _: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> list[dict[str, Any]]:
    return await list_integration_configs(db)


@router.get("/integration-config/{adapter_id}")
async def read_integration_config(
    adapter_id: str,
    _: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    document = await get_integration_config(db, adapter_id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration config '{adapter_id}' not found.",
        )
    return document


@router.post("/integration-config")
async def upsert_integration_config(
    request: IntegrationManifestRequest,
    session: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    return await upsert_integration_config_manifest(
        db,
        request.manifest,
        actor=_actor(session),
        source="json-api",
    )


@router.post("/integration-config/upload")
async def upload_integration_config(
    file: UploadFile = File(...),
    session: dict[str, Any] = Depends(active_module_control_session),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    manifest = parse_manifest_bytes(
        await file.read(), file.filename or "integration-config.yaml"
    )
    return await upsert_integration_config_manifest(
        db,
        manifest,
        actor=_actor(session),
        source=file.filename or "upload",
    )


@router.post("/integration-webhooks/{adapter_id}/{webhook_key}")
async def receive_integration_webhook(
    adapter_id: str,
    webhook_key: str,
    request: Request,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook payload must be a JSON object.",
        )
    return await publish_inbound_integration_webhook(
        db,
        adapter_id=adapter_id,
        webhook_key=webhook_key,
        payload=payload,
        request=request,
    )


@router.post("/evaluate")
async def evaluate(
    request: EvaluationRequest,
    x_module_control_internal_secret: str = Header(
        default="", alias="X-Module-Control-Internal-Secret"
    ),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    if (
        settings.MODULE_CONTROL_INTERNAL_SECRET
        and x_module_control_internal_secret != settings.MODULE_CONTROL_INTERNAL_SECRET
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid module-control internal secret.",
        )
    return await evaluate_module_control(
        db,
        module_id=request.module_id,
        path=request.path,
        integration_id=request.integration_id,
        limit_key=request.limit_key,
        collection=request.collection,
    )
