#!/usr/bin/env python3
"""Seed machine maintenance event logs into InfluxDB."""

import sys
import json
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

NOW = datetime.now(timezone.utc)

DAYS_AGO = lambda d: NOW - timedelta(days=d)


def _escape_tag(value: str) -> str:
    return value.replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _field_value(value: str) -> str:
    return f'"{value}"'


def write_maint(m, offset_days):
    """Write a single maintenance record to InfluxDB as events_maintenance_log."""
    ts = (NOW - timedelta(days=offset_days)).timestamp() * 1_000_000_000
    measurement = "events_maintenance_log"
    event_id = m["id"]
    payload = {
        "id": m["id"],
        "machine_id": m["machine_id"],
        "equipment_id": m["equipment_id"],
        "machine_name": m["machine_name"],
        "maintenance_type": m["maintenance_type"],
        "status": m["status"],
        "severity": m["severity"],
        "description": m.get("description", ""),
        "issue_reported": m.get("issue_reported"),
        "resolution": m.get("resolution"),
        "performed_by": m.get("performed_by"),
        "reviewed_by": m.get("reviewed_by"),
        "work_order_id": m.get("work_order_id"),
        "parts_used": m.get("parts_used", []),
    }
    import json as _json

    payload = {
        "id": m["id"],
        "date": (NOW - timedelta(days=offset_days)).strftime("%Y-%m-%d"),
        "time": (NOW - timedelta(days=offset_days)).strftime("%H:%M:%S"),
        "type": m["maintenance_type"],
        "category": "Maintenance Log",
        "work_order_id": m.get("work_order_id") or "",
        "priority": m.get("severity", "Medium"),
        "machine_id": m["machine_id"],
        "machine_name": m["machine_name"],
        "details": m.get("description", ""),
        "user": m.get("performed_by", ""),
        "nfc_type": "maintenance-event",
        "nfc_tag_id": f"NFC-{m['machine_id']}",
        "nfc_max_size": 512,
        "nfc_payload": f"maintenance:{m['id']}:{m['status']}",
        "maintenance_type": m["maintenance_type"],
        "status": m["status"],
        "severity": m["severity"],
        "resolution": m.get("resolution"),
        "issue_reported": m.get("issue_reported"),
        "performed_by": m.get("performed_by"),
        "reviewed_by": m.get("reviewed_by"),
    }
    payload_json = _json.dumps(payload).replace('"', '\\"')
    tags = (
        f"event_type=maintenance-log,"
        f"source=seed-data,"
        f"subject=events_maintenance_log,"
        f"payload_type=json,"
        f"machine_id={_escape_tag(m['machine_id'])},"
        f"equipment_name={_escape_tag(m['machine_name'])}"
    )
    fields = f'event_id={_field_value(event_id)},payload_json="{payload_json}"'
    line = f"{measurement},{tags} {fields} {int(ts)}"
    return line


MAINTENANCE_RECORDS = [
    {
        "id": "MLOG-001",
        "equipment_id": "MCH-CAP-03-01",
        "machine_id": "MCH-CAP-03-01",
        "machine_name": "Rapid Mixer Granulator",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Quarterly PM — impeller blades inspected, mechanical seal kit replaced, gearbox oil checked",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-RMG-001",
        "parts_used": [
            {
                "part_id": "MSEAL-001",
                "name": "Mechanical Seal Kit",
                "quantity": 1,
                "cost_per_unit": 8500.0,
            }
        ],
        "offset_days": 30,
    },
    {
        "id": "MLOG-002",
        "equipment_id": "MCH-CAP-04-01",
        "machine_id": "MCH-CAP-04-01",
        "machine_name": "Fluid Bed Dryer",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly PM — finger bag set replaced, inlet/exhaust filters inspected, blower belt checked",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-FBD-001",
        "parts_used": [
            {
                "part_id": "FINGERBAG-001",
                "name": "Finger Bag Set",
                "quantity": 1,
                "cost_per_unit": 12000.0,
            }
        ],
        "offset_days": 15,
    },
    {
        "id": "MLOG-003",
        "equipment_id": "MCH-CAP-06-01",
        "machine_id": "MCH-CAP-06-01",
        "machine_name": "Octagonal Blender",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Quarterly PM — butterfly valve seal inspected, shaft seal kit checked, drive belt verified",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-BLD-001",
        "offset_days": 45,
    },
    {
        "id": "MLOG-004",
        "equipment_id": "MCH-TAB-07-01",
        "machine_id": "MCH-TAB-07-01",
        "machine_name": "Rotary Tablet Press",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Weekly PM — punch/die set inspected, cam track cleaned, force feeder paddle checked",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-TBP-001",
        "offset_days": 3,
    },
    {
        "id": "MLOG-005",
        "equipment_id": "MCH-TAB-08-02",
        "machine_id": "MCH-TAB-08-02",
        "machine_name": "Auto Coater",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly PM — spray gun nozzle replaced, pan drive belt inspected, peristaltic pump tubing replaced",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-ACT-001",
        "parts_used": [
            {
                "part_id": "NOZ-001",
                "name": "Spray Gun Nozzle",
                "quantity": 2,
                "cost_per_unit": 3500.0,
            }
        ],
        "offset_days": 20,
    },
    {
        "id": "MLOG-006",
        "equipment_id": "MCH-CAP-10-02",
        "machine_id": "MCH-CAP-10-02",
        "machine_name": "Capsule Filling Machine",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly PM — dosing disc set inspected, tamping pin set checked, vacuum pump seal verified",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-CFM-001",
        "offset_days": 10,
    },
    {
        "id": "MLOG-007",
        "equipment_id": "MCH-CAP-10-01",
        "machine_id": "MCH-CAP-10-01",
        "machine_name": "Blister Packing Machine",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly PM — forming/sealing die set inspected, heating element checked, conveyor chain lubricated",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "work_order_id": "PM-BPM-001",
        "offset_days": 25,
    },
    {
        "id": "MLOG-008",
        "equipment_id": "MCH-CAP-03-01",
        "machine_id": "MCH-CAP-03-01",
        "machine_name": "Rapid Mixer Granulator",
        "maintenance_type": "Corrective",
        "status": "Completed",
        "severity": "Critical",
        "description": "Emergency repair — impeller bearing failure, root cause analysis completed, bearing replaced",
        "issue_reported": "RMG impeller bearing failure causing vibration and noise",
        "resolution": "Replaced impeller bearing assembly, recalibrated RPM indicator",
        "performed_by": "Maintenance Engineer",
        "reviewed_by": "Plant Manager",
        "work_order_id": "CM-001",
        "parts_used": [
            {
                "part_id": "BRG-001",
                "name": "Impeller Bearing Assembly",
                "quantity": 1,
                "cost_per_unit": 15000.0,
            }
        ],
        "offset_days": 5,
    },
    {
        "id": "MLOG-009",
        "equipment_id": "MCH-TAB-07-01",
        "machine_id": "MCH-TAB-07-01",
        "machine_name": "Rotary Tablet Press",
        "maintenance_type": "Corrective",
        "status": "Completed",
        "severity": "Critical",
        "description": "Emergency repair — turret bearing failure, full inspection and repair",
        "issue_reported": "Turret bearing failure causing tablet weight variation",
        "resolution": "Replaced turret bearing, inspected punch/die sets, recalibrated compression force sensors",
        "performed_by": "Maintenance Engineer",
        "reviewed_by": "Plant Manager",
        "work_order_id": "CM-002",
        "parts_used": [
            {
                "part_id": "BRG-TWR-001",
                "name": "Turret Bearing",
                "quantity": 1,
                "cost_per_unit": 25000.0,
            }
        ],
        "offset_days": 2,
    },
    {
        "id": "MLOG-010",
        "equipment_id": "MCH-CAP-04-01",
        "machine_id": "MCH-CAP-04-01",
        "machine_name": "Fluid Bed Dryer",
        "maintenance_type": "Preventive",
        "status": "In Progress",
        "severity": "Medium",
        "description": "Monthly PM in progress — finger bag replacement underway",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-FBD-001",
        "offset_days": 1,
    },
    {
        "id": "MLOG-011",
        "equipment_id": "MCH-TAB-08-02",
        "machine_id": "MCH-TAB-08-02",
        "machine_name": "Auto Coater",
        "maintenance_type": "Preventive",
        "status": "In Progress",
        "severity": "Medium",
        "description": "Monthly PM in progress — coating pan drive belt replacement",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-ACT-001",
        "offset_days": 0,
    },
    {
        "id": "MLOG-012",
        "equipment_id": "MCH-CAP-03-01",
        "machine_id": "MCH-CAP-03-01",
        "machine_name": "Rapid Mixer Granulator",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "Medium",
        "description": "Next quarterly PM — full inspection and seal replacement",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-RMG-001",
        "offset_days": -2,
    },
    {
        "id": "MLOG-013",
        "equipment_id": "MCH-CAP-04-01",
        "machine_id": "MCH-CAP-04-01",
        "machine_name": "Fluid Bed Dryer",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "Medium",
        "description": "Monthly PM — finger bag and filter inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-FBD-001",
        "offset_days": -3,
    },
    {
        "id": "MLOG-014",
        "equipment_id": "MCH-TAB-07-01",
        "machine_id": "MCH-TAB-07-01",
        "machine_name": "Rotary Tablet Press",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "High",
        "description": "Weekly PM — punch/die inspection and lubrication",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-TBP-001",
        "offset_days": -1,
    },
    {
        "id": "MLOG-015",
        "equipment_id": "MCH-TAB-08-02",
        "machine_id": "MCH-TAB-08-02",
        "machine_name": "Auto Coater",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "Medium",
        "description": "Monthly PM — spray system and drive inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-ACT-001",
        "offset_days": -4,
    },
    {
        "id": "MLOG-016",
        "equipment_id": "MCH-CAP-06-01",
        "machine_id": "MCH-CAP-06-01",
        "machine_name": "Octagonal Blender",
        "maintenance_type": "Inspection",
        "status": "Completed",
        "severity": "Low",
        "description": "Routine inspection — safety guards verified, emergency stop tested, interlocks checked",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "offset_days": 7,
    },
    {
        "id": "MLOG-017",
        "equipment_id": "MCH-CAP-10-01",
        "machine_id": "MCH-CAP-10-01",
        "machine_name": "Blister Packing Machine",
        "maintenance_type": "Inspection",
        "status": "Completed",
        "severity": "Low",
        "description": "Routine inspection — heating elements verified, conveyor chain tension checked",
        "performed_by": "Mechanical Technician",
        "reviewed_by": "Line Supervisor",
        "offset_days": 14,
    },
    {
        "id": "MLOG-018",
        "equipment_id": "MCH-CAP-10-02",
        "machine_id": "MCH-CAP-10-02",
        "machine_name": "Capsule Filling Machine",
        "maintenance_type": "Preventive",
        "status": "Delayed",
        "severity": "Medium",
        "description": "Monthly PM delayed — awaiting replacement parts for dosing disc",
        "issue_reported": "Dosing disc wear exceeded tolerance, replacement part on order",
        "performed_by": "Mechanical Technician",
        "work_order_id": "PM-CFM-001",
        "offset_days": 5,
    },
]


def write_lines(lines: list[str]):
    query = urlencode({"db": settings.INFLUXDB_BUCKET})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/write_lp?{query}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"
    body = "\n".join(lines)
    request = Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        response.read()
    print(f"  Wrote {len(lines)} records to InfluxDB")


def main():
    print(f"Writing {len(MAINTENANCE_RECORDS)} maintenance records to InfluxDB...")
    lines = [write_maint(m, m["offset_days"]) for m in MAINTENANCE_RECORDS]
    write_lines(lines)
    print(f"Done. {len(lines)} maintenance records seeded.")


if __name__ == "__main__":
    main()
