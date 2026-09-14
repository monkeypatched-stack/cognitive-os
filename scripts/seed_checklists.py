#!/usr/bin/env python3
"""Seed checklist data for workers."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

NOW = datetime.now(timezone.utc)


def _checklist(checklist_id, title, description, owner_id, tags, items):
    return {
        "_id": checklist_id,
        "checklist_id": checklist_id,
        "title": title,
        "description": description,
        "owner_id": owner_id,
        "tags": tags,
        "items": items,
        "is_archived": False,
    }


def _item(item_id, wo_id, title, description, assigned_to, due_days=0, is_completed=False):
    return {
        "item_id": item_id,
        "work_order_id": wo_id,
        "title": title,
        "description": description,
        "assigned_to": assigned_to,
        "due_at": NOW + timedelta(days=due_days),
        "is_completed": is_completed,
    }


CHECKLISTS = [
    # === GRANULATION OPERATOR (Pallavi Joshi) ===
    _checklist(
        "CL-GRAN-001",
        "Granulation Daily Start-up Checklist",
        "Pre-start checks for granulation stage",
        "WRK-WS-009",
        ["granulation", "daily", "startup"],
        [
            _item(
                "CLI-001",
                "PM-RMG-001",
                "Verify RMG cleaning status",
                "Confirm RMG interior is clean and free from previous batch residue",
                "Pallavi Joshi",
                0,
                True,
            ),
            _item(
                "CLI-002",
                "PM-RMG-001",
                "Check impeller/chopper blades",
                "Visually inspect blades for wear or damage",
                "Pallavi Joshi",
                0,
                True,
            ),
            _item(
                "CLI-003",
                "PM-RMG-001",
                "Verify gearbox oil level",
                "Check oil sight glass — must be within green zone",
                "Pallavi Joshi",
                0,
                True,
            ),
            _item(
                "CLI-004",
                "PM-RMG-001",
                "Check binder spray nozzle",
                "Ensure nozzle is clean and unobstructed",
                "Pallavi Joshi",
                0,
                True,
            ),
            _item(
                "CLI-005",
                "PM-RMG-001",
                "Test RPM indicator",
                "Run RMG at low speed and verify RPM display matches setpoint",
                "Pallavi Joshi",
                0,
            ),
            _item(
                "CLI-006",
                "PM-RMG-001",
                "Inspect discharge valve",
                "Open/close discharge valve — must operate smoothly",
                "Pallavi Joshi",
                0,
            ),
            _item(
                "CLI-007",
                "PM-RMG-001",
                "Verify PPE is available",
                "Safety glasses, gloves, lab coat, ear protection",
                "Pallavi Joshi",
                0,
                True,
            ),
            _item(
                "CLI-008",
                "PM-RMG-001",
                "Record ambient temperature",
                "Must be 20-25°C before starting",
                "Pallavi Joshi",
                0,
            ),
        ],
    ),
    _checklist(
        "CL-GRAN-002",
        "Granulation Batch Completion Checklist",
        "Post-batch checks for granulation stage",
        "WRK-WS-009",
        ["granulation", "batch", "completion"],
        [
            _item(
                "CLI-010",
                "PRD-003",
                "Record final granule weight",
                "Weigh discharge container and record in batch record",
                "Pallavi Joshi",
                0,
            ),
            _item(
                "CLI-011",
                "PRD-003",
                "Check granule appearance",
                "Visual inspection — granules should be uniform, no lumps",
                "Pallavi Joshi",
                0,
            ),
            _item(
                "CLI-012",
                "PRD-003",
                "Verify LOD result",
                "LOD must be within specification (≤ 2.0%)",
                "Pallavi Joshi",
                0,
            ),
            _item(
                "CLI-013",
                "PRD-003",
                "Clean RMG interior",
                "Wipe down RMG bowl and discharge chute",
                "Pallavi Joshi",
                1,
            ),
            _item(
                "CLI-014",
                "PRD-003",
                "Update batch record",
                "Complete all batch record entries and sign",
                "Pallavi Joshi",
                1,
            ),
            _item(
                "CLI-015",
                "PRD-003",
                "Apply line clearance label",
                "Attach line clearance sticker to discharge container",
                "Pallavi Joshi",
                1,
            ),
        ],
    ),
    # === BLENDING OPERATOR (Devansh Kapoor) ===
    _checklist(
        "CL-BLD-001",
        "Blending Daily Start-up Checklist",
        "Pre-start checks for blending stage",
        "WRK-WS-010",
        ["blending", "daily", "startup"],
        [
            _item(
                "CLI-020",
                "PM-BLD-001",
                "Verify blender cleaning status",
                "Confirm blender interior is clean and dry",
                "Devansh Kapoor",
                0,
                True,
            ),
            _item(
                "CLI-021",
                "PM-BLD-001",
                "Check butterfly valve seal",
                "Inspect seal for wear or cracking",
                "Devansh Kapoor",
                0,
                True,
            ),
            _item(
                "CLI-022",
                "PM-BLD-001",
                "Verify shaft seal kit",
                "No visible leakage around shaft",
                "Devansh Kapoor",
                0,
                True,
            ),
            _item(
                "CLI-023",
                "PM-BLD-001",
                "Check drive belt tension",
                "Belt should have 10-15mm deflection",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-024",
                "PM-BLD-001",
                "Inspect shell gasket",
                "No damage or deformation",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-025",
                "PM-BLD-001",
                "Lubricate bearings",
                "Apply food-grade grease to bearing points",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-026",
                "PM-BLD-001",
                "Test RPM counter",
                "Run blender at low speed — verify RPM display",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-027",
                "PM-BLD-001",
                "Verify bin lifter operation",
                "Test bin lift and lower cycle",
                "Devansh Kapoor",
                0,
                True,
            ),
        ],
    ),
    _checklist(
        "CL-BLD-002",
        "Blending Batch Completion Checklist",
        "Post-batch checks for blending stage",
        "WRK-WS-010",
        ["blending", "batch", "completion"],
        [
            _item(
                "CLI-030",
                "PRD-006",
                "Record final blend weight",
                "Weigh discharge and record in batch record",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-031",
                "PRD-006",
                "Collect blend uniformity sample",
                "Take sample from 3 locations (top, middle, bottom)",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-032",
                "PRD-006",
                "Verify blend uniformity result",
                "RSD must be ≤ 5.0%",
                "Devansh Kapoor",
                0,
            ),
            _item(
                "CLI-033",
                "PRD-006",
                "Clean blender interior",
                "Wipe down all product contact surfaces",
                "Devansh Kapoor",
                1,
            ),
            _item(
                "CLI-034",
                "PRD-006",
                "Update batch record",
                "Complete all batch record entries and sign",
                "Devansh Kapoor",
                1,
            ),
            _item(
                "CLI-035",
                "PRD-006",
                "Apply line clearance label",
                "Attach line clearance sticker to discharge bin",
                "Devansh Kapoor",
                1,
            ),
        ],
    ),
    # === PACKAGING OPERATOR (Karan Shah) ===
    _checklist(
        "CL-PKG-001",
        "Packaging Daily Start-up Checklist",
        "Pre-start checks for packaging stage",
        "WRK-WS-007",
        ["packaging", "daily", "startup"],
        [
            _item(
                "CLI-040",
                "PM-BPM-001",
                "Verify blister machine cleaning",
                "Confirm forming/sealing stations are clean",
                "Karan Shah",
                0,
                True,
            ),
            _item(
                "CLI-041",
                "PM-BPM-001",
                "Check forming die temperature",
                "Set to 120-130°C and verify stability",
                "Karan Shah",
                0,
                True,
            ),
            _item(
                "CLI-042",
                "PM-BPM-001",
                "Check sealing die temperature",
                "Set to 150-160°C and verify stability",
                "Karan Shah",
                0,
                True,
            ),
            _item(
                "CLI-043",
                "PM-BPM-001",
                "Inspect conveyor chain",
                "No visible wear or slack",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-044",
                "PM-BPM-001",
                "Test photocell sensors",
                "Verify all sensors detect foil presence",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-045",
                "PM-BPM-001",
                "Verify batch code printer",
                "Print test batch code — must be legible",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-046",
                "PM-BPM-001",
                "Check foil/film supply",
                "Ensure sufficient material for batch",
                "Karan Shah",
                0,
                True,
            ),
        ],
    ),
    _checklist(
        "CL-PKG-002",
        "Packaging Batch Completion Checklist",
        "Post-batch checks for packaging stage",
        "WRK-WS-007",
        ["packaging", "batch", "completion"],
        [
            _item(
                "CLI-050",
                "PRD-014",
                "Record strip count",
                "Count and record total strips produced",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-051",
                "PRD-014",
                "Verify batch code on strips",
                "Sample 10 strips — all must have correct batch code",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-052",
                "PRD-014",
                "Check seal integrity",
                "Visual inspection — no leaking blisters",
                "Karan Shah",
                0,
            ),
            _item(
                "CLI-053",
                "PRD-014",
                "Clean machine",
                "Remove foil residue, clean sealing station",
                "Karan Shah",
                1,
            ),
            _item(
                "CLI-054",
                "PRD-014",
                "Update batch record",
                "Complete all batch record entries and sign",
                "Karan Shah",
                1,
            ),
        ],
    ),
    # === PLANT SAFETY OFFICER (Rajesh Patel) ===
    _checklist(
        "CL-SAF-001",
        "Monthly Safety Walkthrough",
        "Monthly plant safety inspection",
        "WRK-WS-017",
        ["safety", "monthly", "inspection"],
        [
            _item(
                "CLI-060",
                "",
                "Check fire extinguisher accessibility",
                "Verify all extinguishers are unobstructed and within expiry",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-061",
                "",
                "Inspect emergency exits",
                "All exits must be clear and clearly marked",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-062",
                "",
                "Verify eyewash stations",
                "Test flow and check expiry of sterile solution",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-063",
                "",
                "Check PPE availability",
                "Verify stock levels for all PPE categories",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-064",
                "",
                "Inspect electrical panels",
                "No exposed wiring, proper labeling, locked",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-065",
                "",
                "Check chemical storage",
                "SDS available, secondary containment intact",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-066",
                "",
                "Verify spill kit locations",
                "All spill kits stocked and accessible",
                "Rajesh Patel",
                7,
            ),
            _item(
                "CLI-067",
                "",
                "Inspect LOTO devices",
                "All LOTO locks/tags in good condition",
                "Rajesh Patel",
                7,
            ),
        ],
    ),
    # === LINE SUPERVISOR (Rohit Verma) ===
    _checklist(
        "CL-SUP-001",
        "Shift Handover Checklist",
        "End-of-shift handover for line supervisor",
        "WRK-LINE-001",
        ["supervisor", "shift", "handover"],
        [
            _item(
                "CLI-070",
                "",
                "Review pending work orders",
                "List all open WOs and their current status",
                "Rohit Verma",
                0,
            ),
            _item(
                "CLI-071",
                "",
                "Check machine status",
                "All machines on line status (running/down/maintenance)",
                "Rohit Verma",
                0,
            ),
            _item(
                "CLI-072",
                "",
                "Review batch progress",
                "Current batch number, stage, and completion %",
                "Rohit Verma",
                0,
            ),
            _item(
                "CLI-073",
                "",
                "Note any deviations",
                "Record any deviations or incidents during shift",
                "Rohit Verma",
                0,
            ),
            _item(
                "CLI-074",
                "",
                "Hand over to next shift supervisor",
                "Verbal + written handover to incoming supervisor",
                "Rohit Verma",
                0,
            ),
        ],
    ),
]


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Clearing existing checklists...")
    await db["checklists"].delete_many({})

    print(f"Inserting {len(CHECKLISTS)} checklists...")
    for cl in CHECKLISTS:
        await db["checklists"].insert_one(dict(cl))
        item_count = len(cl.get("items", []))
        completed = sum(1 for i in cl.get("items", []) if i.get("is_completed"))
        print(f"  + {cl['checklist_id']}: {cl['title']} ({completed}/{item_count} completed)")

    count = await db["checklists"].count_documents({})
    total_items = sum(len(cl.get("items", [])) for cl in CHECKLISTS)
    completed_items = sum(1 for cl in CHECKLISTS for i in cl.get("items", []) if i.get("is_completed"))
    print(f"\nDone. {count} checklists, {total_items} items ({completed_items} completed)")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
