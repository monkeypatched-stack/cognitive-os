from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from services.common.cdc import publish_cdc_event
from services.common.config import settings

_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_last_error: str | None = None
_last_event_at: str | None = None
_last_started_at: str | None = None
_events_published = 0


def cdc_watcher_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "running": _task is not None and not _task.done(),
        "database": settings.DB_NAME,
        "subject": settings.NATS_CDC_SUBJECT,
        "scope": "all_collections",
        "last_started_at": _last_started_at,
        "last_event_at": _last_event_at,
        "events_published": _events_published,
        "last_error": _last_error,
    }


def _record_id_from_document_key(document_key: Any) -> str | None:
    if isinstance(document_key, dict):
        value = document_key.get("_id")
        return str(value) if value is not None else None
    return None


async def publish_change_stream_event(change: dict[str, Any]) -> None:
    global _events_published, _last_event_at
    ns = change.get("ns") if isinstance(change.get("ns"), dict) else {}
    collection = ns.get("coll")
    operation = str(change.get("operationType") or "unknown")
    if not collection:
        return
    document_key = change.get("documentKey")
    full_document = change.get("fullDocument")
    update_description = change.get("updateDescription")
    record = (
        full_document
        if isinstance(full_document, dict)
        else document_key if isinstance(document_key, dict) else None
    )
    await publish_cdc_event(
        collection=str(collection),
        operation=f"change_stream_{operation}",
        record=record,
        before=None,
        after=full_document,
        filter=document_key,
        update=update_description,
        result={
            "operation_type": operation,
            "document_key": document_key,
            "record_id": _record_id_from_document_key(document_key),
            "cluster_time": (
                str(change.get("clusterTime"))
                if change.get("clusterTime") is not None
                else None
            ),
        },
    )
    _events_published += 1
    _last_event_at = datetime.now(timezone.utc).isoformat()


async def _watch_database(db) -> None:
    global _last_error, _last_started_at
    if _stop_event is None:
        return
    _last_started_at = datetime.now(timezone.utc).isoformat()
    while not _stop_event.is_set():
        try:
            async with db.watch(full_document="updateLookup") as stream:
                _last_error = None
                async for change in stream:
                    if _stop_event.is_set():
                        break
                    await publish_change_stream_event(change)
        except Exception as exc:
            _last_error = str(exc)
            try:
                await asyncio.wait_for(_stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass


async def start_mongo_cdc_watcher(db) -> None:
    global _task, _stop_event
    if _task is not None and not _task.done():
        return
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_watch_database(db))


async def stop_mongo_cdc_watcher() -> None:
    global _task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _task is not None:
        await _task
        _task = None
    _stop_event = None
