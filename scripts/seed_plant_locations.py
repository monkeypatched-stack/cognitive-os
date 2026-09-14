#!/usr/bin/env python3
"""Seed plant_locations (building → floor → room → bay) for both lines."""

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
    _get_driver,
    reset_neo4j_mirror,
)

NOW = datetime.now(timezone.utc)
PLANT_ID = "PLANT-TBL-IN-001"
TAB_LINE = "LINE-TAB-001"
CAP_LINE = "LINE-CAP-001"

PLANT_PATH = "Tablet Manufacturing Plant"


def _loc(loc_id, loc_code, name, ltype, parent_id, level, path, extra=None):
    doc = {
        "id": loc_id,
        "location_id": loc_code,
        "name": name,
        "type": ltype,
        "parent_id": parent_id,
        "level": level,
        "path": path,
        "description": extra.pop("description", name) if extra else name,
        "status": "Operational",
        "created_at": NOW,
        "updated_at": NOW,
    }
    if extra:
        doc.update(extra)
    return doc


LOCATIONS = []

# ── Plant ─────────────────────────────────────────────────────────────────
LOCATIONS.append(
    _loc(
        "LOC-PLANT-001",
        "PLANT-TBL-IN-001",
        "Tablet Manufacturing Plant",
        "plant",
        None,
        1,
        PLANT_PATH,
        {"description": "Complete tablet manufacturing facility in Ahmedabad"},
    )
)

# ── Tablet Line Building ──────────────────────────────────────────────────
TAB_BLDG = "LOC-TAB-BLDG-001"
LOCATIONS.append(
    _loc(
        TAB_BLDG,
        "BLDG-TAB-001",
        "Tablet Manufacturing Building",
        "building",
        "LOC-PLANT-001",
        2,
        f"{PLANT_PATH} > Tablet Manufacturing Building",
        {"description": "Houses Tablet Line A production areas"},
    )
)

# Floor 1: Dispensing → Blending (stages 1-6)
TAB_FLR1 = "LOC-TAB-FLR-001"
LOCATIONS.append(
    _loc(
        TAB_FLR1,
        "FLR-TAB-01",
        "First Floor — Dispensing to Blending",
        "floor",
        TAB_BLDG,
        3,
        f"{PLANT_PATH} > Tablet Manufacturing Building > First Floor — Dispensing to Blending",
        {
            "floor_number": 1,
            "description": "Raw material dispensing, sifting, granulation, drying, sizing, and blending",
        },
    )
)

# Floor 2: Compression → Packaging (stages 7-10)
TAB_FLR2 = "LOC-TAB-FLR-002"
LOCATIONS.append(
    _loc(
        TAB_FLR2,
        "FLR-TAB-02",
        "Second Floor — Compression to Packaging",
        "floor",
        TAB_BLDG,
        3,
        f"{PLANT_PATH} > Tablet Manufacturing Building > Second Floor — Compression to Packaging",
        {
            "floor_number": 2,
            "description": "Tablet compression, coating, inspection, and packaging",
        },
    )
)

# ── Floor 1 Rooms ─────────────────────────────────────────────────────────
_tab_rooms_f1 = [
    (
        "LOC-TAB-RM-001",
        "RM-DISP-001",
        "Dispensing Room",
        "Dispensing",
        "WS-TAB-01-01",
        "ISO 8",
    ),
    (
        "LOC-TAB-RM-002",
        "RM-SIFT-001",
        "Sifting/Milling Room",
        "Sifting/Milling",
        "WS-TAB-02-01",
        "ISO 8",
    ),
    (
        "LOC-TAB-RM-003",
        "RM-GRAN-001",
        "Granulation Room",
        "Granulation",
        "WS-TAB-03-01",
        "ISO 8",
    ),
    (
        "LOC-TAB-RM-004",
        "RM-DRY-001",
        "Drying Room",
        "Drying (FBD)",
        "WS-TAB-04-01",
        "ISO 7",
    ),
    (
        "LOC-TAB-RM-005",
        "RM-SIZE-001",
        "Sizing/Milling Room",
        "Sizing/Milling (post-dry)",
        "WS-TAB-05-01",
        "ISO 8",
    ),
    (
        "LOC-TAB-RM-006",
        "RM-BLEND-001",
        "Blending Room",
        "Blending",
        "WS-TAB-06-01",
        "ISO 7",
    ),
]

for i, (rm_id, rm_code, rm_name, stage_name, ws_id, cc) in enumerate(_tab_rooms_f1, 1):
    LOCATIONS.append(
        _loc(
            rm_id,
            rm_code,
            rm_name,
            "room",
            TAB_FLR1,
            4,
            f"{PLANT_PATH} > Tablet Manufacturing Building > First Floor — Dispensing to Blending > {rm_name}",
            {
                "room_number": f"01-{i:03d}",
                "cleanroom_class": cc,
                "temperature_controlled": True,
                "humidity_controlled": True,
                "description": f"{stage_name} room with associated workstations",
            },
        )
    )
    # Add 2-3 bays per room
    bay_count = 3 if i <= 2 else 2
    for b in range(1, bay_count + 1):
        bay_id = f"LOC-TAB-BAY-{i:02d}{b}"
        bay_code = f"BAY-TAB-{i:02d}{b:02d}"
        LOCATIONS.append(
            _loc(
                bay_id,
                bay_code,
                f"{rm_name} Bay {b}",
                "bay",
                rm_id,
                5,
                f"{PLANT_PATH} > Tablet Manufacturing Building > First Floor — Dispensing to Blending > {rm_name} > Bay {b}",
                {
                    "bay_number": str(b),
                    "workstation_id": ws_id,
                    "cleanroom_class": cc,
                    "description": f"Bay {b} of {rm_name}",
                },
            )
        )

# ── Floor 2 Rooms ─────────────────────────────────────────────────────────
_tab_rooms_f2 = [
    (
        "LOC-TAB-RM-007",
        "RM-COMP-001",
        "Compression Room",
        "Compression",
        "WS-TAB-07-01",
        "ISO 7",
    ),
    (
        "LOC-TAB-RM-008",
        "RM-COAT-001",
        "Coating Room",
        "Film Coating",
        "WS-TAB-08-01",
        "ISO 7",
    ),
    (
        "LOC-TAB-RM-009",
        "RM-INSP-001",
        "Inspection Room",
        "Inspection / Dedust / Polish",
        "WS-TAB-09-01",
        "ISO 8",
    ),
    (
        "LOC-TAB-RM-010",
        "RM-PKG-001",
        "Packaging Room",
        "Packaging",
        "WS-TAB-10-01",
        "ISO 8",
    ),
]

for i, (rm_id, rm_code, rm_name, stage_name, ws_id, cc) in enumerate(_tab_rooms_f2, 7):
    LOCATIONS.append(
        _loc(
            rm_id,
            rm_code,
            rm_name,
            "room",
            TAB_FLR2,
            4,
            f"{PLANT_PATH} > Tablet Manufacturing Building > Second Floor — Compression to Packaging > {rm_name}",
            {
                "room_number": f"02-{i - 6:03d}",
                "cleanroom_class": cc,
                "temperature_controlled": True,
                "humidity_controlled": True,
                "description": f"{stage_name} room with associated workstations",
            },
        )
    )
    bay_count = 3 if i <= 8 else 2
    for b in range(1, bay_count + 1):
        bay_id = f"LOC-TAB-BAY-{i:02d}{b}"
        bay_code = f"BAY-TAB-{i:02d}{b:02d}"
        LOCATIONS.append(
            _loc(
                bay_id,
                bay_code,
                f"{rm_name} Bay {b}",
                "bay",
                rm_id,
                5,
                f"{PLANT_PATH} > Tablet Manufacturing Building > Second Floor — Compression to Packaging > {rm_name} > Bay {b}",
                {
                    "bay_number": str(b),
                    "workstation_id": ws_id,
                    "cleanroom_class": cc,
                    "description": f"Bay {b} of {rm_name}",
                },
            )
        )

# ── Capsule Line Building ─────────────────────────────────────────────────
CAP_BLDG = "LOC-CAP-BLDG-001"
LOCATIONS.append(
    _loc(
        CAP_BLDG,
        "BLDG-CAP-001",
        "Capsule Manufacturing Building",
        "building",
        "LOC-PLANT-001",
        2,
        f"{PLANT_PATH} > Capsule Manufacturing Building",
        {"description": "Houses Capsule Line B production areas"},
    )
)

CAP_FLR1 = "LOC-CAP-FLR-001"
LOCATIONS.append(
    _loc(
        CAP_FLR1,
        "FLR-CAP-01",
        "First Floor — Dispensing to Blending",
        "floor",
        CAP_BLDG,
        3,
        f"{PLANT_PATH} > Capsule Manufacturing Building > First Floor — Dispensing to Blending",
        {
            "floor_number": 1,
            "description": "Raw material dispensing, sifting, granulation, drying, sizing, and blending",
        },
    )
)

CAP_FLR2 = "LOC-CAP-FLR-002"
LOCATIONS.append(
    _loc(
        CAP_FLR2,
        "FLR-CAP-02",
        "Second Floor — Filling to Packaging",
        "floor",
        CAP_BLDG,
        3,
        f"{PLANT_PATH} > Capsule Manufacturing Building > Second Floor — Filling to Packaging",
        {
            "floor_number": 2,
            "description": "Capsule filling, polishing, inspection, and packaging",
        },
    )
)

# ── Capsule Floor 1 Rooms ────────────────────────────────────────────────
_cap_rooms_f1 = [
    (
        "LOC-CAP-RM-001",
        "RM-C-DISP-001",
        "Dispensing Room",
        "Dispensing",
        "WS-CAP-01-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-002",
        "RM-C-SIFT-001",
        "Sifting/Milling Room",
        "Sifting/Milling",
        "WS-CAP-02-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-003",
        "RM-C-GRAN-001",
        "Granulation Room",
        "Granulation",
        "WS-CAP-03-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-004",
        "RM-C-DRY-001",
        "Drying Room",
        "Drying (FBD)",
        "WS-CAP-04-01",
        "ISO 7",
    ),
    (
        "LOC-CAP-RM-005",
        "RM-C-SIZE-001",
        "Sizing/Milling Room",
        "Sizing/Milling (post-dry)",
        "WS-CAP-05-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-006",
        "RM-C-BLEND-001",
        "Blending Room",
        "Blending",
        "WS-CAP-06-01",
        "ISO 7",
    ),
]

for i, (rm_id, rm_code, rm_name, stage_name, ws_id, cc) in enumerate(_cap_rooms_f1, 1):
    LOCATIONS.append(
        _loc(
            rm_id,
            rm_code,
            rm_name,
            "room",
            CAP_FLR1,
            4,
            f"{PLANT_PATH} > Capsule Manufacturing Building > First Floor — Dispensing to Blending > {rm_name}",
            {
                "room_number": f"01-{i:03d}",
                "cleanroom_class": cc,
                "temperature_controlled": True,
                "humidity_controlled": True,
                "description": f"{stage_name} room with associated workstations",
            },
        )
    )
    bay_count = 3 if i <= 2 else 2
    for b in range(1, bay_count + 1):
        bay_id = f"LOC-CAP-BAY-{i:02d}{b}"
        bay_code = f"BAY-CAP-{i:02d}{b:02d}"
        LOCATIONS.append(
            _loc(
                bay_id,
                bay_code,
                f"{rm_name} Bay {b}",
                "bay",
                rm_id,
                5,
                f"{PLANT_PATH} > Capsule Manufacturing Building > First Floor — Dispensing to Blending > {rm_name} > Bay {b}",
                {
                    "bay_number": str(b),
                    "workstation_id": ws_id,
                    "cleanroom_class": cc,
                    "description": f"Bay {b} of {rm_name}",
                },
            )
        )

# ── Capsule Floor 2 Rooms ────────────────────────────────────────────────
_cap_rooms_f2 = [
    (
        "LOC-CAP-RM-007",
        "RM-C-FILL-001",
        "Capsule Filling Room",
        "Capsule Filling",
        "WS-CAP-07-01",
        "ISO 7",
    ),
    (
        "LOC-CAP-RM-008",
        "RM-C-POLISH-001",
        "Polishing/Dedust Room",
        "Polishing / Dedust",
        "WS-CAP-08-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-009",
        "RM-C-INSP-001",
        "Inspection Room",
        "Inspection",
        "WS-CAP-09-01",
        "ISO 8",
    ),
    (
        "LOC-CAP-RM-010",
        "RM-C-PKG-001",
        "Packaging Room",
        "Packaging",
        "WS-CAP-10-01",
        "ISO 8",
    ),
]

for i, (rm_id, rm_code, rm_name, stage_name, ws_id, cc) in enumerate(_cap_rooms_f2, 7):
    LOCATIONS.append(
        _loc(
            rm_id,
            rm_code,
            rm_name,
            "room",
            CAP_FLR2,
            4,
            f"{PLANT_PATH} > Capsule Manufacturing Building > Second Floor — Filling to Packaging > {rm_name}",
            {
                "room_number": f"02-{i - 6:03d}",
                "cleanroom_class": cc,
                "temperature_controlled": True,
                "humidity_controlled": True,
                "description": f"{stage_name} room with associated workstations",
            },
        )
    )
    bay_count = 3 if i <= 8 else 2
    for b in range(1, bay_count + 1):
        bay_id = f"LOC-CAP-BAY-{i:02d}{b}"
        bay_code = f"BAY-CAP-{i:02d}{b:02d}"
        LOCATIONS.append(
            _loc(
                bay_id,
                bay_code,
                f"{rm_name} Bay {b}",
                "bay",
                rm_id,
                5,
                f"{PLANT_PATH} > Capsule Manufacturing Building > Second Floor — Filling to Packaging > {rm_name} > Bay {b}",
                {
                    "bay_number": str(b),
                    "workstation_id": ws_id,
                    "cleanroom_class": cc,
                    "description": f"Bay {b} of {rm_name}",
                },
            )
        )


async def seed():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    await db["plant_locations"].delete_many({})
    await db["plant_location_relationships"].delete_many({})

    print(f"Inserting {len(LOCATIONS)} locations...")
    for loc in LOCATIONS:
        await db["plant_locations"].insert_one(dict(loc))
        await safe_mirror(mirror_document("plant_locations", loc, "seed"))

    # Create parent→child relationships in Neo4j
    await reset_neo4j_mirror()
    for loc in LOCATIONS:
        await safe_mirror(mirror_document("plant_locations", loc, "seed"))

    driver = _get_driver()
    async with driver.session() as session:
        for loc in LOCATIONS:
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

    # Summary
    by_type = {}
    for loc in LOCATIONS:
        t = loc["type"]
        by_type[t] = by_type.get(t, 0) + 1
    for t in ["plant", "building", "floor", "room", "bay"]:
        print(f"  {t}: {by_type.get(t, 0)}")
    print(f"\nTotal: {len(LOCATIONS)} locations")

    await close_neo4j_mirror_driver()
    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
