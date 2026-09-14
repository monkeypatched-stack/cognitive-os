import asyncio
import json
import socket
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.common.config import settings
from services.common.event_types import configured_event_type_slugs, measurement_suffix
from services.events.models.events import EventResponse

MAX_INFLUX_EVENTS = 1000


def _known_influx_event_measurements() -> list[str]:
    measurements = [
        settings.NATS_EVENTS_SUBJECT,
        settings.INFLUXDB_EVENTS_MEASUREMENT,
    ]
    prefix = (
        str(settings.INFLUXDB_EVENT_TYPE_MEASUREMENT_PREFIX or "events").strip()
        or "events"
    )
    for event_type in configured_event_type_slugs(
        settings.INFLUXDB_EVENT_TYPE_MEASUREMENTS
    ):
        measurements.append(f"{prefix}_{measurement_suffix(event_type)}")
        measurements.append(f"{settings.NATS_EVENT_TYPE_SUBJECT_PREFIX}.{event_type}")
    return list(dict.fromkeys(measurements))


def _is_missing_influx_events_table(status_code: int, detail: str) -> bool:
    normalized = detail.lower()
    return status_code == 400 and "table" in normalized and "not found" in normalized


def _clean_filter(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_entity_id(value: Optional[str]) -> Optional[str]:
    value = _clean_filter(value)
    if value is None:
        return None
    if value.upper() in {"DEFAULT", "UNKNOWN", "N/A", "NA", "NONE", "NULL"}:
        return None
    return value


def _clean_category_filter(value: Optional[str]) -> Optional[str]:
    value = _clean_filter(value)
    if value is None:
        return None

    aliases = {
        "eventlog": "Event Log",
        "EventLog": "Event Log",
        "event log": "Event Log",
        "countevent": "Count Event",
        "CountEvent": "Count Event",
        "count event": "Count Event",
        "maintenancelog": "Maintenance Log",
        "MaintenanceLog": "Maintenance Log",
        "maintenance log": "Maintenance Log",
        "downtimelog": "Downtime Log",
        "DowntimeLog": "Downtime Log",
        "downtime log": "Downtime Log",
        "cleaninglog": "Cleaning Log",
        "CleaningLog": "Cleaning Log",
        "cleaning log": "Cleaning Log",
        "calibrationlog": "Calibration Log",
        "CalibrationLog": "Calibration Log",
        "calibration log": "Calibration Log",
    }
    return aliases.get(
        value, aliases.get(value.replace(" ", "").replace("-", "").lower(), value)
    )


def _event_scope(event: dict) -> Optional[str]:
    scope = _clean_filter(event.get("scope"))
    if scope:
        return scope
    if _clean_entity_id(event.get("machine_id")):
        return "machine"
    if _clean_entity_id(event.get("equipment_id")):
        return "equipment"
    if _clean_entity_id(event.get("workstation_id")):
        return "workstation"
    if _clean_entity_id(event.get("warehouse_id")):
        return "warehouse"
    if str(event.get("type") or "").strip().lower() in {
        "scan rfid",
        "rfid scan",
        "nfc scan",
        "scan nfc",
    }:
        return "machine"
    return None


def _query_influx_measurement(measurement: str) -> list[dict]:
    query = (
        "SELECT * "
        f'FROM "{measurement}" '
        "WHERE payload_type = 'json' "
        "AND time >= now() - INTERVAL '90 days' "
        "ORDER BY time DESC "
        f"LIMIT {MAX_INFLUX_EVENTS}"
    )
    params = urlencode({"db": settings.INFLUXDB_BUCKET, "q": query})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/query_sql?{params}"
    headers = {}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if _is_missing_influx_events_table(exc.code, detail):
            return []
        raise RuntimeError(
            f"InfluxDB query failed with status {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Unable to connect to InfluxDB at {settings.INFLUXDB_URL}: {exc}"
        ) from exc
    except (
        ConnectionResetError,
        TimeoutError,
        socket.timeout,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            f"InfluxDB query failed for measurement '{measurement}': {exc}"
        ) from exc


def _query_influx_events() -> list[dict]:
    rows: list[dict] = []
    for measurement in _known_influx_event_measurements():
        try:
            rows.extend(_query_influx_measurement(measurement))
        except RuntimeError:
            continue
    return rows


def _payloads_from_influx() -> list[dict]:
    rows = _query_influx_events()
    payloads = [
        payload for row in rows if (payload := _payload_from_row(row)) is not None
    ]
    return _dedupe_payloads_by_id(payloads)


async def _payloads_from_event_store() -> list[dict]:
    return await asyncio.to_thread(_payloads_from_influx)


def _dedupe_payloads_by_id(payloads: list[dict]) -> list[dict]:
    seen_indexes: dict[str, int] = {}
    deduped = []
    for payload in payloads:
        event_id = payload.get("id")
        if event_id is not None:
            event_id = str(event_id)
            if event_id in seen_indexes:
                existing_index = seen_indexes[event_id]
                if _payload_quality_score(payload) > _payload_quality_score(
                    deduped[existing_index]
                ):
                    deduped[existing_index] = payload
                continue
            seen_indexes[event_id] = len(deduped)
        deduped.append(payload)
    return deduped


def _payload_quality_score(payload: dict) -> int:
    score = 0
    if _clean_entity_id(payload.get("machine_id")):
        score += 4
    if _clean_entity_id(payload.get("equipment_id")):
        score += 2
    if _clean_filter(payload.get("scope")):
        score += 1
    return score


def _payload_from_row(row: dict) -> dict | None:
    payload_json = row.get("payload_json")
    if isinstance(payload_json, str):
        try:
            payload = json.loads(payload_json)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    # Construct payload from row tags/fields when payload_json is missing
    event_id = row.get("event_id", "")
    event_type = row.get("event_type", "")
    equipment_id = row.get("equipment_id", "")
    equipment_name = row.get("equipment_name", "")
    source = row.get("source", "")
    time_val = row.get("time", "")

    if not event_id:
        return None

    # Determine date/time from time field
    date_str = ""
    time_str = ""
    if time_val:
        if "T" in time_val:
            parts = time_val.split("T")
            date_str = parts[0][:10]
            time_str = parts[1][:8] if len(parts) > 1 else ""
        else:
            date_str = time_val[:10]

    # Determine category from event_type
    category = "Event Log"
    if "maintenance" in event_type.lower():
        category = "Maintenance Log"
    elif "calibration" in event_type.lower():
        category = "Calibration Log"
    elif "cleaning" in event_type.lower():
        category = "Cleaning Log"
    elif "downtime" in event_type.lower():
        category = "Downtime Log"

    return {
        "id": event_id,
        "date": date_str,
        "time": time_str,
        "type": event_type.replace("-", " ").replace("_", " ").title(),
        "category": category,
        "work_order_id": "",
        "priority": "Medium",
        "equipment_id": equipment_id,
        "machine_id": None,
        "details": f"{equipment_name} maintenance event" if equipment_name else "",
        "user": "",
        "nfc_type": "",
        "nfc_tag_id": "",
        "nfc_max_size": 0,
        "nfc_payload": "",
    }


def _event_from_payload(payload: dict) -> dict | None:
    try:
        event = dict(payload)
        if _clean_filter(event.get("scope")) is None:
            event["scope"] = _event_scope(event)
        # Set default category from event_type if not present
        if not event.get("category"):
            event_type = event.get("event_type", "")
            if "maintenance" in event_type.lower():
                event["category"] = "Maintenance Log"
            elif "calibration" in event_type.lower():
                event["category"] = "Calibration Log"
            elif "cleaning" in event_type.lower():
                event["category"] = "Cleaning Log"
            elif "downtime" in event_type.lower():
                event["category"] = "Downtime Log"
            else:
                event["category"] = "Event Log"
        # For machine maintenance events, ensure machine_id is primary and equipment_id is not set
        if event.get("category") == "Maintenance Log" and event.get("machine_id"):
            event.pop("equipment_id", None)
        return EventResponse.model_validate(event).model_dump()
    except ValueError:
        return None


def _matches_filters(
    event: dict,
    status: Optional[str],
    priority: Optional[str],
    event_type: Optional[str],
    scope: Optional[str],
    category: Optional[str],
) -> bool:
    status = _clean_filter(status)
    priority = _clean_filter(priority)
    event_type = _clean_filter(event_type)
    scope = _clean_filter(scope)
    category = _clean_category_filter(category)

    if status and event.get("status") != status:
        return False
    if priority and event.get("priority") != priority:
        return False
    if event_type and event.get("type") != event_type:
        return False
    if scope and _event_scope(event) != scope:
        return False
    if category and event.get("category") != category:
        return False
    return True


async def get_all_events(
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    event_type: Optional[str] = None,
    scope: Optional[str] = None,
    category: Optional[str] = None,
) -> tuple[list[dict], int]:
    payloads = await _payloads_from_event_store()
    events = []
    for payload in payloads:
        if not _matches_filters(payload, status, priority, event_type, scope, category):
            continue

        event = _event_from_payload(payload)
        if event is not None:
            events.append(event)

    start = (page - 1) * page_size
    end = start + page_size
    return events[start:end], len(events)


def _matches_count_filters(
    event: dict,
    path: str,
    scope: Optional[str],
    category: Optional[str],
) -> bool:
    scope = _clean_filter(scope)
    category = _clean_category_filter(category)

    if event.get("count_event_path") != path:
        return False
    if scope and _event_scope(event) != scope:
        return False
    if category and event.get("category") != category:
        return False
    return True


async def get_count_events(
    path: str,
    page: int = 1,
    page_size: int = 20,
    scope: Optional[str] = None,
    category: Optional[str] = "Count Event",
) -> tuple[list[dict], int]:
    payloads = await _payloads_from_event_store()
    events = [
        payload
        for payload in payloads
        if _matches_count_filters(payload, path, scope, category)
    ]
    start = (page - 1) * page_size
    end = start + page_size
    return events[start:end], len(events)


async def get_event_by_id(event_id: str) -> Optional[dict]:
    payloads = await _payloads_from_event_store()
    for payload in payloads:
        if payload.get("id") != event_id:
            continue
        return _event_from_payload(payload)
    return None


async def get_events_by_machine_id(
    machine_id: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    payloads = await _payloads_from_event_store()
    events = [
        event
        for payload in payloads
        if payload.get("machine_id") == machine_id
        if (event := _event_from_payload(payload)) is not None
    ]
    start = (page - 1) * page_size
    end = start + page_size
    return events[start:end], len(events)


async def get_events_by_equipment_id(
    equipment_id: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    payloads = await _payloads_from_event_store()
    events = [
        event
        for payload in payloads
        if payload.get("equipment_id") == equipment_id
        if (event := _event_from_payload(payload)) is not None
    ]
    start = (page - 1) * page_size
    end = start + page_size
    return events[start:end], len(events)
