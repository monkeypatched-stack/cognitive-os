#!/usr/bin/env python3
"""Seed missing data for golden question tests.

Adds: workers (Aarav Mehta, Rahul Deshmukh), work orders, batches,
change controls, and machine aliases needed by the 432-question set.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "demo")


async def seed():
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo = AsyncIOMotorClient(MONGO_URI)
    db = mongo[DB_NAME]

    # ── 1. Workers ──────────────────────────────────────────────────
    workers_to_add = [
        {
            "worker_id": "WRK-AARAV-001",
            "name": "Aarav Mehta",
            "role": "operator",
            "title": "Production Worker",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-04",
            "stage_name": "Blending",
            "workstation_id": "WS-TAB-06-01",
            "workstation_name": "Blend Preparation Workstation",
            "reports_to": "WRK-RAHUL-001",
            "reports_to_name": "Rahul Deshmukh",
            "shift": "Day",
            "status": "active",
        },
        {
            "worker_id": "WRK-RAHUL-001",
            "name": "Rahul Deshmukh",
            "role": "line_manager",
            "title": "Line Manager",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "reports_to": None,
            "reports_to_name": None,
            "status": "active",
        },
    ]
    for w in workers_to_add:
        existing = await db.workers.find_one({"worker_id": w["worker_id"]})
        if not existing:
            await db.workers.insert_one(w)
            print(f"  + worker: {w['name']} ({w['role']})")
        else:
            await db.workers.update_one({"worker_id": w["worker_id"]}, {"$set": w})
            print(f"  ~ worker: {w['name']} (updated)")

    # ── 2. Work Orders ──────────────────────────────────────────────
    wo_to_add = [
        {
            "work_order_id": "WO-MAINT-2026-0042",
            "title": "V-Blender VB-01 Vibration Anomaly",
            "status": "Open",
            "priority": "High",
            "type": "Corrective Maintenance",
            "machine_id": "MCH-TAB-06-01",
            "machine_name": "Octagonal / Double Cone Blender",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-04",
            "stage_name": "Blending",
            "workstation_id": "WS-TAB-06-01",
            "description": "V-Blender VB-01 showing abnormal vibration levels above threshold. Requires immediate inspection and corrective action.",
            "assigned_to": "WRK-AARAV-001",
            "assigned_to_name": "Aarav Mehta",
            "created_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
            "expected_completion": datetime(2026, 6, 15, tzinfo=timezone.utc),
        },
        {
            "work_order_id": "WO-MAINT-2026-0050",
            "title": "PM - RMG-01 Quarterly Maintenance",
            "status": "In Progress",
            "priority": "Medium",
            "type": "Preventive Maintenance",
            "machine_id": "MCH-TAB-03-01",
            "machine_name": "Rapid Mixer Granulator",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-03",
            "stage_name": "Granulation",
            "description": "Quarterly preventive maintenance for Rapid Mixer Granulator RMG-01.",
            "created_at": datetime(2026, 6, 8, tzinfo=timezone.utc),
            "expected_completion": datetime(2026, 6, 12, tzinfo=timezone.utc),
        },
    ]
    for wo in wo_to_add:
        existing = await db.work_orders.find_one({"work_order_id": wo["work_order_id"]})
        if not existing:
            await db.work_orders.insert_one(wo)
            print(f"  + work order: {wo['work_order_id']}")
        else:
            await db.work_orders.update_one({"work_order_id": wo["work_order_id"]}, {"$set": wo})
            print(f"  ~ work order: {wo['work_order_id']} (updated)")

    # ── 3. Batches ──────────────────────────────────────────────────
    batches_to_add = [
        {
            "batch_id": "BATCH-2026-001",
            "batch_number": "BATCH-2026-001",
            "product_name": "Paracetamol 650mg Tablets",
            "product_code": "PAR-650-TAB",
            "status": "In Progress",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-04",
            "stage_name": "Blending",
            "planned_quantity": 100000,
            "actual_quantity": 0,
            "yield_percentage": 0,
            "start_date": datetime(2026, 6, 10, tzinfo=timezone.utc),
            "created_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
        },
        {
            "batch_id": "BATCH-2026-002",
            "batch_number": "BATCH-2026-002",
            "product_name": "Ibuprofen 400mg Tablets",
            "product_code": "IBU-400-TAB",
            "status": "In Progress",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-06",
            "stage_name": "Compression",
            "planned_quantity": 80000,
            "actual_quantity": 0,
            "yield_percentage": 0,
            "start_date": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
        },
    ]
    for b in batches_to_add:
        existing = await db.batch_production_execution_records.find_one({"batch_id": b["batch_id"]})
        if not existing:
            await db.batch_production_execution_records.insert_one(b)
            print(f"  + batch: {b['batch_id']}")
        else:
            await db.batch_production_execution_records.update_one({"batch_id": b["batch_id"]}, {"$set": b})
            print(f"  ~ batch: {b['batch_id']} (updated)")

    # ── 4. Change Controls ──────────────────────────────────────────
    try:
        await db.create_collection("change_controls")
    except Exception:
        pass  # collection may already exist
    cc_to_add = [
        {
            "change_control_id": "CC-2026-0008",
            "title": "Increase Granulation Impeller Speed to 180 RPM",
            "status": "Open",
            "type": "Process Change",
            "line_id": "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": "STAGE-03",
            "stage_name": "Granulation",
            "description": "Proposed increase of impeller speed from 150 RPM to 180 RPM for Wet Granulation SOP. Requires validation batch and QA approval.",
            "requested_by": "WRK-RAHUL-001",
            "requested_by_name": "Rahul Deshmukh",
            "affected_batches": ["BATCH-2026-001", "BATCH-2026-002"],
            "created_at": datetime(2026, 6, 9, tzinfo=timezone.utc),
        },
    ]
    for cc in cc_to_add:
        existing = await db.change_controls.find_one({"change_control_id": cc["change_control_id"]})
        if not existing:
            await db.change_controls.insert_one(cc)
            print(f"  + change control: {cc['change_control_id']}")
        else:
            await db.change_controls.update_one({"change_control_id": cc["change_control_id"]}, {"$set": cc})
            print(f"  ~ change control: {cc['change_control_id']} (updated)")

    # ── 5. Machine Aliases ──────────────────────────────────────────
    # Add alias names so questions about "V-Blender VB-01" find the right machine
    result = await db.pharmaceutical_machines.update_one(
        {"machine_id": "MCH-TAB-06-01"},
        {
            "$set": {
                "alias": "V-Blender VB-01",
                "serial_number": "VB-01",
                "manufacturer": "Glatt",
                "model": "Bin Blender 600L",
                "location": "Blending Stage, Tablet Line A",
            }
        },
    )
    print(f"  ~ machine MCH-TAB-06-01: alias=V-Blender VB-01 (matched={result.matched_count})")

    result = await db.pharmaceutical_machines.update_one(
        {"machine_id": "MCH-TAB-03-01"},
        {
            "$set": {
                "alias": "Rapid Mixer Granulator RMG-01",
                "serial_number": "RMG-01",
                "manufacturer": "Lodha",
                "model": "LG-150",
                "location": "Granulation Stage, Tablet Line A",
            }
        },
    )
    print(f"  ~ machine MCH-TAB-03-01: alias=RMG-01 (matched={result.matched_count})")

    # ── Summary ─────────────────────────────────────────────────────
    print("\n--- Summary ---")
    for c in [
        "workers",
        "work_orders",
        "batch_production_execution_records",
        "change_controls",
    ]:
        count = await db[c].count_documents({})
        print(f"  {c}: {count}")

    mongo.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(seed())
