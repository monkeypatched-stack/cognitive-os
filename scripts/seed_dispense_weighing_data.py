#!/usr/bin/env python3
"""Seed GMP weighing and dispensing data into MongoDB and validate against DispenseWeighingExecution model."""

import asyncio
import random
import sys
import uuid
from datetime import datetime, timezone, timedelta

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings
from services.workorders.models.batch_execution_records import (
    DispenseWeighingExecution,
    DispenseWeighingStatus,
)


def utc_now():
    return datetime.now(timezone.utc)


async def seed_dispense_weighing_data(db):
    collection = db["dispense_weighing_records"]
    now = utc_now()

    # We need some existing batch execution records to link to
    # For the purpose of this seed script, we will generate some IDs
    # In a real scenario, we would fetch these from the production_batches or batch_execution_records collection

    # Let's try to fetch some existing batch_execution_record_ids if possible
    batch_records_cursor = db["batch_execution_records"].find({}, {"batch_execution_record_id": 1}).limit(50)
    batch_records = await batch_records_cursor.to_list(length=50)
    batch_ids = [r["batch_execution_record_id"] for r in batch_records]

    if not batch_ids:
        print("No existing batch execution records found. Generating dummy IDs for seeding.")
        batch_ids = [str(uuid.uuid4()) for _ in range(20)]

    materials = [
        {"id": "MAT-001", "name": "Active Pharmaceutical Ingredient A", "unit": "kg"},
        {"id": "MAT-002", "name": "Excipient B", "unit": "kg"},
        {"id": "MAT-003", "name": "Solvent C", "unit": "L"},
        {"id": "MAT-004", "name": "Stabilizer D", "unit": "kg"},
        {"id": "MAT-005", "name": "Colorant E", "unit": "g"},
    ]

    scales = ["SCALE-01", "SCALE-02", "SCALE-03", "SCALE-04"]
    statuses = [
        "Planned",
        "In Progress",
        "Weighed",
        "Verified",
        "Rejected",
        "Cancelled",
    ]

    seeded_count = 0
    validation_errors = 0

    for i in range(100):
        material = random.choice(materials)
        status = random.choice(statuses)

        # Generate data that satisfies Pydantic validation
        target_quantity = round(random.uniform(1.0, 100.0), 3)
        lower_tolerance = round(target_quantity * 0.02, 3)  # 2% tolerance
        upper_tolerance = round(target_quantity * 0.02, 3)

        tare_weight = round(random.uniform(0.1, 2.0), 3)

        # Calculate net_weight based on status
        net_weight = None
        gross_weight = None

        if status in ["Weighed", "Verified"]:
            # Simulate a value within or slightly outside tolerance
            variation = random.uniform(-lower_tolerance * 1.1, upper_tolerance * 1.1)
            net_weight = round(target_quantity + variation, 3)
            gross_weight = round(net_weight + tare_weight, 3)

        # Validation requirements for "Verified" status
        verified_by = None
        verified_at = None
        signature_ids = []
        evidence_document_ids = []
        deviation_id = None

        weighed_by = None
        weighed_at = None

        if status in ["Weighed", "Verified"]:
            weighed_by = random.choice(
                [
                    "Karan Shah",
                    "Vivek Raman",
                    "Rohan Kulkarni",
                    "Neha Singh",
                    "Arjun Mehta",
                ]
            )
            weighed_at = now - timedelta(hours=random.randint(1, 24))

        if status == "Verified":
            verified_by = random.choice(["Rohan Kulkarni", "Neha Singh", "Arjun Mehta"])
            verified_at = weighed_at + timedelta(minutes=random.randint(10, 60))
            signature_ids = [str(uuid.uuid4())]
            evidence_document_ids = [f"DOC-EVID-{uuid.uuid4().hex[:8]}"]

            # Check if it's out of tolerance to decide if deviation_id is needed
            # within_tolerance logic: lower <= net_weight <= upper
            lower_bound = target_quantity - lower_tolerance
            upper_bound = target_quantity + upper_tolerance
            if net_weight is not None and not (lower_bound <= net_weight <= upper_bound):
                deviation_id = f"DEV-{uuid.uuid4().hex[:6].upper()}"

        data = {
            "dispense_weighing_id": str(uuid.uuid4()),
            "process_step_id": f"STEP-DISP-{random.randint(1, 10)}",
            "bmr_document_id": f"BMR-DOC-{random.randint(1000, 9999)}",
            "material_id": material["id"],
            "material_name": material["name"],
            "material_lot_id": f"LOT-{random.randint(10000, 99999)}",
            "target_quantity": target_quantity,
            "lower_tolerance": lower_tolerance,
            "upper_tolerance": upper_tolerance,
            "tare_weight": tare_weight,
            "gross_weight": gross_weight,
            "net_weight": net_weight,
            "unit": material["unit"],
            "scale_equipment_id": random.choice(scales),
            "status": status,
            "weighed_by": weighed_by,
            "weighed_at": weighed_at,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "evidence_document_ids": evidence_document_ids,
            "signature_ids": signature_ids,
            "deviation_id": deviation_id,
            "comments": "Seeded GMP data" if i % 10 == 0 else None,
            "metadata": {"seed_index": i},
        }

        try:
            # Validate against Pydantic model
            validated_record = DispenseWeighingExecution(**data)

            # Insert into MongoDB
            await collection.update_one(
                {"dispense_weighing_id": validated_record.dispense_weighing_id},
                {"$set": validated_record.model_dump()},
                upsert=True,
            )
            seeded_count += 1
        except ValidationError as e:
            print(f"Validation error for record {i}: {e}")
            validation_errors += 1

    return seeded_count, validation_errors


async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Starting seeding of GMP weighing and dispensing data...")
    seeded, errors = await seed_dispense_weighing_data(db)
    print(f"Successfully seeded {seeded} records.")
    if errors > 0:
        print(f"Encountered {errors} validation errors.")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
