"""Administrative API — boot, shutdown, reload, logs, configuration, version,
backup, restore.

POST /boot      — boot the runtime
POST /shutdown  — shutdown the runtime
POST /reload    — reload modules
GET  /logs      — recent logs
GET  /configuration — system configuration
GET  /version   — version info
POST /backup    — export every persisted monkeybrain:* key (Gate 6)
POST /restore   — write a previously-exported backup back to Redis
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.audit_decorator import audited
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    BootRequest,
    BootResponse,
    ShutdownResponse,
    ReloadResponse,
    LogsResponse,
    ConfigurationResponse,
    VersionResponse,
    BackupResponse,
    RestoreRequest,
    RestoreResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.admin")
router = APIRouter()


@router.post("/boot", response_model=BootResponse, tags=["Admin"])
@idempotent("admin.boot_runtime")
async def boot_runtime(
    body: BootRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> BootResponse:
    components = {}
    for name in (
        "cognitive_runtime",
        "simulation_runtime",
        "comparator_runtime",
        "planetary_runtime",
    ):
        rt = getattr(request.app.state, name, None)
        components[name] = "ready" if rt is not None else "offline"
    return BootResponse(status="already_booted", components=components)


@router.post("/shutdown", response_model=ShutdownResponse, tags=["Admin"])
@audited("admin.shutdown")
@idempotent("admin.shutdown_runtime")
async def shutdown_runtime(
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> ShutdownResponse:
    return ShutdownResponse(status="shutdown_requested")


@router.post("/reload", response_model=ReloadResponse, tags=["Admin"])
@idempotent("admin.reload_modules")
async def reload_modules(
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> ReloadResponse:
    return ReloadResponse(status="reloaded", modules_reloaded=0)


@router.get("/logs", response_model=LogsResponse, tags=["Admin"])
async def get_logs(
    request: Request,
    limit: int = 100,
    user_id: str = Depends(require_permission("perm-admin")),
) -> LogsResponse:
    lemon = getattr(request.app.state, "lemon", None)
    if not lemon:
        return LogsResponse()
    entries = lemon.logger.get_entries(limit=limit)
    logs = [e.to_dict() for e in entries]
    return LogsResponse(logs=logs, count=len(logs))


@router.get("/configuration", response_model=ConfigurationResponse, tags=["Admin"])
async def get_configuration(
    user_id: str = Depends(require_permission("perm-admin")),
) -> ConfigurationResponse:
    config = {
        "auth_required": os.getenv("AGENTOS_AUTH_REQUIRED", "true"),
        "rate_limit_rps": os.getenv("RATE_LIMIT_RPS", "100"),
        "rate_limit_burst": os.getenv("RATE_LIMIT_BURST", "200"),
        "cors_origins": os.getenv("CORS_ORIGINS", "http://localhost:3000"),
    }
    return ConfigurationResponse(config=config)


@router.get("/tuning", tags=["Admin"])
async def get_tuning(
    user_id: str = Depends(require_permission("perm-admin")),
) -> dict:
    from src.monkey_brain.kernel.pipeline.tuning import get_tuning

    return get_tuning().to_dict()


@router.post("/tuning", tags=["Admin"])
@idempotent("admin.update_tuning")
async def update_tuning(
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> dict:
    from src.monkey_brain.kernel.pipeline.tuning import get_tuning

    body = await request.json()
    tuning = get_tuning()
    tuning._from_dict(body)
    await tuning.save()
    return {"status": "updated", "config": tuning.to_dict()}


@router.get("/version", response_model=VersionResponse, tags=["Admin"])
async def get_version() -> VersionResponse:
    import tomllib
    from datetime import datetime, timezone

    pyproject = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "..",
        "..",
        "pyproject.toml",
    )
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version", "2.0.0")
    except Exception:
        version = "2.0.0"

    now = datetime.now(timezone.utc)
    date_code = now.strftime("%y%m%d")
    build = f"MS{date_code}.001"

    return VersionResponse(version=version, build=build, runtime="monkeybrain")


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


@router.post("/backup", response_model=BackupResponse, tags=["Admin"])
@audited("admin.backup")
@idempotent("admin.backup_world")
async def backup_world(
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> BackupResponse:
    from src.monkey_brain.kernel.society.world_backup import export_backup

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return BackupResponse(**export_backup(pr))


@router.post("/restore", response_model=RestoreResponse, tags=["Admin"])
@audited("admin.restore")
@idempotent("admin.restore_world")
async def restore_world(
    body: RestoreRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-admin")),
) -> RestoreResponse:
    from src.monkey_brain.kernel.society.world_backup import restore_backup

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return RestoreResponse(**restore_backup(pr, body.backup, overwrite=body.overwrite))
