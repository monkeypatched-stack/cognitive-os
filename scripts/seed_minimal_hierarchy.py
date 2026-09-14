#!/usr/bin/env python3
"""
Seed minimal hierarchy for clean start:
  Plant → Line → Stage → Workstation
  Plant → Building → Floor → Room → Bay

Then sync to Neo4j, Redis, Elasticsearch via MirroredCollection.

Usage:
    .venv/bin/python scripts/seed_minimal_hierarchy.py
"""

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
    reset_neo4j_mirror,
)

NOW = datetime.now(timezone.utc)


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Resetting Neo4j...")
    await reset_neo4j_mirror()

    # ── 1. Plant ───────────────────────────────────────────────────────────
    plant = {
        "_id": "PLANT-TBL-IN-001",
        "id": "PLANT-TBL-IN-001",
        "name": "Tablet Manufacturing Plant",
        "plant_code": "TAB-PLANT-001",
        "type": "Production",
        "location": "Ahmedabad, Gujarat",
        "address": "Survey No. 88, Pharma Industrial Estate, Ahmedabad, Gujarat, India",
        "country": "India",
        "timezone": "IST",
        "status": "Active",
        "capacity_utilization": 72,
        "manager": "Priya Sharma",
        "manager_contact": {
            "name": "Priya Sharma",
            "role": "Plant Manager",
            "email": "priya.sharma@tabletplant.in",
            "phone": "+91 265 234 8901",
        },
        "emergency_contact": {
            "name": "Rajesh Patel",
            "role": "Plant Safety Officer",
            "phone": "+91-79-2657-1234",
            "email": "rajesh.patel@safety.plant.com",
        },
        "shift_schedule": {
            "shifts_per_day": 3,
            "operating_days_per_week": 6,
            "hours_per_shift": 8.0,
        },
        "utilities": {
            "power_source": "State grid power with diesel generator backup",
            "power_capacity_kw": 1250,
            "water_source": "Municipal supply with on-site treated water storage",
            "water_consumption_m3_day": 120.0,
            "has_backup_generator": True,
        },
        "certifications": [
            {
                "name": "WHO-GMP",
                "issued_by": "State Drug Authority",
                "issue_date": "2022-04-01",
                "expiry_date": "2027-03-31",
                "status": "Valid",
            },
            {
                "name": "Schedule M - CDSCO",
                "issued_by": "Central Drugs Standard Control Organisation",
                "issue_date": "2021-09-15",
                "expiry_date": "2026-09-14",
                "status": "Valid",
            },
        ],
        "created_at": NOW,
        "updated_at": NOW,
    }
    await db["industrial_plants"].insert_one(plant)
    await safe_mirror(mirror_document("industrial_plants", plant, "seed"))
    print("  ✓ Plant: PLANT-TBL-IN-001")

    # ── 2. Line ────────────────────────────────────────────────────────────
    line = {
        "_id": "LINE-TAB-A",
        "id": "LINE-TAB-A",
        "name": "Tablet Line A",
        "plant_id": "PLANT-TBL-IN-001",
        "type": "Batch",
        "takt_time": 120.0,
        "status": "Operational",
        "efficiency": 85,
        "description": "High-speed tablet compression line",
        "created_at": NOW,
        "updated_at": NOW,
    }
    await db["industrial_lines"].insert_one(line)
    await safe_mirror(mirror_document("industrial_lines", line, "seed"))
    print("  ✓ Line: LINE-TAB-A → PLANT-TBL-IN-001")

    # ── 3. Stage ───────────────────────────────────────────────────────────
    stage = {
        "_id": "STAGE-001",
        "id": "STAGE-001",
        "name": "Compression",
        "plant_id": "PLANT-TBL-IN-001",
        "line_id": "LINE-TAB-A",
        "type": "Batch",
        "takt_time": 120.0,
        "status": "Operational",
        "efficiency": 88,
        "description": "Tablet compression and in-process checks",
        "created_at": NOW,
        "updated_at": NOW,
    }
    await db["industrial_stages"].insert_one(stage)
    await safe_mirror(mirror_document("industrial_stages", stage, "seed"))
    print("  ✓ Stage: STAGE-001 → LINE-TAB-A")

    # ── 4. Workstation ─────────────────────────────────────────────────────
    workstation = {
        "_id": "WS-001",
        "id": "WS-001",
        "name": "Tablet Compression Workstation",
        "line_id": "LINE-TAB-A",
        "stage_id": "STAGE-001",
        "mode": "Hybrid",
        "status": "Active",
        "operator": "Ravi Kumar",
        "cycle_time_actual": 115.0,
        "cycle_time_target": 120.0,
        "is_bottleneck": False,
        "description": "Primary rotary tablet press workstation",
        "created_at": NOW,
        "updated_at": NOW,
    }
    await db["industrial_workstations"].insert_one(workstation)
    print("  ✓ Workstation (MongoDB): WS-001 → STAGE-001")

    # Mirror into 'workstations' collection (Neo4j mirror handles HAS_WORKSTATION relationships)
    ws_for_mirror = dict(workstation)
    ws_for_mirror["_id"] = "WS-001"
    ws_for_mirror["workstation_id"] = "WS-001"
    await db["workstations"].update_one({"_id": "WS-001"}, {"$set": ws_for_mirror}, upsert=True)
    await safe_mirror(mirror_document("workstations", ws_for_mirror, "seed"))
    print("  ✓ Workstation (mirror): WS-001 → STAGE-001, LINE-TAB-A")

    # ── 5. Building ────────────────────────────────────────────────────────
    building = {
        "id": "LOC-BUILDING-001",
        "location_id": "BLDG-MFG-001",
        "name": "Main Manufacturing Building",
        "type": "building",
        "parent_id": "LOC-PLANT-001",
        "level": 2,
        "path": "Tablet Manufacturing Plant > Main Manufacturing Building",
        "description": "Primary tablet manufacturing building",
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }

    # ── 6. Floor ───────────────────────────────────────────────────────────
    floor = {
        "id": "LOC-FLOOR-001",
        "location_id": "FLR-MFG-01",
        "name": "First Floor - Granulation & Compression",
        "type": "floor",
        "parent_id": "LOC-BUILDING-001",
        "level": 3,
        "path": "Tablet Manufacturing Plant > Main Manufacturing Building > First Floor - Granulation & Compression",
        "description": "Granulation, drying, blending, and tablet compression",
        "floor_number": 1,
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }

    # ── 7. Room ────────────────────────────────────────────────────────────
    room = {
        "id": "LOC-ROOM-001",
        "location_id": "RM-COMP-001",
        "name": "Compression Room 1",
        "type": "room",
        "parent_id": "LOC-FLOOR-001",
        "level": 4,
        "path": "Tablet Manufacturing Plant > Main Manufacturing Building > First Floor - Granulation & Compression > Compression Room 1",
        "description": "Primary tablet compression room with rotary press",
        "room_number": "01-101",
        "cleanroom_class": "ISO 7",
        "temperature_controlled": True,
        "humidity_controlled": True,
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }

    # ── 8. Bay ─────────────────────────────────────────────────────────────
    bay = {
        "id": "LOC-BAY-001",
        "location_id": "BAY-COMP-001A",
        "name": "Compression Bay 1",
        "type": "bay",
        "parent_id": "LOC-ROOM-001",
        "level": 5,
        "path": "Tablet Manufacturing Plant > Main Manufacturing Building > First Floor - Granulation & Compression > Compression Room 1 > Compression Bay 1",
        "description": "Rotary tablet press bay",
        "bay_number": "1",
        "workstation_id": "WS-001",
        "cleanroom_class": "ISO 7",
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }

    locations = [building, floor, room, bay]
    plant_location = {
        "id": "LOC-PLANT-001",
        "location_id": "PLANT-TBL-IN-001",
        "name": "Tablet Manufacturing Plant",
        "type": "plant",
        "parent_id": None,
        "level": 1,
        "path": "Tablet Manufacturing Plant",
        "description": "Complete tablet manufacturing facility in Ahmedabad",
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }
    locations.insert(0, plant_location)

    await db["plant_locations"].insert_many(locations)
    for loc in locations:
        await safe_mirror(mirror_document("plant_locations", loc, "seed"))
    print("  ✓ Locations: Plant → Building → Floor → Room → Bay (5 docs)")

    # Create parent→child relationships between PlantLocation nodes in Neo4j
    from services.common.neo4j_mirror import _get_driver

    driver = _get_driver()
    async with driver.session() as session:
        for loc in locations:
            parent_id = loc.get("parent_id")
            if parent_id:
                await session.run(
                    """
                    MATCH (parent {entity_id: $parent_id})
                    MATCH (child {entity_id: $child_id})
                    MERGE (parent)-[r:HAS_LOCATION]->(child)
                    SET r.mirror_managed = true, r.updated_at = $now
                    """,
                    parent_id=parent_id,
                    child_id=loc["id"],
                    now=str(NOW),
                )
    print("  ✓ Location hierarchy linked: Plant→Building→Floor→Room→Bay")

    # ── Verify ─────────────────────────────────────────────────────────────
    async with driver.session() as session:
        r = await (await session.run("MATCH (n) RETURN count(n) as c")).single()
        nodes = r["c"]
        r = await (await session.run("MATCH ()-[r]->() RETURN count(r) as c")).single()
        rels = r["c"]

    await close_neo4j_mirror_driver()

    print(f"\nDone. Neo4j: {nodes} nodes, {rels} relationships")

    # Verify hierarchy links
    for coll in [
        "industrial_plants",
        "industrial_lines",
        "industrial_stages",
        "industrial_workstations",
        "workstations",
    ]:
        count = await db[coll].count_documents({})
        print(f"  {coll}: {count} docs")

    count = await db["plant_locations"].count_documents({})
    print(f"  plant_locations: {count} docs")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
