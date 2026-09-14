from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from services.common.compliance import document_record_id, utc_now
from services.common.config import settings


def cdc_event(
    *,
    collection: str,
    operation: str,
    record: dict[str, Any] | None = None,
    before: Any = None,
    after: Any = None,
    filter: Any = None,
    update: Any = None,
    result: Any = None,
) -> dict[str, Any]:
    record_id = (
        document_record_id(record)
        if isinstance(record, dict)
        else document_record_id(after) if isinstance(after, dict) else None
    )
    return jsonable_encoder(
        {
            "event_id": f"cdc-{uuid4().hex}",
            "event_type": "cdc-change",
            "source": "mongodb-cdc",
            "subject": settings.NATS_CDC_SUBJECT,
            "collection": collection,
            "operation": operation,
            "record_id": record_id,
            "before": before,
            "after": after,
            "filter": filter,
            "update": update,
            "result": result,
            "occurred_at": utc_now(),
        }
    )


async def publish_cdc_event(**kwargs) -> None:
    try:
        from services.auth.helpers import nats_store

        await nats_store.publish_event(
            __import__("json").dumps(cdc_event(**kwargs), default=str).encode("utf-8"),
            subject=settings.NATS_CDC_SUBJECT,
        )
    except Exception:
        return
