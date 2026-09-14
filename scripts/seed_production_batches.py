#!/usr/bin/env python3
"""Seed production batches, execution records, and batch step executions with full data."""

import sys
import uuid
from datetime import datetime, timezone, timedelta
import random

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

NOW = datetime.now(timezone.utc)

PRODUCTS = [
    ("PRD-TAB-001", "Paracetamol 500mg Tablets"),
    ("PRD-TAB-002", "Amoxicillin 250mg Capsules"),
    ("PRD-TAB-003", "Metformin 500mg Tablets"),
    ("PRD-TAB-004", "Atorvastatin 10mg Tablets"),
    ("PRD-TAB-005", "Cetirizine 10mg Tablets"),
]

LINES = ["LINE-TAB-001", "LINE-CAP-001"]
STAGE_IDS = {
    "LINE-TAB-001": [
        "STAGE-01",
        "STAGE-03",
        "STAGE-02",
        "STAGE-09",
        "STAGE-04",
        "STAGE-05",
        "STAGE-06",
        "STAGE-08",
    ],
    "LINE-CAP-001": [
        "STAGE-CAP-01",
        "STAGE-CAP-02",
        "STAGE-CAP-03",
        "STAGE-CAP-04",
        "STAGE-CAP-06",
        "STAGE-CAP-07",
        "STAGE-CAP-10",
    ],
}
WORKSTATIONS = {
    "LINE-TAB-001": [
        "WS-TAB-01-01",
        "WS-TAB-02-01",
        "WS-TAB-03-01",
        "WS-TAB-04-01",
        "WS-TAB-05-01",
        "WS-TAB-06-01",
        "WS-TAB-07-01",
        "WS-TAB-08-01",
    ],
    "LINE-CAP-001": [
        "WS-CAP-01-01",
        "WS-CAP-02-01",
        "WS-CAP-03-01",
        "WS-CAP-04-01",
        "WS-CAP-06-01",
        "WS-CAP-07-01",
        "WS-CAP-10-01",
    ],
}
MACHINES = {
    "LINE-TAB-001": [
        "MCH-TAB-01-01",
        "MCH-TAB-02-01",
        "MCH-TAB-03-01",
        "MCH-TAB-04-01",
        "MCH-TAB-06-01",
        "MCH-TAB-07-01",
        "MCH-TAB-08-02",
        "MCH-TAB-10-01",
    ],
    "LINE-CAP-001": [
        "MCH-CAP-01-01",
        "MCH-CAP-02-01",
        "MCH-CAP-03-01",
        "MCH-CAP-04-01",
        "MCH-CAP-06-01",
        "MCH-CAP-07-02",
        "MCH-CAP-10-01",
    ],
}

STEP_NAMES = [
    "Raw Material Dispensing",
    "Granulation",
    "Drying",
    "Milling",
    "Blending",
    "Compression",
    "Film Coating",
    "Packaging",
    "Quality Inspection",
    "Documentation & Review",
]

STEP_ACTUALS = {
    "Raw Material Dispensing": {
        "weight_kg": 25.0,
        "material_lot": "LOT-RM-001",
        "dispensed_by": "Verified",
    },
    "Granulation": {
        "granule_moisture_pct": 2.5,
        "batch_temp_celsius": 55,
        "duration_min": 45,
    },
    "Drying": {
        "moisture_pct": 0.8,
        "inlet_temp_celsius": 65,
        "outlet_temp_celsius": 42,
        "duration_min": 120,
    },
    "Milling": {"particle_size_micron": 150, "sieve_size": 30, "yield_pct": 97.5},
    "Blending": {"blend_time_min": 20, "rpm": 25, "blend_uniformity_rsd": 1.2},
    "Compression": {
        "hardness_kp": 8.5,
        "thickness_mm": 6.2,
        "weight_mg": 502,
        "friability_pct": 0.3,
        "disintegration_sec": 180,
    },
    "Film Coating": {
        "coat_weight_gain_pct": 3.2,
        "spray_rate_g_min": 12.0,
        "pan_speed_rpm": 8,
        "inlet_temp_celsius": 70,
    },
    "Packaging": {
        "blister_seal_temp_celsius": 135,
        "pack_rate_per_min": 120,
        "leaflet_count": 500,
    },
    "Quality Inspection": {
        "hardness_avg": 8.4,
        "weight_avg_mg": 501,
        "disintegration_avg_sec": 175,
        "moisture_pct": 0.7,
    },
    "Documentation & Review": {"reviewer": "QA Manager", "review_status": "Approved"},
}

OPERATORS = [
    ("USER-OP-001", "Priya Sharma"),
    ("USER-OP-002", "Karan Shah"),
    ("USER-OP-003", "Rajiv Malhotra"),
    ("USER-OP-004", "Ananya Desai"),
    ("USER-OP-005", "Sneha Patel"),
    ("USER-OP-006", "Devansh Kapoor"),
]

VERIFIERS = [
    ("USER-VF-001", "Dr. Meera Iyer"),
    ("USER-VF-002", "Ravi Kumar"),
    ("USER-VF-003", "Sanjay Gupta"),
]

WO_IDS = (
    [f"PM-RMG-00{i}" for i in range(1, 10)]
    + [f"PM-FBD-00{i}" for i in range(1, 5)]
    + [f"PM-TBP-00{i}" for i in range(1, 6)]
)

STATUS_PROGRESSION = {
    "Planned": ("Planned", None, None),
    "Created": ("Created", None, None),
    "Released": ("Released", None, None),
    "Dispensing": ("In Progress", timedelta(hours=0), None),
    "In Production": ("In Progress", timedelta(hours=0), None),
    "On Hold": ("In Progress", timedelta(hours=0), None),
    "Completed": ("Completed", timedelta(hours=0), timedelta(hours=4)),
    "Under QA Review": ("Completed", timedelta(hours=0), timedelta(hours=4)),
    "Approved": ("Completed", timedelta(hours=0), timedelta(hours=4)),
    "Rejected": ("Completed", timedelta(hours=0), timedelta(hours=4)),
    "Cancelled": ("Planned", None, None),
}


def _uid():
    return f"U-{uuid.uuid4().hex[:8].upper()}"


async def seed_all(db):
    await db.production_batches.delete_many({"metadata.seed": True})
    await db.batch_production_execution_records.delete_many({"metadata.seed": True})
    await db.batch_step_executions.delete_many({"metadata.seed": True})

    print("Seeding production batches...")
    batches = []
    for i in range(50):
        product = random.choice(PRODUCTS)
        line = random.choice(LINES)
        batch_status = random.choice(
            [
                "Planned",
                "Created",
                "Released",
                "Dispensing",
                "In Production",
                "On Hold",
                "Completed",
                "Under QA Review",
                "Approved",
                "Cancelled",
            ]
        )
        batch_id = f"PB-{uuid.uuid4().hex[:8].upper()}"
        plan_start = NOW - timedelta(days=random.randint(0, 60))
        plan_end = plan_start + timedelta(hours=random.randint(24, 168))

        batch = {
            "batch_id": batch_id,
            "batch_number": f"BATCH-{i + 1:03d}",
            "product_id": product[0],
            "product_name": product[1],
            "process_definition_id": f"PD-{line.split('-')[1]}-001",
            "work_order_id": random.choice(WO_IDS) if random.random() > 0.3 else None,
            "plant_id": "PLANT-TBL-IN-001",
            "line_id": line,
            "current_stage_id": random.choice(STAGE_IDS[line]),
            "current_workstation_id": random.choice(WORKSTATIONS[line]),
            "status": batch_status,
            "qa_reviewer_id": ("USER-QA-001" if batch_status in ("Approved", "Closed") else None),
            "lifecycle_events": [
                {
                    "event_id": f"BLE-{uuid.uuid4().hex[:8].upper()}",
                    "event_type": "Created",
                    "status_to": batch_status,
                    "timestamp": plan_start.isoformat(),
                    "user": "admin@monkeypatched.com",
                },
            ],
            "batch_execution_record_ids": [],
            "planned_start_at": plan_start.isoformat(),
            "planned_end_at": plan_end.isoformat(),
            "started_at": (plan_start.isoformat() if batch_status not in ("Planned", "Created") else None),
            "completed_at": (plan_end.isoformat() if batch_status in ("Completed", "Approved", "Closed") else None),
            "released_at": (plan_start.isoformat() if batch_status not in ("Planned", "Created") else None),
            "metadata": {"seed": True, "estimated_quantity": random.randint(100, 5000)},
            "created_at": plan_start.isoformat(),
            "updated_at": NOW.isoformat(),
        }
        batches.append(batch)

    await db.production_batches.insert_many(batches)
    print(f"  {len(batches)} production batches")

    print("Seeding batch execution records...")
    exec_records = []
    for batch in batches[:30]:
        ber_id = f"BER-{uuid.uuid4().hex[:8].upper()}"
        ber = {
            "batch_execution_record_id": ber_id,
            "batch_id": batch["batch_id"],
            "process_definition_id": batch.get("process_definition_id", ""),
            "plant_id": "PLANT-TBL-IN-001",
            "line_id": batch["line_id"],
            "status": batch["status"],
            "batch_step_execution_ids": [],
            "metadata": {
                "seed": True,
                "batch_record_package": {"batch_step_execution_ids": []},
                "bmr_package": {"batch_step_execution_ids": []},
                "bpr_package": {"batch_step_execution_ids": []},
            },
            "created_at": batch["created_at"],
            "updated_at": NOW.isoformat(),
        }
        exec_records.append(ber)
        batch["batch_execution_record_ids"] = [ber_id]
        batch["active_batch_execution_record_id"] = ber_id

    await db.batch_production_execution_records.insert_many(exec_records)
    print(f"  {len(exec_records)} batch execution records")

    await db.production_batches.update_many(
        {"metadata.seed": True},
        {
            "$set": {
                "batch_execution_record_ids": [],
                "active_batch_execution_record_id": None,
            }
        },
    )
    for batch in batches[:30]:
        await db.production_batches.update_one(
            {"batch_id": batch["batch_id"]},
            {
                "$set": {
                    "batch_execution_record_ids": batch["batch_execution_record_ids"],
                    "active_batch_execution_record_id": batch["active_batch_execution_record_id"],
                }
            },
        )

    print("Seeding batch step executions...")
    steps = []
    ber_map = {er["batch_id"]: er["batch_execution_record_id"] for er in exec_records}

    for batch in batches[:30]:
        line = batch["line_id"]
        batch_status = batch["status"]
        ber_id = ber_map.get(batch["batch_id"], f"BER-{batch['batch_id'][-8:]}")

        num_steps = random.randint(5, min(len(STEP_NAMES), len(STAGE_IDS[line])))
        selected_steps = STEP_NAMES[:num_steps]

        for seq, step_name in enumerate(selected_steps, 1):
            stage_idx = min(seq - 1, len(STAGE_IDS[line]) - 1)
            machine_idx = min(seq - 1, len(MACHINES[line]) - 1)
            op = random.choice(OPERATORS)
            vf = random.choice(VERIFIERS)

            _, start_delta, end_delta = STATUS_PROGRESSION.get(batch_status, ("Planned", None, None))
            if start_delta is not None:
                if batch_status in ("Dispensing", "In Production", "On Hold"):
                    step_status = "Completed" if seq < num_steps else "In Progress"
                else:
                    step_status = "Completed"
            else:
                step_status = "Planned"

            step_start = step_end = None
            duration = None
            if step_status in ("In Progress", "Completed"):
                base = datetime.fromisoformat(batch.get("started_at", batch["created_at"]))
                step_start = (base + timedelta(hours=seq * 4)).isoformat()
            if step_status == "Completed":
                step_end_dt = datetime.fromisoformat(step_start) + timedelta(hours=random.randint(1, 6))
                step_end = step_end_dt.isoformat()
                duration = round(
                    (step_end_dt - datetime.fromisoformat(step_start)).total_seconds() / 60,
                    1,
                )

            sig_ids = []
            ev_ids = []
            audit_ids = []
            if step_status == "Completed":
                sig_ids = [f"SIG-{uuid.uuid4().hex[:6].upper()}"]
                ev_ids = [f"DOC-{uuid.uuid4().hex[:6].upper()}"] if random.random() > 0.4 else []
                audit_ids = [f"AUD-{uuid.uuid4().hex[:6].upper()}"]

            actuals = {}
            if step_status == "Completed":
                actuals = dict(STEP_ACTUALS.get(step_name, {}))

            exception_notes = None
            deviation_id = None
            if step_status == "Failed":
                exception_notes = "Parameter out of specification range"
                deviation_id = f"DEV-{uuid.uuid4().hex[:6].upper()}"

            step_doc = {
                "batch_step_execution_id": f"BSE-{uuid.uuid4().hex[:8].upper()}",
                "batch_execution_record_id": ber_id,
                "batch_id": batch["batch_id"],
                "process_definition_id": batch.get("process_definition_id", "PD-TAB-001"),
                "process_step_id": f"PS-{seq:03d}",
                "sequence": seq,
                "step_name": step_name,
                "plant_id": "PLANT-TBL-IN-001",
                "line_id": line,
                "stage_id": STAGE_IDS[line][stage_idx],
                "workstation_id": WORKSTATIONS[line][stage_idx],
                "equipment_ids": [],
                "machine_ids": [MACHINES[line][machine_idx]],
                "operator_id": op[0],
                "operator_name": op[1],
                "verifier_id": (vf[0] if step_status in ("Completed", "Failed") else None),
                "verifier_name": (vf[1] if step_status in ("Completed", "Failed") else None),
                "status": step_status,
                "started_at": step_start,
                "completed_at": step_end,
                "duration_minutes": duration,
                "actual_values": actuals,
                "exception_notes": exception_notes,
                "deviation_id": deviation_id,
                "executed_instruction_evidence_ids": [],
                "signature_ids": sig_ids,
                "evidence_document_ids": ev_ids,
                "audit_event_ids": audit_ids,
                "metadata": {"seed": True},
                "created_by": op[0],
                "created_at": batch["created_at"],
                "updated_at": NOW.isoformat(),
            }
            steps.append(step_doc)

    await db.batch_step_executions.insert_many(steps)
    print(f"  {len(steps)} batch step executions")


async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]
    await seed_all(db)
    client.close()
    print("Done.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
