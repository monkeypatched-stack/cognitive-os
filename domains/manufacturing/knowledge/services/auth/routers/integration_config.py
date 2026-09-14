from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from typing import Any

import httpx
import yaml
from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field

from services.common.auth import get_current_user
from services.common.config import settings
from services.common.db import get_database
from services.common.integration_ingestion import publish_external_ingestion_records
from services.common.models.databricks import DatabricksConfig, QueryRequest
from services.common.module_control import save_module_control_policy, utc_now
from services.common.ontology_registry import list_integration_adapters
from services.module_control.helpers.integration_config import (
    get_integration_config,
    list_integration_configs,
    upsert_integration_config_manifest,
)

router = APIRouter()

INTEGRATION_CONFIG_COLLECTION = "integration_configs"
INTEGRATION_CONFIG_AUDIT_EVENT_TYPE = "integration-config-upserted"
SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "client_secret",
    "access_token",
    "refresh_token",
}

FRONTEND_TO_BACKEND_MODULES = {
    "internet-of-things": "iot",
    "changeover-management": "changeover_management",
    "event-management": "workstation_status",
    "inventory-management": "wms",
    "order-management": "order_management",
    "procurement-management": "supply_chain",
    "product-management": "product_management",
    "shipping-management": "supply_chain",
    "shift-management": "shift_management",
    "supplier-management": "suppliers",
    "supply-chain-graph": "supply_chain",
}

KIND_TO_BACKEND_MODULES = {
    "databricks": ["supply_chain", "wms", "workstation_status", "product_management"],
    "delta": ["supply_chain", "wms", "workstation_status", "product_management"],
    "delta_lake": ["supply_chain", "wms", "workstation_status", "product_management"],
    "lakehouse": ["supply_chain", "wms", "workstation_status", "product_management"],
    "iot": ["iot"],
    "iot_gateway": ["iot"],
    "changeover": ["changeover_management"],
    "changeover_management": ["changeover_management"],
    "event": ["workstation_status"],
    "events": ["workstation_status"],
    "workstation_status": ["workstation_status"],
    "inventory": ["wms"],
    "warehouse": ["wms"],
    "warehousing": ["wms"],
    "wms": ["wms"],
    "order": ["order_management"],
    "orders": ["order_management"],
    "erp": ["order_management", "product_management", "suppliers", "supply_chain"],
    "mes": ["workstation_status", "product_management"],
    "procurement": ["supply_chain"],
    "product": ["product_management"],
    "products": ["product_management"],
    "shipping": ["supply_chain"],
    "shift": ["shift_management"],
    "shifts": ["shift_management"],
    "supplier": ["suppliers"],
    "suppliers": ["suppliers"],
    "supply_chain": ["supply_chain"],
}

KIND_TO_INTEGRATION_ID = {
    "databricks": "databricks",
    "delta": "databricks",
    "delta_lake": "databricks",
    "lakehouse": "databricks",
    "erp": "sap_erp",
    "mes": "generic_lims",
    "lims": "generic_lims",
    "wms": "wms",
    "warehouse": "wms",
    "warehousing": "wms",
    "document_management": "document_management",
    "veeva_quality": "veeva_quality",
    "cmms": "cmms",
    "maintenance": "cmms",
}


class IntegrationConfigDeclaration(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    config_id: str | None = None
    name: str | None = None
    kind: str | None = None
    enabled: bool = True
    config: dict[str, Any] | str | None = Field(default_factory=dict)
    activation: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None


class ExternalIngestionRequest(BaseModel):
    adapter_id: str = Field(..., min_length=1, max_length=128)
    source_object: str = Field(..., min_length=1, max_length=255)
    records: list[dict[str, Any]] | dict[str, Any]
    source: dict[str, Any] = Field(default_factory=dict)


def _adapter_ids() -> set[str]:
    return {str(adapter["adapter_id"]) for adapter in list_integration_adapters()}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "integration-config"


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _text(value: Any, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "***redacted***" if str(key).lower() in SECRET_KEYS else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _serialize(document: dict[str, Any]) -> dict[str, Any]:
    clean = dict(document)
    clean.pop("_id", None)
    return _redact(clean)


def _audit_elasticsearch_auth() -> tuple[str, str] | None:
    if settings.AUDIT_ELASTICSEARCH_USERNAME:
        return (
            settings.AUDIT_ELASTICSEARCH_USERNAME,
            settings.AUDIT_ELASTICSEARCH_PASSWORD,
        )
    return None


def _audit_elasticsearch_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.AUDIT_ELASTICSEARCH_API_KEY:
        headers["Authorization"] = f"ApiKey {settings.AUDIT_ELASTICSEARCH_API_KEY}"
    return headers


async def _index_integration_config_audit(event: dict[str, Any]) -> dict[str, Any]:
    if not settings.AUDIT_ELASTICSEARCH_ENABLED:
        return {
            "enabled": False,
            "indexed": False,
            "index": settings.AUDIT_ELASTICSEARCH_INDEX,
        }
    document = jsonable_encoder(
        {
            **event,
            "search_text": " ".join(
                str(value) for value in event.values() if value is not None
            ),
        }
    )
    try:
        async with httpx.AsyncClient(
            base_url=settings.AUDIT_ELASTICSEARCH_URL.rstrip("/"),
            timeout=5.0,
            auth=_audit_elasticsearch_auth(),
            headers=_audit_elasticsearch_headers(),
        ) as client:
            response = await client.post(
                f"/{settings.AUDIT_ELASTICSEARCH_INDEX}/_doc", json=document
            )
            response.raise_for_status()
            payload = response.json()
        return {
            "enabled": True,
            "indexed": True,
            "index": settings.AUDIT_ELASTICSEARCH_INDEX,
            "document_id": payload.get("_id"),
        }
    except httpx.HTTPError as exc:
        return {
            "enabled": True,
            "indexed": False,
            "index": settings.AUDIT_ELASTICSEARCH_INDEX,
            "error": str(exc),
        }


def _extract_frontend_module_ids(activation: dict[str, Any] | None) -> list[str]:
    if not isinstance(activation, dict):
        return []
    frontend = (
        activation.get("frontend")
        if isinstance(activation.get("frontend"), dict)
        else {}
    )
    ids = frontend.get("module_ids") or activation.get("module_ids") or []
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if str(item) in FRONTEND_TO_BACKEND_MODULES]


def _candidate_terms(declaration: IntegrationConfigDeclaration) -> list[str]:
    terms = [
        declaration.kind,
        getattr(declaration, "type", None),
        getattr(declaration, "provider", None),
    ]
    config = declaration.config if isinstance(declaration.config, dict) else {}
    metadata = declaration.metadata if isinstance(declaration.metadata, dict) else {}
    for source in (config, metadata):
        for key in (
            "kind",
            "type",
            "provider",
            "domain",
            "domains",
            "topics",
            "modules",
            "capabilities",
            "features",
        ):
            value = source.get(key)
            if isinstance(value, list):
                terms.extend(str(item) for item in value)
            else:
                terms.append(value)
    return [
        str(term).strip().lower().replace("-", "_").replace(" ", "_")
        for term in terms
        if str(term or "").strip()
    ]


def _backend_modules_for(declaration: IntegrationConfigDeclaration) -> list[str]:
    modules = {
        FRONTEND_TO_BACKEND_MODULES[module_id]
        for module_id in _extract_frontend_module_ids(declaration.activation)
    }
    for term in _candidate_terms(declaration):
        modules.update(KIND_TO_BACKEND_MODULES.get(term, []))
    return sorted(modules)


def _integration_ids_for(declaration: IntegrationConfigDeclaration) -> list[str]:
    adapters = _adapter_ids()
    integration_ids: set[str] = set()
    for term in _candidate_terms(declaration):
        if term in adapters:
            integration_ids.add(term)
        mapped = KIND_TO_INTEGRATION_ID.get(term)
        if mapped:
            integration_ids.add(mapped)
    config = declaration.config if isinstance(declaration.config, dict) else {}
    provider = _text(config.get("provider")).lower()
    if provider in adapters:
        integration_ids.add(provider)
    return sorted(integration_ids)


def _looks_like_manifest(value: Any) -> bool:
    adapters = _adapter_ids()
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            str(key) in adapters and isinstance(item, dict)
            for key, item in value.items()
        )
    )


def _manifest_from_declaration(
    declaration: IntegrationConfigDeclaration,
) -> dict[str, Any] | None:
    if _looks_like_manifest(declaration.manifest):
        return declaration.manifest
    if _looks_like_manifest(declaration.config):
        return declaration.config  # type: ignore[return-value]
    if _looks_like_manifest(declaration.model_extra):
        return declaration.model_extra

    config = declaration.config if isinstance(declaration.config, dict) else {}
    integration_ids = _integration_ids_for(declaration)
    base_url = _text(config.get("base_url"))
    if not base_url or not integration_ids:
        return None
    return {
        integration_id: {
            "base_url": base_url,
            "auth": config.get("auth") or "none",
            "webhooks": config.get("webhooks") or {},
            "field_mappings": config.get("field_mappings") or {},
        }
        for integration_id in integration_ids
    }


def _config_id_for(declaration: IntegrationConfigDeclaration) -> str:
    return _text(
        declaration.id,
        _text(
            declaration.config_id,
            _slug(
                f"{declaration.kind or 'custom'}-{declaration.name or 'integration'}"
            ),
        ),
    )


async def _apply_backend_activation(
    db: AsyncIOMotorDatabase,
    declaration: IntegrationConfigDeclaration,
    *,
    actor: str,
) -> dict[str, Any]:
    backend_modules = _backend_modules_for(declaration)
    integration_ids = _integration_ids_for(declaration)
    enabled = bool(declaration.enabled)
    patch: dict[str, Any] = {
        "reason": "Integration config activation applies after next sign-in.",
    }
    if backend_modules:
        patch["modules"] = {module_id: not enabled for module_id in backend_modules}
    if integration_ids:
        patch["integrations"] = {
            integration_id: enabled for integration_id in integration_ids
        }
    if len(patch) == 1:
        return {
            "updated": False,
            "modules": backend_modules,
            "integrations": integration_ids,
        }
    policy = await save_module_control_policy(db, patch, actor=actor)
    return {
        "updated": True,
        "policy_version": policy.get("version"),
        "modules": backend_modules,
        "integrations": integration_ids,
    }


async def _upsert_declaration(
    db: AsyncIOMotorDatabase,
    declaration: IntegrationConfigDeclaration,
    *,
    actor: str,
    source: str,
) -> dict[str, Any]:
    now = utc_now()
    config_id = _config_id_for(declaration)
    existing = await db[INTEGRATION_CONFIG_COLLECTION].find_one(
        {"config_id": config_id}
    )
    document = {
        "id": config_id,
        "config_id": config_id,
        "name": declaration.name or config_id,
        "kind": declaration.kind or "custom",
        "enabled": bool(declaration.enabled),
        "status": "active" if declaration.enabled else "disabled",
        "source": source,
        "config": declaration.config if declaration.config is not None else {},
        "activation": declaration.activation or {},
        "metadata": declaration.metadata or {},
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "updated_by": actor,
        "applies_after": "next_sign_in",
    }
    backend_activation = await _apply_backend_activation(db, declaration, actor=actor)
    document["backend_activation"] = backend_activation
    await db[INTEGRATION_CONFIG_COLLECTION].replace_one(
        {"config_id": config_id}, document, upsert=True
    )
    audit_event = {
        "event_type": INTEGRATION_CONFIG_AUDIT_EVENT_TYPE,
        "config_id": config_id,
        "actor": actor,
        "source": source,
        "enabled": bool(declaration.enabled),
        "backend_activation": backend_activation,
        "created_at": now,
    }
    document["audit"] = await _index_integration_config_audit(audit_event)

    manifest = _manifest_from_declaration(declaration)
    if manifest:
        await upsert_integration_config_manifest(
            db, manifest, actor=actor, source=source
        )

    return _serialize(document)


async def _actor(current_user: dict[str, Any]) -> str:
    return str(
        current_user.get("email") or current_user.get("sub") or "integration-config"
    )


def _source_with_defaults(
    source: dict[str, Any] | None, **defaults: Any
) -> dict[str, Any]:
    merged = {key: value for key, value in defaults.items() if value not in (None, "")}
    merged.update(source or {})
    return merged


def _databricks_config_with_defaults(
    *,
    host: str = "",
    token: str = "",
    cluster_id: str = "",
    catalog: str = "",
    schema: str = "",
) -> DatabricksConfig:
    return DatabricksConfig(
        host=host or settings.DATABRICKS_HOST,
        token=token or settings.DATABRICKS_TOKEN,
        cluster_id=cluster_id or settings.DATABRICKS_CLUSTER_ID,
        catalog=catalog or settings.DATABRICKS_CATALOG,
        schema_name=schema or settings.DATABRICKS_SCHEMA,
    )


def _parse_batch_upload(
    raw: bytes, filename: str | None
) -> list[dict[str, Any]] | dict[str, Any]:
    name = str(filename or "").lower()
    text = raw.decode("utf-8")
    try:
        if name.endswith(".csv"):
            return [dict(row) for row in csv.DictReader(io.StringIO(text))]
        if name.endswith(".ndjson") or name.endswith(".jsonl"):
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        if name.endswith(".yaml") or name.endswith(".yml"):
            parsed = yaml.safe_load(text)
        else:
            parsed = json.loads(text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid batch ingestion file: {exc}",
        ) from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("records"), list):
        return parsed["records"]
    if isinstance(parsed, (dict, list)):
        return parsed
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Batch ingestion file must contain a JSON/YAML object, array, CSV, or NDJSON records.",
    )


# publishes the event to nats and returns the same payload for now,
# eventually needs telegraph integration for data transform
# and must the create the nodes in the graph
async def _publish_ingestion_request(
    db: AsyncIOMotorDatabase,
    request: ExternalIngestionRequest,
    *,
    ingestion_mode: str,
    source: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    return await publish_external_ingestion_records(
        db,
        adapter_id=request.adapter_id,
        source_object=request.source_object,
        records=request.records,
        ingestion_mode=ingestion_mode,  # type: ignore[arg-type]
        source=source,
        requested_by=actor,
    )


# ─── Non-Databricks routes ────────────────────────────────────────────────────


@router.get("")
async def read_integration_configs(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    cursor = db[INTEGRATION_CONFIG_COLLECTION].find({})
    declarations = [_serialize(document) async for document in cursor]
    module_manifests = await list_integration_configs(db)
    return {
        "results": declarations,
        "items": declarations,
        "configs": declarations,
        "module_control_manifests": module_manifests,
        "read_by": await _actor(current_user),
    }


@router.get("/{config_id}")
async def read_integration_config(
    config_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    document = await db[INTEGRATION_CONFIG_COLLECTION].find_one(
        {"config_id": config_id}
    )
    if document:
        clean = _serialize(document)
        clean["read_by"] = await _actor(current_user)
        return clean
    manifest = await get_integration_config(db, config_id)
    if manifest:
        return manifest
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Integration config '{config_id}' not found.",
    )


@router.post("/ingest/spark-mini-batch")
async def ingest_spark_mini_batch(
    request: ExternalIngestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="spark_mini_batch",
        source=_source_with_defaults(
            request.source, engine="spark", cadence="mini_batch"
        ),
        actor=await _actor(current_user),
    )


@router.post("/ingest/kafka-realtime")
async def ingest_kafka_realtime(
    request: ExternalIngestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="kafka_realtime",
        source=_source_with_defaults(
            request.source, engine="kafka", cadence="real_time"
        ),
        actor=await _actor(current_user),
    )


@router.post("/ingest/flink-realtime")
async def ingest_flink_realtime(
    request: ExternalIngestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="flink_realtime",
        source=_source_with_defaults(
            request.source, engine="flink", cadence="real_time"
        ),
        actor=await _actor(current_user),
    )


@router.post("/ingest/batch")
async def ingest_batch(
    request: ExternalIngestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="batch",
        source=_source_with_defaults(request.source, engine="batch", cadence="batch"),
        actor=await _actor(current_user),
    )


@router.post("/ingest/batch/upload")
async def upload_batch_ingestion(
    adapter_id: str = Form(...),
    source_object: str = Form(...),
    file: UploadFile = File(...),
    source: str | None = Form(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    records = _parse_batch_upload(await file.read(), file.filename)
    try:
        source_payload = json.loads(source) if source else {}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid source metadata JSON: {exc}",
        ) from exc
    if not isinstance(source_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source metadata must be a JSON object.",
        )
    request = ExternalIngestionRequest(
        adapter_id=adapter_id,
        source_object=source_object,
        records=records,
        source=_source_with_defaults(
            source_payload,
            engine="batch",
            cadence="batch",
            filename=file.filename,
            content_type=file.content_type,
        ),
    )
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="batch",
        source=request.source,
        actor=await _actor(current_user),
    )


# ─── Databricks config routes ─────────────────────────────────────────────────


@router.post("")
@router.post("/databricks/config")
async def upsert_integration_config(
    request: IntegrationConfigDeclaration,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _upsert_declaration(
        db, request, actor=await _actor(current_user), source="json-api"
    )


@router.post("/upload")
@router.post("/databricks/upload/config")
async def upload_integration_config(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    kind: str | None = Form(default=None),
    enabled: bool = Form(default=True),
    activation: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    raw = await file.read()
    try:
        parsed = (
            json.loads(raw.decode("utf-8"))
            if (file.filename or "").lower().endswith(".json")
            else yaml.safe_load(raw.decode("utf-8"))
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid YAML/JSON file: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded integration config must be a YAML/JSON object.",
        )

    parsed_activation = json.loads(activation) if activation else None
    parsed_metadata = json.loads(metadata) if metadata else {}
    declaration = IntegrationConfigDeclaration(
        name=name or file.filename or "Uploaded integration config",
        kind=kind or parsed.get("kind") or parsed.get("provider") or "custom",
        enabled=enabled,
        config=parsed,
        activation=parsed_activation,
        metadata={
            **(parsed_metadata if isinstance(parsed_metadata, dict) else {}),
            "filename": file.filename,
            "content_type": file.content_type,
        },
        manifest=parsed if _looks_like_manifest(parsed) else None,
    )
    return await _upsert_declaration(
        db,
        declaration,
        actor=await _actor(current_user),
        source=file.filename or "upload",
    )


# ─── Databricks routes ────────────────────────────────────────────────────────


class DatabricksJobTriggerRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    notebook_params: dict[str, Any] = Field(default_factory=dict)
    source_object: str | None = None


async def _databricks_client(cfg: DatabricksConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.host.rstrip("/"),
        headers={"Authorization": f"Bearer {cfg.token}"},
        timeout=30.0,
    )


@router.get("/databricks/health")
async def databricks_health() -> dict[str, Any]:
    configured = bool(settings.DATABRICKS_HOST and settings.DATABRICKS_TOKEN)
    if not configured:
        return {"status": "unconfigured", "configured": False}
    try:
        async with await _databricks_client(
            _databricks_config_with_defaults()
        ) as client:
            response = await client.get("/api/2.1/jobs/list", params={"limit": 1})
            response.raise_for_status()
        return {
            "status": "ok",
            "configured": True,
            "host": settings.DATABRICKS_HOST.rstrip("/"),
        }
    except httpx.HTTPError as exc:
        return {"status": "error", "configured": True, "error": str(exc)}


@router.post("/databricks/jobs/trigger")
async def trigger_databricks_job(
    request: DatabricksJobTriggerRequest,
    db_host: str | None = None,
    db_token: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    cfg = _databricks_config_with_defaults(
        host=db_host or "",
        token=db_token or "",
    )
    async with await _databricks_client(cfg) as client:
        response = await client.post(
            "/api/2.1/jobs/run-now",
            json={
                "job_id": request.job_id,
                "notebook_params": request.notebook_params,
            },
        )
        response.raise_for_status()
        run = response.json()

    return {
        "success": True,
        "run_id": run.get("run_id"),
        "job_id": request.job_id,
        "triggered_by": await _actor(current_user),
    }


@router.get("/databricks/jobs/{run_id}/status")
async def get_databricks_run_status(
    run_id: int,
    db_host: str | None = None,
    db_token: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    cfg = _databricks_config_with_defaults(host=db_host or "", token=db_token or "")
    async with await _databricks_client(cfg) as client:
        response = await client.get("/api/2.1/jobs/runs/get", params={"run_id": run_id})
        response.raise_for_status()
        run = response.json()

    state = run.get("state", {})
    return {
        "run_id": run_id,
        "life_cycle_state": state.get("life_cycle_state"),
        "result_state": state.get("result_state"),
        "state_message": state.get("state_message"),
        "queried_by": await _actor(current_user),
    }


@router.post("/databricks/ingest")
async def ingest_databricks(
    request: ExternalIngestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_database()
    return await _publish_ingestion_request(
        db,
        request,
        ingestion_mode="nats_jetstream",
        source=_source_with_defaults(
            request.source, engine="databricks", cadence="real_time"
        ),
        actor=await _actor(current_user),
    )
