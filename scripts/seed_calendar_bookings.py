#!/usr/bin/env python3
"""Seed calendar bookings for users with work order assignments."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

NOW = datetime.now(timezone.utc)


def _booking(title, start_hours, duration_hours, booked_by, wo_id=None, description=None):
    start = NOW + timedelta(hours=start_hours)
    end = start + timedelta(hours=duration_hours)
    return {
        "_id": str(uuid4()),
        "id": str(uuid4()),
        "calendar_id": f"cal-{booked_by.lower().replace(' ', '-')}",
        "title": title,
        "start_at": start,
        "end_at": end,
        "booked_by": booked_by,
        "attendee_ids": [],
        "description": description or f"Work order: {wo_id}" if wo_id else description,
        "status": "booked",
    }


# Work order bookings — schedule based on work order type and status
BOOKINGS = [
    # === MECHANICAL TECHNICIAN ===
    _booking(
        "RMG Quarterly PM — PM-RMG-001",
        8,
        4,
        "Mechanical Technician",
        "PM-RMG-001",
        "Quarterly preventive maintenance for Rapid Mixer Granulator",
    ),
    _booking(
        "FBD Monthly PM — PM-FBD-001",
        14,
        3,
        "Mechanical Technician",
        "PM-FBD-001",
        "Monthly preventive maintenance for Fluid Bed Dryer",
    ),
    _booking(
        "Blender Quarterly PM — PM-BLD-001",
        24,
        3,
        "Mechanical Technician",
        "PM-BLD-001",
        "Quarterly preventive maintenance for Octagonal Blender",
    ),
    _booking(
        "Tablet Press Weekly PM — PM-TBP-001",
        32,
        2,
        "Mechanical Technician",
        "PM-TBP-001",
        "Weekly preventive maintenance for Rotary Tablet Press",
    ),
    _booking(
        "Tablet Press Monthly PM — PM-TBP-002",
        36,
        4,
        "Mechanical Technician",
        "PM-TBP-002",
        "Monthly preventive maintenance for Rotary Tablet Press",
    ),
    _booking(
        "Auto Coater Monthly PM — PM-ACT-001",
        48,
        3,
        "Mechanical Technician",
        "PM-ACT-001",
        "Monthly preventive maintenance for Auto Coater",
    ),
    _booking(
        "Dust Collector Monthly PM — PM-DCU-001",
        56,
        2,
        "Mechanical Technician",
        "PM-DCU-001",
        "Monthly preventive maintenance for Dust Collection System",
    ),
    _booking(
        "Cartoning Machine Monthly PM — PM-CTM-001",
        60,
        2,
        "Mechanical Technician",
        "PM-CTM-001",
        "Monthly preventive maintenance for Cartoning Machine",
    ),
    _booking(
        "Breakdown — RMG (CM-001)",
        4,
        6,
        "Maintenance Engineer",
        "CM-001",
        "Emergency repair for RMG — fault identification and repair",
    ),
    _booking(
        "Emergency Repair — Tablet Press (CM-002)",
        12,
        4,
        "Maintenance Engineer",
        "CM-002",
        "Emergency repair for Tablet Press — LOTO, diagnose, repair",
    ),
    # === HVAC TECHNICIAN ===
    _booking(
        "AHU Monthly PM — PM-AHU-001",
        10,
        3,
        "HVAC Technician",
        "PM-AHU-001",
        "Monthly preventive maintenance for AHU",
    ),
    _booking(
        "AHU HEPA Annual Replacement — PM-AHU-002",
        16,
        2,
        "HVAC Technician",
        "PM-AHU-002",
        "Annual HEPA filter replacement and DOP test",
    ),
    _booking(
        "LAF Booth Monthly PM — PM-LAF-001",
        20,
        2,
        "HVAC Technician",
        "PM-LAF-001",
        "Monthly preventive maintenance for LAF Sampling Booth",
    ),
    # === QC LAB TECHNICIAN — CALIBRATIONS ===
    _booking(
        "Analytical Balance Calibration — CAL-BAL-001",
        8,
        2,
        "QC Lab Technician",
        "CAL-BAL-001",
        "6-monthly calibration of analytical balance at Weighing Booth",
    ),
    _booking(
        "Analytical Balance Calibration — CAL-BAL-002",
        10,
        2,
        "QC Lab Technician",
        "CAL-BAL-002",
        "6-monthly calibration of analytical balance at IPQC Station",
    ),
    _booking(
        "Moisture Analyzer Calibration — CAL-MOI-001",
        12,
        2,
        "QC Lab Technician",
        "CAL-MOI-001",
        "Annual calibration of Moisture Analyzer",
    ),
    _booking(
        "LOD Analyzer Calibration — CAL-LOD-001",
        14,
        2,
        "QC Lab Technician",
        "CAL-LOD-001",
        "6-monthly calibration of LOD Moisture Analyzer at FBD",
    ),
    _booking(
        "Thermometer Calibration — CAL-THM-001",
        16,
        1,
        "QC Lab Technician",
        "CAL-THM-001",
        "Annual 3-point calibration of thermometer at Binder Prep",
    ),
    _booking(
        "Compression Force Sensor Calibration — CAL-PRE-001",
        18,
        2,
        "QC Lab Technician",
        "CAL-PRE-001",
        "6-monthly calibration of compression force sensor",
    ),
    _booking(
        "Hardness Tester Calibration — CAL-HRD-001",
        20,
        2,
        "QC Lab Technician",
        "CAL-HRD-001",
        "6-monthly calibration of hardness tester",
    ),
    _booking(
        "Friability Tester Calibration — CAL-FRB-001",
        22,
        1,
        "QC Lab Technician",
        "CAL-FRB-001",
        "Annual calibration of friability tester",
    ),
    _booking(
        "Disintegration Tester Calibration — CAL-DIS-001",
        24,
        1,
        "QC Lab Technician",
        "CAL-DIS-001",
        "Annual calibration of disintegration tester",
    ),
    _booking(
        "Viscometer Calibration — CAL-VIS-001",
        26,
        1,
        "QC Lab Technician",
        "CAL-VIS-001",
        "Annual calibration of viscometer at Solution Prep",
    ),
    _booking(
        "DP Gauge (AHU) Calibration — CAL-DPG-001",
        28,
        1,
        "QC Lab Technician",
        "CAL-DPG-001",
        "Annual calibration of differential pressure gauge at AHU",
    ),
    _booking(
        "DP Gauge (LAF) Calibration — CAL-DPG-002",
        30,
        1,
        "QC Lab Technician",
        "CAL-DPG-002",
        "Annual calibration of differential pressure gauge at LAF",
    ),
    _booking(
        "Fill Weight Sensor Calibration — CAL-WGT-001",
        32,
        2,
        "QC Lab Technician",
        "CAL-WGT-001",
        "6-monthly calibration of fill weight sensor at Bottle Filler",
    ),
    _booking(
        "Checkweigher Calibration — CAL-CWG-001",
        34,
        2,
        "QC Lab Technician",
        "CAL-CWG-001",
        "6-monthly calibration of checkweigher at Cartoning",
    ),
    _booking(
        "Vision System Calibration — CAL-VIS-001B",
        36,
        2,
        "QC Lab Technician",
        "CAL-VIS-001B",
        "6-monthly calibration of vision inspection system at Blister",
    ),
    _booking(
        "Torque Wrench Calibration — CAL-TWR-001",
        38,
        1,
        "QC Lab Technician",
        "CAL-TWR-001",
        "Annual calibration of torque wrench set",
    ),
    _booking(
        "Multimeter Calibration — CAL-MMT-001",
        40,
        1,
        "QC Lab Technician",
        "CAL-MMT-001",
        "Annual calibration of multimeter",
    ),
    # === PRODUCTION OPERATORS ===
    _booking(
        "Material Dispensing — PRD-001",
        0,
        2,
        "Dispensing Operator",
        "PRD-001",
        "Dispensing operation for current batch",
    ),
    _booking(
        "Sifting / Pre-milling — PRD-002",
        2,
        2,
        "Milling Operator",
        "PRD-002",
        "Sifting and pre-milling operation",
    ),
    _booking(
        "Granulation — PRD-003",
        4,
        3,
        "Granulation Operator",
        "PRD-003",
        "Wet granulation process",
    ),
    _booking(
        "Drying (FBD) — PRD-004",
        7,
        4,
        "Drying Operator",
        "PRD-004",
        "Fluid bed drying process — in progress",
    ),
    _booking(
        "Sizing / Post-dry Milling — PRD-005",
        11,
        2,
        "Milling Operator",
        "PRD-005",
        "Post-dry sizing and milling",
    ),
    _booking(
        "Blending — PRD-006",
        13,
        2,
        "Blending Operator",
        "PRD-006",
        "Blending operation for current batch",
    ),
    _booking(
        "Lubrication — PRD-007",
        15,
        1,
        "Blending Operator",
        "PRD-007",
        "Lubrication step",
    ),
    _booking(
        "Compression — PRD-008",
        16,
        4,
        "Compression Operator",
        "PRD-008",
        "Tablet compression process",
    ),
    _booking(
        "Coating — PRD-009",
        20,
        3,
        "Coating Operator",
        "PRD-009",
        "Film coating process",
    ),
    _booking(
        "Tablet Inspection — PRD-010",
        23,
        2,
        "Inspection Operator",
        "PRD-010",
        "Visual inspection and metal detection",
    ),
    _booking(
        "Capsule Filling — PRD-011",
        25,
        3,
        "Capsule Filling Operator",
        "PRD-011",
        "Capsule filling process on Capsule Line B",
    ),
    _booking(
        "Capsule Polishing — PRD-012",
        28,
        1,
        "Capsule Operator",
        "PRD-012",
        "Capsule polishing and dedusting",
    ),
    _booking(
        "Capsule Inspection — PRD-013",
        29,
        2,
        "Inspection Operator",
        "PRD-013",
        "Visual inspection for capsules",
    ),
    _booking(
        "Primary Packaging — Blister — PRD-014",
        31,
        3,
        "Packaging Operator",
        "PRD-014",
        "Blister packaging operation",
    ),
    _booking(
        "Primary Packaging — Bottle — PRD-015",
        34,
        2,
        "Packaging Operator",
        "PRD-015",
        "Bottle filling and packaging",
    ),
    _booking(
        "Secondary Packaging — PRD-016",
        36,
        3,
        "Packaging Operator",
        "PRD-016",
        "Cartoning and labelling",
    ),
]


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Clearing existing calendar bookings...")
    await db["calendar_bookings"].delete_many({})

    print(f"Inserting {len(BOOKINGS)} calendar bookings...")
    for booking in BOOKINGS:
        await db["calendar_bookings"].insert_one(dict(booking))
    print(f"  + {len(BOOKINGS)} bookings inserted")

    count = await db["calendar_bookings"].count_documents({})
    users = await db["calendar_bookings"].distinct("booked_by")
    print(f"\nDone. {count} bookings for {len(users)} users:")
    for user in users:
        c = await db["calendar_bookings"].count_documents({"booked_by": user})
        print(f"  {user}: {c} bookings")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
