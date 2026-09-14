#!/usr/bin/env python3
"""Seed workstations for Tablet Line A and Capsule Line B."""

import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings
from services.common.neo4j_mirror import (
    mirror_document,
    safe_mirror,
    close_neo4j_mirror_driver,
)

NOW = datetime.now(timezone.utc)
PLANT_ID = "PLANT-TBL-IN-001"
TAB_LINE = "LINE-TAB-001"
CAP_LINE = "LINE-CAP-001"

TABLET_WORKSTATIONS = [
    ("WS-TAB-01-01", "Sampling Booth", "STAGE-01", 1),
    ("WS-TAB-01-02", "Weighing Booth", "STAGE-01", 2),
    ("WS-TAB-01-03", "Material Staging Area", "STAGE-01", 3),
    ("WS-TAB-02-01", "Sifter Station", "STAGE-03", 1),
    ("WS-TAB-02-02", "Co-Mill Station", "STAGE-03", 2),
    ("WS-TAB-02-03", "Vibro Sifter Station", "STAGE-03", 3),
    ("WS-TAB-03-01", "RMG/High Shear Granulator Station", "STAGE-02", 1),
    ("WS-TAB-03-02", "Binder Preparation Station", "STAGE-02", 2),
    ("WS-TAB-04-01", "FBD Loading Station", "STAGE-09", 1),
    ("WS-TAB-04-02", "FBD Unloading Station", "STAGE-09", 2),
    ("WS-TAB-05-01", "Co-Mill Station (post-dry)", "STAGE-10", 1),
    ("WS-TAB-05-02", "Sieve Station", "STAGE-10", 2),
    ("WS-TAB-06-01", "Blender Loading Station", "STAGE-04", 1),
    ("WS-TAB-06-02", "Blender Station", "STAGE-04", 2),
    ("WS-TAB-06-03", "Lubrication Station", "STAGE-04", 3),
    ("WS-TAB-07-01", "Tablet Press Station", "STAGE-05", 1),
    ("WS-TAB-07-02", "In-Process QC Station", "STAGE-05", 2),
    ("WS-TAB-07-03", "Deduster Station", "STAGE-05", 3),
    ("WS-TAB-08-01", "Coating Pan Station", "STAGE-06", 1),
    ("WS-TAB-08-02", "Solution Prep Station", "STAGE-06", 2),
    ("WS-TAB-08-03", "Inlet/Outlet Air Station", "STAGE-06", 3),
    ("WS-TAB-09-01", "Visual Inspection Station", "STAGE-07", 1),
    ("WS-TAB-09-02", "Metal Detector Station", "STAGE-07", 2),
    ("WS-TAB-09-03", "Polishing Station", "STAGE-07", 3),
    ("WS-TAB-10-01", "Blister/Strip Station", "STAGE-08", 1),
    ("WS-TAB-10-02", "Bottle Filling Station", "STAGE-08", 2),
    ("WS-TAB-10-03", "Labelling Station", "STAGE-08", 3),
    ("WS-TAB-10-04", "Cartoning Station", "STAGE-08", 4),
]

CAPSULE_WORKSTATIONS = [
    ("WS-CAP-01-01", "Sampling Booth", "STAGE-CAP-01", 1),
    ("WS-CAP-01-02", "Weighing Booth", "STAGE-CAP-01", 2),
    ("WS-CAP-01-03", "Material Staging Area", "STAGE-CAP-01", 3),
    ("WS-CAP-02-01", "Sifter Station", "STAGE-CAP-02", 1),
    ("WS-CAP-02-02", "Co-Mill Station", "STAGE-CAP-02", 2),
    ("WS-CAP-02-03", "Vibro Sifter Station", "STAGE-CAP-02", 3),
    ("WS-CAP-03-01", "RMG/High Shear Granulator Station", "STAGE-CAP-03", 1),
    ("WS-CAP-03-02", "Binder Preparation Station", "STAGE-CAP-03", 2),
    ("WS-CAP-04-01", "FBD Loading Station", "STAGE-CAP-04", 1),
    ("WS-CAP-04-02", "FBD Unloading Station", "STAGE-CAP-04", 2),
    ("WS-CAP-05-01", "Co-Mill Station (post-dry)", "STAGE-CAP-05", 1),
    ("WS-CAP-05-02", "Sieve Station", "STAGE-CAP-05", 2),
    ("WS-CAP-06-01", "Blender Loading Station", "STAGE-CAP-06", 1),
    ("WS-CAP-06-02", "Blender Station", "STAGE-CAP-06", 2),
    ("WS-CAP-06-03", "Lubrication Station", "STAGE-CAP-06", 3),
    ("WS-CAP-07-01", "Capsule Filler Station", "STAGE-CAP-07", 1),
    ("WS-CAP-07-02", "Empty Capsule Feeding Station", "STAGE-CAP-07", 2),
    ("WS-CAP-07-03", "In-Process QC Station", "STAGE-CAP-07", 3),
    ("WS-CAP-08-01", "Capsule Polisher Station", "STAGE-CAP-08", 1),
    ("WS-CAP-08-02", "Deduster Station", "STAGE-CAP-08", 2),
    ("WS-CAP-09-01", "Visual Inspection Station", "STAGE-CAP-09", 1),
    ("WS-CAP-09-02", "Metal Detector Station", "STAGE-CAP-09", 2),
    ("WS-CAP-10-01", "Blister/Strip Station", "STAGE-CAP-10", 1),
    ("WS-CAP-10-02", "Bottle Filling Station", "STAGE-CAP-10", 2),
    ("WS-CAP-10-03", "Labelling Station", "STAGE-CAP-10", 3),
    ("WS-CAP-10-04", "Cartoning Station", "STAGE-CAP-10", 4),
]


OPERATORS = [
    "Priya Sharma",
    "Rajesh Patel",
    "Nandita Rao",
    "Rohan Kulkarni",
    "Arjun Mehta",
    "Neha Singh",
    "Rahul Desai",
    "Nikita Bansal",
    "Sonal Iyer",
    "Vikram Sethi",
    "Karan Shah",
    "Vivek Raman",
    "Pallavi Joshi",
    "Devansh Kapoor",
    "Farah Merchant",
    "Ananya Ghosh",
    "Imran Qureshi",
    "Devika Nair",
    "Manish Trivedi",
    "Ritika Soni",
]

TABLET_COUNT = len(TABLET_WORKSTATIONS)


def _make_ws(ws_id, name, stage_id, line_id, sequence, idx):
    cycle_target = 60.0 + (idx % 5) * 15.0
    cycle_actual = cycle_target * (0.85 + (idx % 3) * 0.05)
    return {
        "_id": ws_id,
        "id": ws_id,
        "name": name,
        "line_id": line_id,
        "stage_id": stage_id,
        "sequence": sequence,
        "mode": "Hybrid",
        "status": "Active",
        "operator": OPERATORS[idx % len(OPERATORS)],
        "is_bottleneck": cycle_actual > cycle_target,
        "cycle_time_actual": round(cycle_actual, 1),
        "cycle_time_target": cycle_target,
        "description": f"{name} for {stage_id}",
        "created_at": NOW,
        "updated_at": NOW,
    }


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    await db["industrial_workstations"].delete_many({})
    await db["workstations"].delete_many({})

    count = 0
    for idx, (ws_id, name, stage_id, seq) in enumerate(TABLET_WORKSTATIONS):
        doc = _make_ws(ws_id, name, stage_id, TAB_LINE, seq, idx)
        await db["industrial_workstations"].insert_one(dict(doc))
        mirror = dict(doc)
        mirror["workstation_id"] = ws_id
        await db["workstations"].update_one({"_id": ws_id}, {"$set": mirror}, upsert=True)
        await safe_mirror(mirror_document("workstations", mirror, "seed"))
        count += 1

    for idx, (ws_id, name, stage_id, seq) in enumerate(CAPSULE_WORKSTATIONS):
        doc = _make_ws(ws_id, name, stage_id, CAP_LINE, seq, TABLET_COUNT + idx)
        await db["industrial_workstations"].insert_one(dict(doc))
        mirror = dict(doc)
        mirror["workstation_id"] = ws_id
        await db["workstations"].update_one({"_id": ws_id}, {"$set": mirror}, upsert=True)
        await safe_mirror(mirror_document("workstations", mirror, "seed"))
        count += 1

    print(f"Done. {count} workstations seeded.")
    client.close()
    await close_neo4j_mirror_driver()


if __name__ == "__main__":
    asyncio.run(seed())
