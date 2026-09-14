import json
import asyncio
import logging
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.common.config import settings
from services.common.event_types import (
    configured_event_type_slugs,
    infer_event_type,
    measurement_suffix,
    slugify_event_type,
)

log = logging.getLogger("uvicorn.error")


def _payload_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_kind": type(payload).__name__}
    return {
        "payload_id": payload.get("id"),
        "category": payload.get("category"),
        "scope": payload.get("scope"),
        "type": payload.get("type"),
        "work_order_id": payload.get("work_order_id"),
    }


def _sensor_summary(reading: dict[str, Any]) -> dict[str, Any]:
    return {
        "sensor_id": reading.get("sensor_id"),
        "value": reading.get("value"),
        "unit": reading.get("unit"),
        "machine_id": reading.get("machine_id"),
        "equipment_id": reading.get("equipment_id"),
    }


def _escape_key(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(" ", "\\ ")
        .replace(",", "\\,")
        .replace("=", "\\=")
    )


def _escape_string_field(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        return str(value)
    return f'"{_escape_string_field(str(value))}"'


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def subject_for_event_type(event_type: str) -> str:
    return f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}.{slugify_event_type(event_type)}"


def measurement_for_event_type(event_type: str) -> str:
    prefix = (
        str(settings.INFLUXDB_EVENT_TYPE_MEASUREMENT_PREFIX or "events").strip()
        or "events"
    )
    return f"{prefix}_{measurement_suffix(event_type)}"


def event_type_from_subject(subject: str) -> str | None:
    subject = str(subject or "").strip()
    prefix = f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}."
    if subject.startswith(prefix):
        return slugify_event_type(subject.removeprefix(prefix))
    return None


def measurement_for_subject(subject: str, event: dict[str, Any] | None = None) -> str:
    event_type = event_type_from_subject(subject)
    if event_type:
        return measurement_for_event_type(event_type)
    if event is not None:
        inferred_event_type = slugify_event_type(
            event.get("event_type") or infer_event_type(event)
        )
        if inferred_event_type != "generic":
            return measurement_for_event_type(inferred_event_type)
    return (
        str(
            subject
            or settings.INFLUXDB_EVENTS_MEASUREMENT
            or settings.NATS_EVENTS_SUBJECT
        ).strip()
        or settings.NATS_EVENTS_SUBJECT
    )


def known_event_measurements() -> list[str]:
    measurements = [settings.NATS_EVENTS_SUBJECT, settings.INFLUXDB_EVENTS_MEASUREMENT]
    for event_type in configured_event_type_slugs(
        settings.INFLUXDB_EVENT_TYPE_MEASUREMENTS
    ):
        measurements.append(measurement_for_event_type(event_type))
        measurements.append(subject_for_event_type(event_type))
    return list(dict.fromkeys(measurements))


def _line_protocol(event: dict[str, Any], subject: str, measurement: str) -> str:
    event_type = slugify_event_type(event.get("event_type") or infer_event_type(event))
    tags = {
        "subject": subject,
        "event_type": event_type,
        "source": str(event.get("source") or "websocket"),
        "payload_type": str(event.get("payload_type") or "unknown"),
    }
    user_id = event.get("user_id")
    if user_id is not None:
        tags["user_id"] = str(user_id)

    tag_set = ",".join(
        f"{_escape_key(key)}={_escape_key(value)}"
        for key, value in sorted(tags.items())
    )
    fields = {
        "event_id": str(event.get("event_id") or ""),
        "payload_json": json.dumps(event.get("payload")),
        "event_json": json.dumps(event),
    }
    field_set = ",".join(
        f"{_escape_key(key)}={_field_value(value)}" for key, value in fields.items()
    )

    point = f"{_escape_key(measurement)},{tag_set} {field_set}"
    received_at = _parse_time(event.get("received_at"))
    if received_at is not None:
        point = f"{point} {int(received_at.timestamp() * 1_000_000_000)}"
    return point


def _sensor_line_protocol(reading: dict[str, Any], subject: str) -> str:
    measurement = measurement_for_subject(subject)
    sensor_id = str(reading.get("sensor_id") or subject.rsplit(".", 1)[-1] or "unknown")
    tags = {
        "sensor_id": sensor_id,
        "subject": subject,
        "source": str(reading.get("source") or "mqtt"),
    }
    for key in (
        "sensor_type",
        "unit",
        "machine_id",
        "equipment_id",
        "edge_server_id",
        "topic",
    ):
        value = reading.get(key)
        if value not in (None, ""):
            tags[key] = str(value)

    tag_set = ",".join(
        f"{_escape_key(key)}={_escape_key(value)}"
        for key, value in sorted(tags.items())
    )
    fields: dict[str, Any] = {
        "reading_json": json.dumps(reading),
    }
    value = reading.get("value")
    if isinstance(value, bool):
        fields["value_bool"] = value
    elif isinstance(value, (int, float)):
        fields["value"] = value
    elif value is not None:
        fields["raw_value"] = str(value)
        try:
            fields["value"] = float(str(value))
        except ValueError:
            pass

    for key in ("quality", "status", "event_id"):
        value = reading.get(key)
        if value not in (None, ""):
            fields[key] = str(value)

    field_set = ",".join(
        f"{_escape_key(key)}={_field_value(value)}" for key, value in fields.items()
    )
    point = f"{_escape_key(measurement)},{tag_set} {field_set}"
    timestamp = _parse_time(reading.get("timestamp") or reading.get("received_at"))
    if timestamp is not None:
        point = f"{point} {int(timestamp.timestamp() * 1_000_000_000)}"
    return point


def _write_line_protocol(line_protocol: str) -> None:
    query = urlencode({"db": settings.INFLUXDB_BUCKET})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/write_lp?{query}"
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
    }
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    request = Request(
        url,
        data=line_protocol.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"InfluxDB write failed with status {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Unable to connect to InfluxDB at {settings.INFLUXDB_URL}: {exc}"
        ) from exc
    except (ConnectionResetError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"InfluxDB write failed at {settings.INFLUXDB_URL}: {exc}"
        ) from exc


async def write_websocket_event(event: dict[str, Any], subject: str) -> None:
    payload = event.get("payload")
    event_type = slugify_event_type(event.get("event_type") or infer_event_type(event))
    event["event_type"] = event_type
    event["subject"] = subject
    measurement = measurement_for_subject(subject, event)
    log.info(
        "Influx write queued event_id=%s source=%s subject=%s event_type=%s measurement=%s payload_type=%s summary=%s",
        event.get("event_id"),
        event.get("source"),
        subject,
        event_type,
        measurement,
        event.get("payload_type"),
        _payload_summary(payload),
    )
    line_protocol = _line_protocol(event, subject, measurement)
    await asyncio.to_thread(_write_line_protocol, line_protocol)
    log.info(
        "Influx write succeeded event_id=%s event_type=%s measurement=%s bucket=%s summary=%s",
        event.get("event_id"),
        event_type,
        measurement,
        settings.INFLUXDB_BUCKET,
        _payload_summary(payload),
    )


async def write_sensor_reading(reading: dict[str, Any], subject: str) -> None:
    subject = measurement_for_subject(subject)
    reading["subject"] = subject
    reading.setdefault("source", "mqtt")
    log.info(
        "Influx sensor write queued subject=%s table=%s summary=%s",
        subject,
        subject,
        _sensor_summary(reading),
    )
    line_protocol = _sensor_line_protocol(reading, subject)
    await asyncio.to_thread(_write_line_protocol, line_protocol)
    log.info(
        "Influx sensor write succeeded subject=%s bucket=%s summary=%s",
        subject,
        settings.INFLUXDB_BUCKET,
        _sensor_summary(reading),
    )


async def ensure_event_measurements() -> None:
    now = datetime.now(timezone.utc).isoformat()
    lines = []
    for measurement in known_event_measurements():
        event_type = (
            measurement.rsplit(".", 1)[-1]
            if measurement.startswith(f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}.")
            else "legacy"
        )
        event = {
            "event_id": f"bootstrap-{measurement}",
            "received_at": now,
            "source": "event-infra-bootstrap",
            "event_type": event_type,
            "payload_type": "json",
            "payload": {"bootstrap": True, "subject": measurement},
        }
        lines.append(_line_protocol(event, measurement, measurement))
    try:
        await asyncio.to_thread(_write_line_protocol, "\n".join(lines))
    except RuntimeError as exc:
        log.warning("Skipping InfluxDB event measurement bootstrap: %s", exc)


async def close_influx_client() -> None:
    return None
