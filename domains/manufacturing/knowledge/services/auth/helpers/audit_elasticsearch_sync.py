from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.compliance import AUDIT_COLLECTION, stable_json
from services.common.config import settings

logger = logging.getLogger("auth.audit_elasticsearch_sync")

SYNC_STATE_COLLECTION = "part11_audit_search_sync_state"
SYNC_STATE_ID = "elasticsearch"

_sync_task: asyncio.Task | None = None


def _raw_db(db: Any):
    return getattr(db, "_db", db)


def _jsonable(value: Any, *, strip_embeddings: bool = True) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, list):
        return [_jsonable(item, strip_embeddings=strip_embeddings) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if strip_embeddings and str(key) == "embedding":
                continue
            cleaned[str(key)] = _jsonable(item, strip_embeddings=strip_embeddings)
        return cleaned
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return stable_json(_jsonable(value))
    except Exception:
        return str(value)


def _actor_user_id(entry: dict[str, Any]) -> str | None:
    actor = entry.get("actor")
    if isinstance(actor, dict):
        return actor.get("user_id") or actor.get("sub")
    return entry.get("actor_user_id") or entry.get("user_id")


def _actor_email(entry: dict[str, Any]) -> str | None:
    actor = entry.get("actor")
    if isinstance(actor, dict):
        return actor.get("email")
    return entry.get("email")


def _change_control_id(entry: dict[str, Any]) -> str | None:
    collection = str(entry.get("collection") or "")
    record_id = entry.get("record_id")
    if collection == "gxp_change_controls" and record_id:
        return str(record_id)

    for source_key in ("after", "before", "update", "filter"):
        source = entry.get(source_key)
        if not isinstance(source, dict):
            continue
        for key in ("change_control_id", "change_control_number"):
            value = source.get(key)
            if value:
                return str(value)
        compliance = source.get("compliance")
        if isinstance(compliance, dict):
            change_controls = compliance.get("change_controls")
            if isinstance(change_controls, list) and change_controls:
                first = change_controls[0]
                if isinstance(first, dict):
                    value = first.get("change_control_id") or first.get(
                        "change_control_number"
                    )
                    if value:
                        return str(value)
    return None


def _search_text(entry: dict[str, Any]) -> str:
    fields = [
        entry.get("audit_id"),
        entry.get("collection"),
        entry.get("record_id"),
        entry.get("action"),
        entry.get("change_reason"),
        _actor_user_id(entry),
        _actor_email(entry),
        _change_control_id(entry),
        entry.get("before"),
        entry.get("after"),
        entry.get("update"),
        entry.get("result_summary"),
    ]
    return "\n".join(part for part in (_text(field) for field in fields) if part)


def _index_document(entry: dict[str, Any]) -> dict[str, Any]:
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp_value = timestamp.astimezone(timezone.utc).isoformat()
    else:
        timestamp_value = timestamp

    return {
        "audit_id": entry.get("audit_id") or str(entry.get("_id")),
        "mongo_id": str(entry.get("_id")),
        "timestamp": timestamp_value,
        "standard": entry.get("standard"),
        "standards": entry.get("standards") or [],
        "standard_ids": entry.get("standard_ids") or [],
        "controls": entry.get("controls") or [],
        "data_integrity_principles": entry.get("data_integrity_principles") or [],
        "collection": entry.get("collection"),
        "record_id": entry.get("record_id"),
        "action": entry.get("action"),
        "actor_user_id": _actor_user_id(entry),
        "actor_email": _actor_email(entry),
        "change_control_id": _change_control_id(entry),
        "change_reason": entry.get("change_reason"),
        "before_hash": entry.get("before_hash"),
        "after_hash": entry.get("after_hash"),
        "previous_hash": entry.get("previous_hash"),
        "entry_hash": entry.get("entry_hash"),
        "before": _jsonable(entry.get("before")),
        "after": _jsonable(entry.get("after")),
        "filter": _jsonable(entry.get("filter")),
        "update": _jsonable(entry.get("update")),
        "result_summary": _jsonable(entry.get("result_summary")),
        "search_text": _search_text(entry),
    }


def _client_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/x-ndjson"}
    if settings.AUDIT_ELASTICSEARCH_API_KEY:
        headers["Authorization"] = f"ApiKey {settings.AUDIT_ELASTICSEARCH_API_KEY}"
    return headers


def _client_auth():
    if settings.AUDIT_ELASTICSEARCH_USERNAME:
        return (
            settings.AUDIT_ELASTICSEARCH_USERNAME,
            settings.AUDIT_ELASTICSEARCH_PASSWORD,
        )
    return None


async def _ensure_index(client: httpx.AsyncClient) -> None:
    index_url = f"/{settings.AUDIT_ELASTICSEARCH_INDEX}"
    response = await client.head(index_url)
    if response.status_code == 200:
        return
    if response.status_code not in {404, 400}:
        response.raise_for_status()

    mapping = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "audit_id": {"type": "keyword"},
                "mongo_id": {"type": "keyword"},
                "timestamp": {"type": "date"},
                "standard": {"type": "keyword"},
                "collection": {"type": "keyword"},
                "record_id": {"type": "keyword"},
                "action": {"type": "keyword"},
                "actor_user_id": {"type": "keyword"},
                "actor_email": {"type": "keyword"},
                "change_control_id": {"type": "keyword"},
                "change_reason": {"type": "text"},
                "standard_ids": {"type": "keyword"},
                "standards": {"type": "keyword"},
                "controls": {"type": "keyword"},
                "data_integrity_principles": {"type": "keyword"},
                "before_hash": {"type": "keyword"},
                "after_hash": {"type": "keyword"},
                "previous_hash": {"type": "keyword"},
                "entry_hash": {"type": "keyword"},
                "before": {"type": "object", "enabled": False},
                "after": {"type": "object", "enabled": False},
                "filter": {"type": "object", "enabled": False},
                "update": {"type": "object", "enabled": False},
                "result_summary": {"type": "object", "enabled": False},
                "search_text": {"type": "text"},
            }
        },
    }
    response = await client.put(index_url, json=mapping)
    if response.status_code not in {200, 201}:
        response.raise_for_status()


async def _post_bulk(client: httpx.AsyncClient, lines: list[str]) -> int:
    if not lines:
        return 0
    response = await client.post(
        "/_bulk", content=("\n".join(lines) + "\n"), headers=_client_headers()
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        failed = [
            item
            for item in payload.get("items", [])
            if (item.get("index") or {}).get("error")
        ][:5]
        raise RuntimeError(f"Elasticsearch audit bulk index failed: {failed}")
    return len(lines) // 2


async def _bulk_index(client: httpx.AsyncClient, entries: list[dict[str, Any]]) -> int:
    lines: list[str] = []
    current_bytes = 0
    indexed = 0
    max_bytes = max(
        int(settings.AUDIT_ELASTICSEARCH_BULK_MAX_BYTES or 5 * 1024 * 1024), 1024 * 1024
    )

    for entry in entries:
        document = _index_document(entry)
        document_id = str(document["audit_id"])
        action_line = json.dumps(
            {
                "index": {
                    "_index": settings.AUDIT_ELASTICSEARCH_INDEX,
                    "_id": document_id,
                }
            },
            separators=(",", ":"),
        )
        document_line = json.dumps(document, default=str, separators=(",", ":"))
        pair_bytes = (
            len(action_line.encode("utf-8")) + len(document_line.encode("utf-8")) + 2
        )

        if lines and current_bytes + pair_bytes > max_bytes:
            indexed += await _post_bulk(client, lines)
            lines = []
            current_bytes = 0

        lines.extend([action_line, document_line])
        current_bytes += pair_bytes

    indexed += await _post_bulk(client, lines)
    return indexed


async def sync_part11_audit_to_elasticsearch(
    db: AsyncIOMotorDatabase, *, batch_size: int | None = None
) -> dict[str, Any]:
    if not settings.AUDIT_ELASTICSEARCH_ENABLED:
        return {"enabled": False, "indexed": 0}

    raw_db = _raw_db(db)
    state_collection = raw_db[SYNC_STATE_COLLECTION]
    state = await state_collection.find_one({"_id": SYNC_STATE_ID}) or {}
    last_object_id = state.get("last_object_id")
    query: dict[str, Any] = {}
    if last_object_id:
        try:
            query["_id"] = {"$gt": ObjectId(str(last_object_id))}
        except Exception:
            logger.warning(
                "Ignoring invalid audit Elasticsearch sync cursor: %s", last_object_id
            )

    limit = max(int(batch_size or settings.AUDIT_ELASTICSEARCH_BATCH_SIZE or 500), 1)
    indexed = 0
    last_entry: dict[str, Any] | None = None

    async with httpx.AsyncClient(
        base_url=settings.AUDIT_ELASTICSEARCH_URL.rstrip("/"),
        timeout=30.0,
        auth=_client_auth(),
    ) as client:
        await _ensure_index(client)
        while True:
            entries = (
                await raw_db[AUDIT_COLLECTION]
                .find(query)
                .sort("_id", 1)
                .limit(limit)
                .to_list(length=limit)
            )
            if not entries:
                break

            indexed += await _bulk_index(client, entries)
            last_entry = entries[-1]
            last_id = last_entry["_id"]
            await state_collection.update_one(
                {"_id": SYNC_STATE_ID},
                {
                    "$set": {
                        "last_object_id": str(last_id),
                        "last_audit_id": last_entry.get("audit_id"),
                        "last_timestamp": last_entry.get("timestamp"),
                        "updated_at": datetime.now(timezone.utc),
                        "index": settings.AUDIT_ELASTICSEARCH_INDEX,
                    },
                    "$inc": {"indexed_count": len(entries)},
                    "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                },
                upsert=True,
            )
            query = {"_id": {"$gt": last_id}}

    return {
        "enabled": True,
        "indexed": indexed,
        "last_audit_id": (
            last_entry.get("audit_id") if last_entry else state.get("last_audit_id")
        ),
        "last_object_id": (
            str(last_entry.get("_id")) if last_entry else state.get("last_object_id")
        ),
        "index": settings.AUDIT_ELASTICSEARCH_INDEX,
    }


async def _sync_loop(db: AsyncIOMotorDatabase) -> None:
    import pymongo.errors

    interval = max(int(settings.AUDIT_ELASTICSEARCH_SYNC_INTERVAL_SECONDS or 3600), 60)
    while True:
        try:
            result = await sync_part11_audit_to_elasticsearch(db)
            logger.info("Part 11 audit Elasticsearch sync completed: %s", result)
        except asyncio.CancelledError:
            raise
        except pymongo.errors.ServerSelectionTimeoutError:
            logger.warning(
                "Part 11 audit sync skipped — MongoDB not reachable (will retry in %ds)",
                interval,
            )
        except Exception:
            logger.exception("Part 11 audit Elasticsearch sync failed")
        await asyncio.sleep(interval)


async def start_audit_elasticsearch_sync(db: AsyncIOMotorDatabase) -> None:
    global _sync_task
    if not settings.AUDIT_ELASTICSEARCH_ENABLED:
        logger.info("Part 11 audit Elasticsearch sync is disabled")
        return
    if _sync_task and not _sync_task.done():
        return
    _sync_task = asyncio.create_task(
        _sync_loop(db), name="part11-audit-elasticsearch-sync"
    )
    logger.info(
        "Started Part 11 audit Elasticsearch sync job every %ss to %s/%s",
        settings.AUDIT_ELASTICSEARCH_SYNC_INTERVAL_SECONDS,
        settings.AUDIT_ELASTICSEARCH_URL,
        settings.AUDIT_ELASTICSEARCH_INDEX,
    )


async def stop_audit_elasticsearch_sync() -> None:
    global _sync_task
    if not _sync_task:
        return
    _sync_task.cancel()
    try:
        await _sync_task
    except asyncio.CancelledError:
        pass
    _sync_task = None
