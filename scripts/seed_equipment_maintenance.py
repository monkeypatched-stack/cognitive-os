#!/usr/bin/env python3
"""Seed equipment maintenance events into MongoDB and InfluxDB."""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

NOW = datetime.now(timezone.utc)

DAYS_AGO = lambda d: NOW - timedelta(days=d)
HOURS_FROM = lambda h: NOW + timedelta(hours=h)


def _escape_tag(value: str) -> str:
    return value.replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


EQUIPMENT_MAINTENANCE = [
    {
        "id": "EMLOG-001",
        "equipment_id": "EQP-TAB-02-01",
        "equipment_name": "Dust Extractor",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly filter bag replacement and motor inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-001",
        "offset_days": 20,
    },
    {
        "id": "EMLOG-002",
        "equipment_id": "EQP-TAB-03-01",
        "equipment_name": "Chopper Motor",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Quarterly bearing inspection and lubrication",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-002",
        "offset_days": 35,
    },
    {
        "id": "EMLOG-003",
        "equipment_id": "EQP-TAB-03-02",
        "equipment_name": "Impeller Motor",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Quarterly seal and bearing inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-003",
        "offset_days": 35,
    },
    {
        "id": "EMLOG-004",
        "equipment_id": "EQP-TAB-04-01",
        "equipment_name": "Product Container",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Low",
        "description": "Monthly visual inspection for dents and cracks",
        "performed_by": "Mechanical Technician",
        "offset_days": 10,
    },
    {
        "id": "EMLOG-005",
        "equipment_id": "EQP-TAB-04-02",
        "equipment_name": "Finger Bag Set",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Replaced worn finger bags, checked seal integrity",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-005",
        "offset_days": 15,
    },
    {
        "id": "EMLOG-006",
        "equipment_id": "EQP-TAB-06-01",
        "equipment_name": "Bin Lifter",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Monthly hydraulic system check and chain inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-006",
        "offset_days": 12,
    },
    {
        "id": "EMLOG-007",
        "equipment_id": "EQP-TAB-07-01",
        "equipment_name": "Punch & Die Set",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Weekly inspection of punch tips and die bores",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-007",
        "offset_days": 5,
    },
    {
        "id": "EMLOG-008",
        "equipment_id": "EQP-TAB-07-02",
        "equipment_name": "Force Feeder",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly paddle and scraper blade inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-008",
        "offset_days": 8,
    },
    {
        "id": "EMLOG-009",
        "equipment_id": "EQP-TAB-07-04",
        "equipment_name": "Tablet Deduster",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Low",
        "description": "Monthly screen and brush inspection",
        "performed_by": "Mechanical Technician",
        "offset_days": 10,
    },
    {
        "id": "EMLOG-010",
        "equipment_id": "EQP-TAB-08-02",
        "equipment_name": "Peristaltic Pump",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly tubing replacement and flow rate calibration",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-010",
        "offset_days": 14,
    },
    {
        "id": "EMLOG-011",
        "equipment_id": "EQP-TAB-08-03",
        "equipment_name": "Spray Gun Assembly",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly nozzle cleaning and needle inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-011",
        "offset_days": 14,
    },
    {
        "id": "EMLOG-012",
        "equipment_id": "EQP-TAB-09-01",
        "equipment_name": "Inspection Conveyor",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Low",
        "description": "Monthly belt tension and alignment check",
        "performed_by": "Mechanical Technician",
        "offset_days": 7,
    },
    {
        "id": "EMLOG-013",
        "equipment_id": "EQP-TAB-09-03",
        "equipment_name": "Metal Detector",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Monthly sensitivity test with reference standards",
        "performed_by": "QC Lab Technician",
        "work_order_id": "EM-WO-013",
        "offset_days": 7,
    },
    {
        "id": "EMLOG-014",
        "equipment_id": "EQP-TAB-09-05",
        "equipment_name": "Tablet Polisher",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Low",
        "description": "Monthly brush and drum inspection",
        "performed_by": "Mechanical Technician",
        "offset_days": 10,
    },
    {
        "id": "EMLOG-015",
        "equipment_id": "EQP-TAB-10-02",
        "equipment_name": "Tablet Counter",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly optical sensor calibration and count verification",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-015",
        "offset_days": 12,
    },
    {
        "id": "EMLOG-016",
        "equipment_id": "EQP-TAB-10-05",
        "equipment_name": "Batch Coding Machine",
        "maintenance_type": "Preventive",
        "status": "In Progress",
        "severity": "Medium",
        "description": "Monthly printhead cleaning and ink system check",
        "performed_by": "Mechanical Technician",
        "offset_days": 1,
    },
    {
        "id": "EMLOG-017",
        "equipment_id": "EQP-CAP-07-01",
        "equipment_name": "Capsule Rectifier",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "Medium",
        "description": "Monthly orientation mechanism inspection",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-017",
        "offset_days": 18,
    },
    {
        "id": "EMLOG-018",
        "equipment_id": "EQP-CAP-07-03",
        "equipment_name": "Tamping Pin Set",
        "maintenance_type": "Preventive",
        "status": "Completed",
        "severity": "High",
        "description": "Monthly pin wear measurement and replacement check",
        "performed_by": "Mechanical Technician",
        "work_order_id": "EM-WO-018",
        "offset_days": 15,
    },
    {
        "id": "EMLOG-019",
        "equipment_id": "EQP-CAP-07-04",
        "equipment_name": "Dosing Disc Set",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "High",
        "description": "Monthly disc wear inspection and thickness measurement",
        "performed_by": "Mechanical Technician",
        "offset_days": -2,
    },
    {
        "id": "EMLOG-020",
        "equipment_id": "EQP-CAP-08-01",
        "equipment_name": "Capsule Polishing Machine",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "Medium",
        "description": "Monthly brush and polishing drum inspection",
        "performed_by": "Mechanical Technician",
        "offset_days": -3,
    },
    {
        "id": "EMLOG-021",
        "equipment_id": "EQP-CAP-08-03",
        "equipment_name": "Capsule Deduster",
        "maintenance_type": "Preventive",
        "status": "Scheduled",
        "severity": "Low",
        "description": "Monthly screen and air knife inspection",
        "performed_by": "Mechanical Technician",
        "offset_days": -1,
    },
    {
        "id": "EMLOG-022",
        "equipment_id": "EQP-TAB-07-01",
        "equipment_name": "Punch & Die Set",
        "maintenance_type": "Corrective",
        "status": "Completed",
        "severity": "Critical",
        "description": "Punch tip chipping repair — replaced worn punch set",
        "performed_by": "Maintenance Engineer",
        "work_order_id": "EM-WO-022",
        "offset_days": 4,
    },
]


def _add_timestamps(record: dict) -> dict:
    record["created_at"] = NOW.isoformat()
    record["updated_at"] = NOW.isoformat()
    record["category"] = "Maintenance Log"
    return record


async def seed_mongo():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Clearing existing equipment maintenance logs...")
    await db["equipment_maintenance_logs"].delete_many({})

    print(f"Inserting {len(EQUIPMENT_MAINTENANCE)} equipment maintenance records into MongoDB...")
    for record in EQUIPMENT_MAINTENANCE:
        await db["equipment_maintenance_logs"].insert_one(dict(_add_timestamps(record)))

    count = await db["equipment_maintenance_logs"].count_documents({})
    print(f"  MongoDB: {count} records")
    client.close()


def seed_influx():
    print(f"Inserting {len(EQUIPMENT_MAINTENANCE)} equipment maintenance records into InfluxDB...")
    lines = []
    for m in EQUIPMENT_MAINTENANCE:
        ts = (NOW - timedelta(days=m["offset_days"])).timestamp() * 1_000_000_000
        tags = (
            f"event_type=equipment-maintenance-log,"
            f"source=seed-data-equipment,"
            f"subject=events_maintenance_log,"
            f"payload_type=json,"
            f"equipment_id={_escape_tag(m['equipment_id'])},"
            f"equipment_name={_escape_tag(m['equipment_name'])}"
        )
        fields = f'event_id="{_escape_tag(m["id"])}"'
        lines.append(f"events_maintenance_log,{tags} {fields} {int(ts)}")

    query = urlencode({"db": settings.INFLUXDB_BUCKET})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/write_lp?{query}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    body = "\n".join(lines)
    request = Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        response.read()
    print(f"  InfluxDB: {len(lines)} records")


def main():
    asyncio.run(seed_mongo())
    seed_influx()
    print(f"\nDone. {len(EQUIPMENT_MAINTENANCE)} equipment maintenance records seeded.")


if __name__ == "__main__":
    main()
