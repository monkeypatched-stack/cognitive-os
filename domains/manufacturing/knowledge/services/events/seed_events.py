import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[2]))

from services.auth.helpers.influx_store import write_websocket_event
from services.common.config import settings
from services.events.routers.count_events import COUNT_EVENT_PATHS

TEST_EVENTS = [
    {
        "id": "TEST-EVT-001",
        "scope": "facility",
        "date": "2026-05-27",
        "time": "14:45:00",
        "type": "Scan RFID",
        "category": "Event Log",
        "work_order_id": "WO-TEST-001",
        "priority": "Medium",
        "equipment_id": "EQ-TEST-001",
        "machine_id": None,
        "details": "Seeded facility RFID scan event",
        "user": "USER-TEST",
        "nfc_type": "seed.test",
        "nfc_tag_id": "NFC-TEST-001",
        "nfc_max_size": 128,
        "nfc_payload": "seed-payload-001",
    },
    {
        "id": "TEST-EVT-002",
        "scope": "machine",
        "date": "2026-05-27",
        "time": "14:46:00",
        "type": "Machine Alert",
        "category": "Event Log",
        "work_order_id": "WO-TEST-002",
        "priority": "High",
        "equipment_id": None,
        "machine_id": "MCH-TEST-002",
        "details": "Seeded machine alert event",
        "user": "USER-TEST",
        "nfc_type": "seed.test",
        "nfc_tag_id": "NFC-TEST-002",
        "nfc_max_size": 128,
        "nfc_payload": "seed-payload-002",
    },
    {
        "id": "TEST-EVT-003",
        "scope": "workstation",
        "date": "2026-05-27",
        "time": "14:47:00",
        "type": "Cleaning Check",
        "category": "Event Log",
        "work_order_id": "",
        "priority": "Low",
        "equipment_id": "EQ-TEST-003",
        "machine_id": None,
        "details": "Seeded workstation cleaning event",
        "user": "USER-TEST",
        "nfc_type": "seed.test",
        "nfc_tag_id": "NFC-TEST-003",
        "nfc_max_size": 128,
        "nfc_payload": "seed-payload-003",
    },
    {
        "id": "TEST-EVT-004",
        "scope": "equipment",
        "date": "2026-05-27",
        "time": "14:48:00",
        "type": "Equipment Alert",
        "category": "Event Log",
        "work_order_id": "WO-TEST-003",
        "priority": "High",
        "equipment_id": "EQ-TEST-004",
        "machine_id": None,
        "details": "Seeded equipment vibration alert event",
        "user": "USER-TEST",
        "nfc_type": "seed.test",
        "nfc_tag_id": "NFC-TEST-004",
        "nfc_max_size": 128,
        "nfc_payload": "seed-payload-004",
    },
    {
        "id": "TEST-MACHINE-1-MAINT-001",
        "scope": "machine",
        "date": "2026-05-30",
        "time": "09:15:00",
        "type": "Preventive Maintenance",
        "category": "Maintenance Log",
        "work_order_id": "WO-MACHINE-1-MAINT-001",
        "priority": "Medium",
        "equipment_id": None,
        "machine_id": "MACH-2026-05-28-841477",
        "details": (
            "Machine 1 preventive maintenance completed for lubrication, "
            "guard inspection, vibration check, and safety verification."
        ),
        "user": "admin",
        "nfc_type": "seed.maintenance",
        "nfc_tag_id": "NFC-MACHINE-1-MAINT-001",
        "nfc_max_size": 256,
        "nfc_payload": "machine-1-maintenance-log",
    },
]

COUNT_EVENT_SCOPES = ["facility", "machine", "workstation", "warehouse"]
QUANTITY_EVENT_UNITS = {
    "consumed-count-events": "kg",
    "raw-material-consumption-events": "kg",
    "raw-material-defect-count-events": "kg",
    "workstation-raw-material-consumption-events": "L",
    "workstation-raw-material-defect-count-events": "L",
    "warehouse-raw-material-consumption-events": "kg",
    "warehouse-raw-material-defect-count-events": "kg",
}


def _measurement_fields(path: str, index: int) -> dict:
    if path in QUANTITY_EVENT_UNITS:
        quantity = round((index + 1) * 2.75, 2)
        unit = QUANTITY_EVENT_UNITS[path]
        return {
            "count": None,
            "quantity": quantity,
            "unit": unit,
            "uom": unit,
        }

    return {
        "count": (index + 1) * 5,
        "quantity": None,
        "unit": None,
        "uom": None,
    }


def _count_event_payload(path: str, index: int) -> dict:
    scope = COUNT_EVENT_SCOPES[index % len(COUNT_EVENT_SCOPES)]
    event = {
        "id": f"TEST-COUNT-{index + 1:03d}",
        "scope": scope,
        "category": "Count Event",
        "type": path.replace("-", " ").title(),
        "count_event_path": path,
        "work_order_id": f"WO-TEST-COUNT-{index + 1:03d}",
        "date": "2026-05-27",
        "time": f"15:{index % 60:02d}:00",
        "nfc_type": "seed.count",
        "nfc_tag_id": f"NFC-COUNT-{index + 1:03d}",
        "nfc_max_size": 128,
        "nfc_payload": f"seed-count-payload-{index + 1:03d}",
        "product_id": f"PROD-TEST-{(index % 5) + 1:03d}",
        "machine_id": f"MCH-TEST-{(index % 4) + 1:03d}",
        "equipment_id": f"EQ-TEST-{(index % 4) + 1:03d}",
        "workstation_id": f"WS-TEST-{(index % 4) + 1:03d}",
        "warehouse_id": f"WH-TEST-{(index % 3) + 1:03d}",
        "details": f"Seeded count event for {path}",
        "user": "USER-TEST",
    }
    event.update(_measurement_fields(path, index))
    return event


def _event_envelope(payload: dict) -> dict:
    return {
        "event_id": str(uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "source": "seed",
        "subject": settings.NATS_EVENTS_SUBJECT,
        "event_type": payload.get("category") or payload.get("type") or "generic",
        "user_id": payload["user"],
        "payload_type": "json",
        "payload": payload,
    }


async def seed_events() -> None:
    for event in TEST_EVENTS:
        await write_websocket_event(
            _event_envelope(event), settings.NATS_EVENTS_SUBJECT
        )
        print(f"Seeded event {event['id']} scope={event['scope']}")

    for index, path in enumerate(COUNT_EVENT_PATHS):
        event = _count_event_payload(path, index)
        await write_websocket_event(
            _event_envelope(event), settings.NATS_EVENTS_SUBJECT
        )
        print(
            "Seeded count event "
            f"{event['id']} path={event['count_event_path']} scope={event['scope']}"
        )


if __name__ == "__main__":
    asyncio.run(seed_events())
