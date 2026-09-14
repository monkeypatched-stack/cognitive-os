#!/usr/bin/env python3
"""Seed batch step executions with realistic varied data."""

import asyncio
import random
import sys
from datetime import datetime, timezone, timedelta

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings


def utc_now():
    return datetime.now(timezone.utc)


async def seed_batch_step_executions(db):
    collection = db["batch_step_executions"]
    now = utc_now()
    # Seed 50 realistic batch step executions
    executions = []
    statuses = ["Planned", "In Progress", "Completed", "Failed", "Cancelled"]
    step_names = [
        "Raw Material Dispensing",
        "Mixing",
        "Granulation",
        "Drying",
        "Compaction",
        "Tablet Pressing",
        "Packaging",
        "Quality Inspection",
        "Cleaning",
        "Documentation",
    ]
    operators = [
        "Karan Shah",
        "Vivek Raman",
        "Rohan Kulkarni",
        "Neha Singh",
        "Arjun Mehta",
    ]
    # Generate a set of batch execution record IDs and batch IDs
    batch_execution_record_ids = [f"BER-{i + 1:03d}" for i in range(10)]
    batch_ids = [f"BATCH-{i + 1:03d}" for i in range(10)]
    # Generate records
    for i in range(50):
        exec_id = f"BSE-{i + 1:03d}"
        batch_execution_record_id = random.choice(batch_execution_record_ids)
        batch_id = random.choice(batch_ids)
        step_name = random.choice(step_names)
        status = random.choice(statuses)
        # Create timestamps
        if status == "Completed":
            # Completed steps have both started_at and completed_at
            started_at = now - timedelta(days=random.randint(0, 10), hours=random.randint(0, 23))
            completed_at = started_at + timedelta(hours=random.randint(1, 8))
            duration_minutes = int((completed_at - started_at).total_seconds() / 60)
        elif status == "In Progress":
            started_at = now - timedelta(hours=random.randint(0, 24))
            completed_at = None
            duration_minutes = None
        elif status == "Planned":
            started_at = None
            completed_at = None
            duration_minutes = None
        else:  # Failed, Cancelled
            started_at = now - timedelta(days=random.randint(0, 10))
            completed_at = None
            duration_minutes = None
        # Additional fields based on status
        signature_ids = []
        exception_notes = None
        deviation_id = None
        if status == "Completed":
            # For completed steps, include a signature
            signature_ids = [f"SIG-{random.randint(100, 999)}"]
        elif status == "Failed":
            exception_notes = f"Deviation detected: {random.choice(['Temperature', 'Pressure', 'Quality'])} out of spec"
            deviation_id = f"DEV-{random.randint(100, 999)}"
        # Construct the document
        exec_doc = {
            "batch_step_execution_id": exec_id,
            "batch_execution_record_id": batch_execution_record_id,
            "batch_id": batch_id,
            "step_name": step_name,
            "status": status,
            "operator_name": random.choice(operators),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_minutes": duration_minutes,
            "signature_ids": signature_ids,
            "exception_notes": exception_notes,
            "deviation_id": deviation_id,
        }
        executions.append(exec_doc)
    # Insert all executions
    for exec_doc in executions:
        await collection.update_one(
            {"batch_step_execution_id": exec_doc["batch_step_execution_id"]},
            {"$set": exec_doc},
            upsert=True,
        )
    return len(executions)


async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]
    count = await seed_batch_step_executions(db)
    print(f"Inserted/updated {count} batch step executions")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
