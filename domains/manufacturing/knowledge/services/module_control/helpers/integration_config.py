from __future__ import annotations

import json
import secrets
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.auth.helpers import nats_store
from services.common.data_transformation import RAW_INTEGRATION_SUBJECT
from services.common.module_control import utc_now
from services.common.ontology_registry import list_integration_adapters

INTEGRATION_CONFIG_COLLECTION = "integration_connection_manifests"
INTEGRATION_CONFIG_AUDIT_COLLECTION = "integration_connection_manifest_audit"
ALLOWED_AUTH_MODES = {"oauth2", "basic", "api_key", "bearer", "none"}
SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "client_secret",
    "access_token",
    "refresh_token",
}


def _adapter_ids() -> set[str]:
    return {str(adapter["adapter_id"]) for adapter in list_integration_adapters()}


def parse_manifest_bytes(
    content: bytes, filename: str = "manifest.yaml"
) -> dict[str, Any]:
    text = content.decode("utf-8")
    if filename.lower().endswith(".json"):
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Integration manifest must be a YAML/JSON object.",
        )
    return data


def _normalize_auth(auth: Any) -> dict[str, Any]:
    if isinstance(auth, str):
        mode = auth.strip().lower()
        if mode not in ALLOWED_AUTH_MODES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported auth mode '{auth}'.",
            )
        return {"mode": mode}
    if isinstance(auth, dict):
        mode = str(auth.get("mode") or auth.get("type") or "").strip().lower()
        if mode not in ALLOWED_AUTH_MODES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported auth mode '{mode}'.",
            )
        return {**auth, "mode": mode}
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="auth must be a string mode or object with mode/type.",
    )


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


def redact_manifest(document: dict[str, Any]) -> dict[str, Any]:
    clean = dict(document)
    clean.pop("_id", None)
    if "auth" in clean:
        clean["auth"] = _redact(clean["auth"])
    return clean


def _webhook_path_and_source(webhook_key: str, value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        path = str(value.get("path") or value.get("url") or "").strip()
        source_object = str(value.get("source_object") or webhook_key).strip()
    else:
        path = str(value or "").strip()
        source_object = webhook_key
    if not path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Webhook '{webhook_key}' requires a path.",
        )
    return path, source_object


def validate_integration_config_manifest(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    valid_adapters = _adapter_ids()
    normalized: dict[str, dict[str, Any]] = {}
    for adapter_id, config in manifest.items():
        adapter_id = str(adapter_id)
        if adapter_id not in valid_adapters:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown integration adapter '{adapter_id}'.",
            )
        if not isinstance(config, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Config for '{adapter_id}' must be an object.",
            )

        base_url = str(config.get("base_url") or "").strip()
        if not base_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Config for '{adapter_id}' requires base_url.",
            )
        webhooks = config.get("webhooks") or {}
        field_mappings = config.get("field_mappings") or {}
        if not isinstance(webhooks, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"webhooks for '{adapter_id}' must be an object.",
            )
        if not isinstance(field_mappings, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"field_mappings for '{adapter_id}' must be an object.",
            )
        normalized_webhooks = {}
        for webhook_key, value in webhooks.items():
            path, source_object = _webhook_path_and_source(str(webhook_key), value)
            normalized_webhooks[str(webhook_key)] = {
                "path": path,
                "source_object": source_object,
            }

        normalized[adapter_id] = {
            "adapter_id": adapter_id,
            "base_url": base_url.rstrip("/"),
            "auth": _normalize_auth(config.get("auth") or "none"),
            "webhooks": normalized_webhooks,
            "field_mappings": {
                str(key): value for key, value in field_mappings.items()
            },
        }
    return normalized


async def upsert_integration_config_manifest(
    db: AsyncIOMotorDatabase,
    manifest: dict[str, Any],
    *,
    actor: str,
    source: str = "api",
) -> dict[str, Any]:
    normalized = validate_integration_config_manifest(manifest)
    now = utc_now()
    updated = []
    for adapter_id, config in normalized.items():
        document = {
            **config,
            "config_id": adapter_id,
            "source": source,
            "updated_at": now,
            "updated_by": actor,
        }
        existing = await db[INTEGRATION_CONFIG_COLLECTION].find_one(
            {"config_id": adapter_id}
        )
        document["created_at"] = existing.get("created_at") if existing else now
        await db[INTEGRATION_CONFIG_COLLECTION].replace_one(
            {"config_id": adapter_id}, document, upsert=True
        )
        await db[INTEGRATION_CONFIG_AUDIT_COLLECTION].insert_one(
            {
                "event_type": "integration-config-upserted",
                "adapter_id": adapter_id,
                "actor": actor,
                "source": source,
                "created_at": now,
            }
        )
        updated.append(redact_manifest(document))
    return {"updated_count": len(updated), "integrations": updated}


async def list_integration_configs(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[INTEGRATION_CONFIG_COLLECTION].find({})
    return [redact_manifest(document) async for document in cursor]


async def get_integration_config(
    db: AsyncIOMotorDatabase, adapter_id: str
) -> dict[str, Any] | None:
    document = await db[INTEGRATION_CONFIG_COLLECTION].find_one(
        {"config_id": adapter_id}
    )
    return redact_manifest(document) if document else None


def _configured_secret(config: dict[str, Any]) -> str:
    auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    for key in ("webhook_secret", "api_key", "secret", "token"):
        value = auth.get(key)
        if value:
            return str(value)
    return ""


def _provided_secret(request: Request) -> str:
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return (
        request.headers.get("x-sentinel-integration-secret")
        or request.headers.get("x-api-key")
        or request.query_params.get("secret")
        or ""
    )


def _enrich_payload_with_field_mappings(
    payload: dict[str, Any], field_mappings: dict[str, Any]
) -> dict[str, Any]:
    enriched = dict(payload)
    for key, value in field_mappings.items():
        if str(key).endswith("_field"):
            continue
        enriched.setdefault(str(key), value)
    return enriched


async def publish_inbound_integration_webhook(
    db: AsyncIOMotorDatabase,
    *,
    adapter_id: str,
    webhook_key: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    config = await db[INTEGRATION_CONFIG_COLLECTION].find_one({"config_id": adapter_id})
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration config '{adapter_id}' not found.",
        )
    webhooks = (
        config.get("webhooks") if isinstance(config.get("webhooks"), dict) else {}
    )
    webhook = webhooks.get(webhook_key)
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook '{webhook_key}' is not registered for '{adapter_id}'.",
        )

    configured_secret = _configured_secret(config)
    if configured_secret and not secrets.compare_digest(
        configured_secret, _provided_secret(request)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid integration webhook secret.",
        )

    source_object = str(webhook.get("source_object") or webhook_key)
    enriched_payload = _enrich_payload_with_field_mappings(
        payload, config.get("field_mappings") or {}
    )
    event = {
        "event_id": f"integration-webhook-{uuid4().hex[:12]}",
        "event_type": "integration-raw",
        "source": "integration-webhook",
        "adapter_id": adapter_id,
        "source_object": source_object,
        "webhook_key": webhook_key,
        "payload": enriched_payload,
        "field_mappings": config.get("field_mappings") or {},
        "received_at": utc_now(),
    }
    await nats_store.publish_event(
        nats_store._event_bytes(event), subject=RAW_INTEGRATION_SUBJECT
    )
    await db[INTEGRATION_CONFIG_AUDIT_COLLECTION].insert_one(
        {
            "event_type": "integration-webhook-received",
            "adapter_id": adapter_id,
            "webhook_key": webhook_key,
            "source_object": source_object,
            "event_id": event["event_id"],
            "created_at": utc_now(),
        }
    )
    return {
        "queued": True,
        "subject": RAW_INTEGRATION_SUBJECT,
        "event_id": event["event_id"],
        "adapter_id": adapter_id,
        "webhook_key": webhook_key,
        "source_object": source_object,
    }
