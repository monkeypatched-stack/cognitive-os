#!/usr/bin/env python3
"""Seed machine maintenance event logs into MongoDB."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

NOW = datetime.now(timezone.utc)


def _maint(
    id,
    machine_id,
    machine_name,
    maint_type,
    status,
    severity,
    description,
    issue=None,
    resolution=None,
    scheduled_start=None,
    scheduled_end=None,
    actual_start=None,
    actual_end=None,
    performed_by=None,
    reviewed_by=None,
    work_order_id=None,
    parts=None,
    downtime_start=None,
    downtime_end=None,
):
    return {
        "id": id,
        "machine_id": machine_id,
        "machine_name": machine_name,
        "maintenance_type": maint_type,
        "status": status,
        "severity": severity,
        "description": description,
        "issue_reported": issue,
        "resolution": resolution,
        "scheduled_start": scheduled_start.isoformat() if scheduled_start else None,
        "scheduled_end": scheduled_end.isoformat() if scheduled_end else None,
        "actual_start": actual_start.isoformat() if actual_start else None,
        "actual_end": actual_end.isoformat() if actual_end else None,
        "next_due_date": None,
        "maintenance_interval_days": None,
        "performed_by": performed_by,
        "reviewed_by": reviewed_by,
        "approved_by": None,
        "downtime": (
            {
                "start_time": downtime_start.isoformat(),
                "end_time": downtime_end.isoformat(),
            }
            if downtime_start
            else None
        ),
        "parts_used": parts or [],
        "work_order_id": work_order_id,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


DAYS_AGO = lambda d: NOW - timedelta(days=d)
HOURS_AGO = lambda h: NOW - timedelta(hours=h)
HOURS_FROM = lambda h: NOW + timedelta(hours=h)


MAINTENANCE_LOGS = [
    # === COMPLETED ===
    _maint(
        "MLOG-001",
        "MACH-GRAN-001",
        "Rapid Mixer Granulator",
        "Preventive",
        "Completed",
        "Medium",
        "Quarterly PM completed — impeller blades inspected, mechanical seal kit replaced, gearbox oil checked",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-RMG-001",
        scheduled_start=DAYS_AGO(30),
        scheduled_end=DAYS_AGO(30) + timedelta(hours=4),
        actual_start=DAYS_AGO(30) + timedelta(minutes=15),
        actual_end=DAYS_AGO(30) + timedelta(hours=3, minutes=45),
        parts=[
            {
                "part_id": "MSEAL-001",
                "name": "Mechanical Seal Kit",
                "quantity": 1,
                "cost_per_unit": 8500.0,
            }
        ],
        downtime_start=DAYS_AGO(30) + timedelta(minutes=15),
        downtime_end=DAYS_AGO(30) + timedelta(hours=3, minutes=45),
    ),
    _maint(
        "MLOG-002",
        "MACH-DRY-001",
        "Fluid Bed Dryer",
        "Preventive",
        "Completed",
        "Medium",
        "Monthly PM completed — finger bag set replaced, inlet/exhaust filters inspected, blower belt checked",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-FBD-001",
        scheduled_start=DAYS_AGO(15),
        scheduled_end=DAYS_AGO(15) + timedelta(hours=3),
        actual_start=DAYS_AGO(15) + timedelta(minutes=10),
        actual_end=DAYS_AGO(15) + timedelta(hours=2, minutes=50),
        parts=[
            {
                "part_id": "FINGERBAG-001",
                "name": "Finger Bag Set",
                "quantity": 1,
                "cost_per_unit": 12000.0,
            }
        ],
        downtime_start=DAYS_AGO(15) + timedelta(minutes=10),
        downtime_end=DAYS_AGO(15) + timedelta(hours=2, minutes=50),
    ),
    _maint(
        "MLOG-003",
        "MACH-BLEND-001",
        "Octagonal Blender",
        "Preventive",
        "Completed",
        "Medium",
        "Quarterly PM completed — butterfly valve seal inspected, shaft seal kit checked, drive belt verified",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-BLD-001",
        scheduled_start=DAYS_AGO(45),
        scheduled_end=DAYS_AGO(45) + timedelta(hours=3),
        actual_start=DAYS_AGO(45) + timedelta(minutes=20),
        actual_end=DAYS_AGO(45) + timedelta(hours=2, minutes=40),
        downtime_start=DAYS_AGO(45) + timedelta(minutes=20),
        downtime_end=DAYS_AGO(45) + timedelta(hours=2, minutes=40),
    ),
    _maint(
        "MLOG-004",
        "MACH-COMP-001",
        "Rotary Tablet Press",
        "Preventive",
        "Completed",
        "High",
        "Weekly PM completed — punch/die set inspected, cam track cleaned, force feeder paddle checked",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-TBP-001",
        scheduled_start=DAYS_AGO(3),
        scheduled_end=DAYS_AGO(3) + timedelta(hours=2),
        actual_start=DAYS_AGO(3) + timedelta(minutes=5),
        actual_end=DAYS_AGO(3) + timedelta(hours=1, minutes=55),
        downtime_start=DAYS_AGO(3) + timedelta(minutes=5),
        downtime_end=DAYS_AGO(3) + timedelta(hours=1, minutes=55),
    ),
    _maint(
        "MLOG-005",
        "MACH-COAT-001",
        "Auto Coater",
        "Preventive",
        "Completed",
        "Medium",
        "Monthly PM completed — spray gun nozzle replaced, pan drive belt inspected, peristaltic pump tubing replaced",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-ACT-001",
        scheduled_start=DAYS_AGO(20),
        scheduled_end=DAYS_AGO(20) + timedelta(hours=3),
        actual_start=DAYS_AGO(20) + timedelta(minutes=10),
        actual_end=DAYS_AGO(20) + timedelta(hours=2, minutes=50),
        parts=[
            {
                "part_id": "NOZ-001",
                "name": "Spray Gun Nozzle",
                "quantity": 2,
                "cost_per_unit": 3500.0,
            },
            {
                "part_id": "TUBE-001",
                "name": "Peristaltic Pump Tubing",
                "quantity": 1,
                "cost_per_unit": 1200.0,
            },
        ],
        downtime_start=DAYS_AGO(20) + timedelta(minutes=10),
        downtime_end=DAYS_AGO(20) + timedelta(hours=2, minutes=50),
    ),
    _maint(
        "MLOG-006",
        "MACH-FILL-001",
        "Capsule Filling Machine",
        "Preventive",
        "Completed",
        "Medium",
        "Monthly PM completed — dosing disc set inspected, tamping pin set checked, vacuum pump seal verified",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-CFM-001",
        scheduled_start=DAYS_AGO(10),
        scheduled_end=DAYS_AGO(10) + timedelta(hours=3),
        actual_start=DAYS_AGO(10) + timedelta(minutes=15),
        actual_end=DAYS_AGO(10) + timedelta(hours=2, minutes=45),
        downtime_start=DAYS_AGO(10) + timedelta(minutes=15),
        downtime_end=DAYS_AGO(10) + timedelta(hours=2, minutes=45),
    ),
    _maint(
        "MLOG-007",
        "MACH-PACK-001",
        "Blister Packing Machine",
        "Preventive",
        "Completed",
        "Medium",
        "Monthly PM completed — forming/sealing die set inspected, heating element checked, conveyor chain lubricated",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        work_order_id="PM-BPM-001",
        scheduled_start=DAYS_AGO(25),
        scheduled_end=DAYS_AGO(25) + timedelta(hours=2),
        actual_start=DAYS_AGO(25) + timedelta(minutes=10),
        actual_end=DAYS_AGO(25) + timedelta(hours=1, minutes=50),
        downtime_start=DAYS_AGO(25) + timedelta(minutes=10),
        downtime_end=DAYS_AGO(25) + timedelta(hours=1, minutes=50),
    ),
    # === CORRECTIVE / BREAKDOWN ===
    _maint(
        "MLOG-008",
        "MACH-GRAN-001",
        "Rapid Mixer Granulator",
        "Corrective",
        "Completed",
        "Critical",
        "Emergency repair — RMG impeller bearing failure, root cause analysis completed, bearing replaced",
        issue="RMG impeller bearing failure causing vibration and noise",
        resolution="Replaced impeller bearing assembly, recalibrated RPM indicator, verified no further vibration",
        performed_by="Maintenance Engineer",
        reviewed_by="Plant Manager",
        work_order_id="CM-001",
        actual_start=DAYS_AGO(5) + timedelta(hours=8),
        actual_end=DAYS_AGO(5) + timedelta(hours=14),
        parts=[
            {
                "part_id": "BRG-001",
                "name": "Impeller Bearing Assembly",
                "quantity": 1,
                "cost_per_unit": 15000.0,
            },
            {
                "part_id": "SEAL-001",
                "name": "Shaft Seal Kit",
                "quantity": 1,
                "cost_per_unit": 4500.0,
            },
        ],
        downtime_start=DAYS_AGO(5) + timedelta(hours=8),
        downtime_end=DAYS_AGO(5) + timedelta(hours=14),
    ),
    _maint(
        "MLOG-009",
        "MACH-COMP-001",
        "Rotary Tablet Press",
        "Corrective",
        "Completed",
        "Critical",
        "Emergency repair — tablet press turret bearing failure, full inspection and repair",
        issue="Turret bearing failure causing tablet weight variation",
        resolution="Replaced turret bearing, inspected punch/die sets, recalibrated compression force sensors",
        performed_by="Maintenance Engineer",
        reviewed_by="Plant Manager",
        work_order_id="CM-002",
        actual_start=DAYS_AGO(2) + timedelta(hours=6),
        actual_end=DAYS_AGO(2) + timedelta(hours=12),
        parts=[
            {
                "part_id": "BRG-TWR-001",
                "name": "Turret Bearing",
                "quantity": 1,
                "cost_per_unit": 25000.0,
            }
        ],
        downtime_start=DAYS_AGO(2) + timedelta(hours=6),
        downtime_end=DAYS_AGO(2) + timedelta(hours=12),
    ),
    # === IN PROGRESS ===
    _maint(
        "MLOG-010",
        "MACH-DRY-001",
        "Fluid Bed Dryer",
        "Preventive",
        "In Progress",
        "Medium",
        "Monthly PM in progress — finger bag replacement underway",
        scheduled_start=DAYS_AGO(1),
        scheduled_end=DAYS_AGO(1) + timedelta(hours=3),
        actual_start=DAYS_AGO(1) + timedelta(minutes=30),
        performed_by="Mechanical Technician",
    ),
    _maint(
        "MLOG-011",
        "MACH-COAT-001",
        "Auto Coater",
        "Preventive",
        "In Progress",
        "Medium",
        "Monthly PM in progress — coating pan drive belt replacement",
        scheduled_start=HOURS_AGO(2),
        scheduled_end=HOURS_AGO(2) + timedelta(hours=3),
        actual_start=HOURS_AGO(1),
        performed_by="Mechanical Technician",
    ),
    # === SCHEDULED ===
    _maint(
        "MLOG-012",
        "MACH-GRAN-001",
        "Rapid Mixer Granulator",
        "Preventive",
        "Scheduled",
        "Medium",
        "Next quarterly PM — full inspection and seal replacement",
        scheduled_start=HOURS_FROM(48),
        scheduled_end=HOURS_FROM(52),
        performed_by="Mechanical Technician",
        work_order_id="PM-RMG-001",
    ),
    _maint(
        "MLOG-013",
        "MACH-DRY-001",
        "Fluid Bed Dryer",
        "Preventive",
        "Scheduled",
        "Medium",
        "Monthly PM — finger bag and filter inspection",
        scheduled_start=HOURS_FROM(72),
        scheduled_end=HOURS_FROM(75),
        performed_by="Mechanical Technician",
        work_order_id="PM-FBD-001",
    ),
    _maint(
        "MLOG-014",
        "MACH-COMP-001",
        "Rotary Tablet Press",
        "Preventive",
        "Scheduled",
        "High",
        "Weekly PM — punch/die inspection and lubrication",
        scheduled_start=HOURS_FROM(24),
        scheduled_end=HOURS_FROM(26),
        performed_by="Mechanical Technician",
        work_order_id="PM-TBP-001",
    ),
    _maint(
        "MLOG-015",
        "MACH-COAT-001",
        "Auto Coater",
        "Preventive",
        "Scheduled",
        "Medium",
        "Monthly PM — spray system and drive inspection",
        scheduled_start=HOURS_FROM(96),
        scheduled_end=HOURS_FROM(99),
        performed_by="Mechanical Technician",
        work_order_id="PM-ACT-001",
    ),
    # === INSPECTION ===
    _maint(
        "MLOG-016",
        "MACH-BLEND-001",
        "Octagonal Blender",
        "Inspection",
        "Completed",
        "Low",
        "Routine inspection — all safety guards verified, emergency stop tested, interlocks checked",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        actual_start=DAYS_AGO(7),
        actual_end=DAYS_AGO(7) + timedelta(minutes=30),
    ),
    _maint(
        "MLOG-017",
        "MACH-PACK-001",
        "Blister Packing Machine",
        "Inspection",
        "Completed",
        "Low",
        "Routine inspection — heating elements verified, conveyor chain tension checked",
        performed_by="Mechanical Technician",
        reviewed_by="Line Supervisor",
        actual_start=DAYS_AGO(14),
        actual_end=DAYS_AGO(14) + timedelta(minutes=25),
    ),
    # === DELAYED ===
    _maint(
        "MLOG-018",
        "MACH-FILL-001",
        "Capsule Filling Machine",
        "Preventive",
        "Delayed",
        "Medium",
        "Monthly PM delayed — awaiting replacement parts for dosing disc",
        issue="Dosing disc wear exceeded tolerance, replacement part on order",
        scheduled_start=DAYS_AGO(5),
        scheduled_end=DAYS_AGO(5) + timedelta(hours=3),
        performed_by="Mechanical Technician",
        work_order_id="PM-CFM-001",
    ),
]


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Clearing existing maintenance logs...")
    await db["maintenance_logs"].delete_many({})

    print(f"Inserting {len(MAINTENANCE_LOGS)} maintenance logs...")
    for log in MAINTENANCE_LOGS:
        await db["maintenance_logs"].insert_one(dict(log))

    count = await db["maintenance_logs"].count_documents({})
    completed = await db["maintenance_logs"].count_documents({"status": "Completed"})
    in_progress = await db["maintenance_logs"].count_documents({"status": "In Progress"})
    scheduled = await db["maintenance_logs"].count_documents({"status": "Scheduled"})
    delayed = await db["maintenance_logs"].count_documents({"status": "Delayed"})
    print(f"\nDone. {count} maintenance logs seeded:")
    print(f"  Completed: {completed}")
    print(f"  In Progress: {in_progress}")
    print(f"  Scheduled: {scheduled}")
    print(f"  Delayed: {delayed}")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
