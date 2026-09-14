#!/usr/bin/env python3
"""Fill empty required fields on machines and equipment, rebuild embeddings, mirror to Neo4j."""

import asyncio
import random
import string
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings
from services.common.embeddings import build_embedding
from services.common.neo4j_mirror import (
    mirror_document,
    safe_mirror,
    close_neo4j_mirror_driver,
)

MACHINE_CATEGORIES = {
    "Rapid Mixer Granulator": ("CNC / Automated Machine", "GEA"),
    "Granule Sampling Station": ("Embedded / Smart Machine", "Sartorius"),
    "Fluid Bed Dryer": ("CNC / Automated Machine", "Glatt"),
    "Moisture Analyzer": ("Embedded / Smart Machine", "Mettler Toledo"),
    "Bin Blender": ("Mechanical Machine", "Patterson-Kelley"),
    "Rotary Tablet Press": ("CNC / Automated Machine", "Fette"),
    "Tablet Weight Checker": ("Embedded / Smart Machine", "Mettler Toledo"),
    "Auto Coater": ("CNC / Automated Machine", "Thomas Engineering"),
    "Visual Inspection": ("Embedded / Smart Machine", "Sartorius"),
    "Blister Packaging": ("CNC / Automated Machine", "Uhlmann"),
    "Cartoning Machine": ("Mechanical Machine", "Marchesini"),
    "V-Blender": ("Mechanical Machine", "Patterson-Kelley"),
    "High Shear Granulator": ("CNC / Automated Machine", "GEA"),
    "Cone Mill": ("Mechanical Machine", "Frewitt"),
    "Tablet Deduster": ("Mechanical Machine", "Vector"),
    "Metal Detector": ("Embedded / Smart Machine", "Mettler Toledo"),
    "Coating Pan": ("CNC / Automated Machine", "Thomas Engineering"),
    "Solution Preparation Vessel": ("Mechanical Machine", "GEA"),
    "Serialization System": ("CNC / Automated Machine", "Uhlmann"),
    "Checkweigher": ("Embedded / Smart Machine", "Mettler Toledo"),
}

MACHINE_TAXONOMY_MAPPING = {
    "Rapid Mixer Granulator": (
        "fam_osd_ahmedabad",
        "cls_osd_granulation",
        "sub_osd_rmg",
    ),
    "Granule Sampling Station": (
        "fam_osd_ahmedabad",
        "cls_osd_qc_inspection",
        "sub_osd_thief_sampler",
    ),
    "Fluid Bed Dryer": (
        "fam_osd_ahmedabad",
        "cls_osd_particle_processing",
        "sub_osd_fbd",
    ),
    "Moisture Analyzer": (
        "fam_osd_ahmedabad",
        "cls_osd_qc_inspection",
        "sub_osd_inspection_station",
    ),
    "Bin Blender": ("fam_osd_ahmedabad", "cls_osd_blending", "sub_osd_bin_blender"),
    "Rotary Tablet Press": (
        "fam_osd_ahmedabad",
        "cls_osd_tablet_processing",
        "sub_osd_rotary_tablet_press",
    ),
    "Tablet Weight Checker": (
        "fam_osd_ahmedabad",
        "cls_osd_qc_inspection",
        "sub_osd_tablet_counter",
    ),
    "Auto Coater": (
        "fam_osd_ahmedabad",
        "cls_osd_tablet_processing",
        "sub_osd_auto_coating_pan",
    ),
    "Visual Inspection": (
        "fam_osd_ahmedabad",
        "cls_osd_qc_inspection",
        "sub_osd_vision_inspection_system",
    ),
    "Blister Packaging": ("fam_osd_ahmedabad", "cls_osd_packaging", "sub_osd_blister"),
    "Cartoning Machine": ("fam_osd_ahmedabad", "cls_osd_packaging", "sub_osd_cartoner"),
}

MACHINE_WORKSTATION_MAPPING = {
    "Rapid Mixer Granulator": "WS-003",
    "Granule Sampling Station": "WS-004",
    "Fluid Bed Dryer": "WS-005",
    "Moisture Analyzer": "WS-006",
    "Bin Blender": "WS-008",
    "Rotary Tablet Press": "WS-009",
    "Tablet Weight Checker": "WS-010",
    "Auto Coater": "WS-011",
    "Visual Inspection": "WS-012",
    "Blister Packaging": "WS-013",
    "Cartoning Machine": "WS-014",
}

WORKSTATION_LOCATION_MAPPING = {
    "WS-003": "BAY-GRAN-001A",
    "WS-004": "BAY-GRAN-001A",
    "WS-005": "RM-DRY-001",
    "WS-006": "RM-DRY-001",
    "WS-008": "BAY-COMP-001A",
    "WS-009": "BAY-COMP-001A",
    "WS-010": "BAY-COMP-001A",
    "WS-011": "RM-COAT-001",
    "WS-012": "RM-COAT-001",
    "WS-013": "BAY-BLISTER-001A",
    "WS-014": "BAY-BLISTER-001A",
}

LOCATION_DATA = {
    "BAY-GRAN-001A": {
        "location_path": "PLANT-OSD-001 > BLDG-MFG-001 > FLR-MFG-01 > RM-GRAN-001 > BAY-GRAN-001A",
        "location_name": "Granulation Bay A",
        "location_type": "bay",
        "location_level": 5,
        "cleanroom_class": "ISO 7",
        "plant_id": "PLANT-OSD-001",
        "building_id": "BLDG-MFG-001",
        "floor_id": "FLR-MFG-01",
        "room_id": "RM-GRAN-001",
    },
    "RM-DRY-001": {
        "location_path": "PLANT-OSD-001 > BLDG-MFG-001 > FLR-MFG-01 > RM-DRY-001",
        "location_name": "Drying Room",
        "location_type": "room",
        "location_level": 4,
        "cleanroom_class": "ISO 7",
        "plant_id": "PLANT-OSD-001",
        "building_id": "BLDG-MFG-001",
        "floor_id": "FLR-MFG-01",
        "room_id": "RM-DRY-001",
    },
    "BAY-COMP-001A": {
        "location_path": "PLANT-OSD-001 > BLDG-MFG-001 > FLR-MFG-02 > RM-COMP-001 > BAY-COMP-001A",
        "location_name": "Compression Bay A",
        "location_type": "bay",
        "location_level": 5,
        "cleanroom_class": "ISO 7",
        "plant_id": "PLANT-OSD-001",
        "building_id": "BLDG-MFG-001",
        "floor_id": "FLR-MFG-02",
        "room_id": "RM-COMP-001",
    },
    "RM-COAT-001": {
        "location_path": "PLANT-OSD-001 > BLDG-MFG-001 > FLR-MFG-02 > RM-COAT-001",
        "location_name": "Coating Room",
        "location_type": "room",
        "location_level": 4,
        "cleanroom_class": "ISO 7",
        "plant_id": "PLANT-OSD-001",
        "building_id": "BLDG-MFG-001",
        "floor_id": "FLR-MFG-02",
        "room_id": "RM-COAT-001",
    },
    "BAY-BLISTER-001A": {
        "location_path": "PLANT-OSD-001 > BLDG-MFG-001 > FLR-MFG-03 > RM-BLISTER-001 > BAY-BLISTER-001A",
        "location_name": "Blister Packaging Bay A",
        "location_type": "bay",
        "location_level": 5,
        "cleanroom_class": "ISO 8",
        "plant_id": "PLANT-OSD-001",
        "building_id": "BLDG-MFG-001",
        "floor_id": "FLR-MFG-03",
        "room_id": "RM-BLISTER-001",
    },
}

EQUIPMENT_CATEGORY_MAP = {
    "API": "Machine",
    "Excipient": "Machine",
    "Consumable": "Machine",
    "Intermediate Product": "Machine",
    "Container": "Storage",
    "Inspection Device": "Instrument",
    "Tooling": "Machine",
    "Packaging Material": "Machine",
    "Finished Product": "Machine",
}

MACHINE_MANUFACTURERS = [
    "GEA",
    "Bosch",
    "Romaco",
    "Diosna",
    "Fette",
    "Korsch",
    "Uhlmann",
    "IMA",
    "Marchesini",
    "MG2",
]
EQUIPMENT_MANUFACTURERS = [
    "Mettler Toledo",
    "Sartorius",
    "Ohaus",
    "A&D",
    "Thermo Fisher",
    "Shimadzu",
]

PLANT_METADATA = {
    "Tablet Manufacturing Plant": {
        "manager": "Meera Shah",
        "capacity_utilization": 72,
        "emergency_contact": {
            "name": "Rajesh Patel",
            "role": "Plant Safety Officer",
            "phone": "+91-79-2657-1234",
            "email": "rajesh.patel@safety.plant.com",
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
            {
                "name": "ISO 9001:2015",
                "issued_by": "Bureau Veritas",
                "issue_date": "2024-06-01",
                "expiry_date": "2027-05-31",
                "status": "Valid",
            },
            {
                "name": "ISO 14001:2015",
                "issued_by": "Bureau Veritas",
                "issue_date": "2024-06-01",
                "expiry_date": "2027-05-31",
                "status": "Valid",
            },
            {
                "name": "ISO 45001:2018",
                "issued_by": "Bureau Veritas",
                "issue_date": "2024-06-01",
                "expiry_date": "2027-05-31",
                "status": "Valid",
            },
        ],
    }
}


def _gen_serial(prefix: str) -> str:
    year = datetime.now().year % 100
    rand = "".join(random.choices(string.digits, k=6))
    return f"{prefix}-{year}-{rand}"


def _guess_machine_fields(name: str) -> dict:
    category = "Other"
    manufacturer = "Unknown"
    for pattern, (cat, mfr) in MACHINE_CATEGORIES.items():
        if pattern.lower() in name.lower():
            category = cat
            manufacturer = mfr
            break
    model_no = f"{manufacturer.replace(' ', '')}-{name.replace(' ', '')[:12]}-{random.randint(100, 999)}"
    return {
        "serial_no": _gen_serial("SN"),
        "model_no": model_no,
        "type_category": category,
        "manufacturer": manufacturer,
    }


def _resolve_machine_updates(machine: dict) -> dict:
    name = str(machine.get("name") or "")
    machine_id = str(machine.get("machine_id") or "")
    updates: dict = {}

    if not machine.get("serial_no"):
        updates["serial_no"] = _gen_serial("SN")

    if not machine.get("model_no"):
        manufacturer = machine.get("manufacturer") or _guess_machine_fields(name)["manufacturer"]
        updates["model_no"] = f"{manufacturer.replace(' ', '')}-{name.replace(' ', '')[:12]}-{random.randint(100, 999)}"

    if not machine.get("type_category"):
        updates["type_category"] = _guess_machine_fields(name)["type_category"]

    if not machine.get("manufacturer"):
        updates["manufacturer"] = _guess_machine_fields(name)["manufacturer"]

    if not machine.get("manufacturer_country"):
        updates["manufacturer_country"] = "Unknown"

    if not machine.get("status"):
        updates["status"] = "Operational"

    if not machine.get("area_classification"):
        updates["area_classification"] = "Unclassified"

    if not machine.get("description"):
        updates["description"] = name or machine_id

    workstation_id = machine.get("workstation_id")
    if not workstation_id:
        for pattern, candidate_workstation in MACHINE_WORKSTATION_MAPPING.items():
            if pattern.lower() in name.lower():
                workstation_id = candidate_workstation
                updates["workstation_id"] = candidate_workstation
                break

    if workstation_id and not machine.get("plant_id"):
        updates["plant_id"] = "PLANT-OSD-001"
    if workstation_id and not machine.get("building_id"):
        updates["building_id"] = "BLDG-MFG-001"

    location_id = None
    if workstation_id:
        location_id = WORKSTATION_LOCATION_MAPPING.get(workstation_id)
    if location_id:
        location = LOCATION_DATA.get(location_id)
        if location:
            for key in (
                "location_id",
                "location_path",
                "location_name",
                "location_type",
                "location_level",
                "cleanroom_class",
                "plant_id",
                "building_id",
                "floor_id",
                "room_id",
                "bay_id",
            ):
                value = location.get(key) if key != "location_id" else location_id
                if key == "bay_id":
                    value = location_id if location["location_type"] == "bay" else None
                if key in {"plant_id", "building_id", "floor_id", "room_id"}:
                    if not machine.get(key):
                        updates[key] = value
                elif not machine.get(key):
                    updates[key] = value

    taxonomy = None
    for pattern, (category, manufacturer) in MACHINE_CATEGORIES.items():
        if pattern.lower() in name.lower():
            taxonomy = {"type_category": category, "manufacturer": manufacturer}
            break
    if taxonomy:
        updates.setdefault("type_category", taxonomy["type_category"])
        updates.setdefault("manufacturer", taxonomy["manufacturer"])

    for pattern, (family_id, class_id, subclass_id) in MACHINE_TAXONOMY_MAPPING.items():
        if pattern.lower() in name.lower():
            if not machine.get("family_id"):
                updates["family_id"] = family_id
            if not machine.get("class_id"):
                updates["class_id"] = class_id
            if not machine.get("subclass_id"):
                updates["subclass_id"] = subclass_id
            break

    return updates


def _guess_equipment_fields(name: str, equipment_type: str) -> dict:
    category = EQUIPMENT_CATEGORY_MAP.get(equipment_type, "Instrument")
    manufacturer = random.choice(EQUIPMENT_MANUFACTURERS)
    model_no = f"{manufacturer.replace(' ', '')}-{name.replace(' ', '')[:10]}-{random.randint(10, 99)}"
    return {
        "category": category,
        "manufacturer": manufacturer,
        "model_number": model_no,
        "serial_number": _gen_serial("EQ"),
    }


def _resolve_plant_updates(plant: dict) -> dict:
    plant_id = str(plant.get("id") or plant.get("plant_id") or "")
    defaults = PLANT_METADATA.get(str(plant.get("name") or "")) or PLANT_METADATA.get(plant_id)
    if not defaults:
        return {}

    updates: dict = {}
    for key, default_value in defaults.items():
        current_value = plant.get(key)
        if key in {"emergency_contact", "utilities"}:
            current_value = current_value if isinstance(current_value, dict) else {}
            merged = {**default_value, **current_value}
            if merged != current_value:
                updates[key] = merged
        elif key == "certifications":
            current_list = current_value if isinstance(current_value, list) else []
            current_names = {
                str(item.get("name") or "").strip().lower() for item in current_list if isinstance(item, dict)
            }
            canonical_names = {
                str(item.get("name") or "").strip().lower() for item in default_value if isinstance(item, dict)
            }
            if current_names != canonical_names or current_list != default_value:
                updates[key] = default_value
        elif current_value in (None, "", [], {}):
            updates[key] = default_value
    return updates


async def fix_machines(db):
    print("=== Fixing Machines ===")
    machines = await db.pharmaceutical_machines.find().to_list(None)
    updated = 0
    for m in machines:
        sets = _resolve_machine_updates(m)

        if sets:
            sets["updated_at"] = datetime.now(timezone.utc)
            doc = {**m, **sets}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("pharmaceutical_machines", doc)
            await db.pharmaceutical_machines.update_one({"machine_id": m["machine_id"]}, {"$set": sets})
            await safe_mirror(mirror_document("pharmaceutical_machines", doc, "update"))
            updated += 1
            print(f"  Updated: {m.get('machine_id')} - filled {sorted(k for k in sets.keys() if k != 'updated_at')}")
        else:
            doc = {**m}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("pharmaceutical_machines", doc)
            await db.pharmaceutical_machines.update_one(
                {"machine_id": m["machine_id"]},
                {"$set": {"embedding": doc["embedding"]}},
            )
            await safe_mirror(mirror_document("pharmaceutical_machines", doc, "update"))

    print(f"Machines: {updated} updated, {len(machines)} total")
    return updated


async def fix_equipment(db):
    print("\n=== Fixing Equipment ===")
    equipment = await db.pharmaceutical_equipment.find().to_list(None)
    updated = 0
    for e in equipment:
        sets = {}
        if not e.get("category"):
            etype = e.get("equipment_type", "")
            sets["category"] = EQUIPMENT_CATEGORY_MAP.get(etype, "Instrument")
        if not e.get("manufacturer"):
            sets["manufacturer"] = random.choice(EQUIPMENT_MANUFACTURERS)
        if not e.get("model_number"):
            mfr = e.get("manufacturer") or sets.get("manufacturer", "Unknown")
            name = e.get("name", "")
            sets["model_number"] = f"{mfr.replace(' ', '')}-{name.replace(' ', '')[:10]}-{random.randint(10, 99)}"
        if not e.get("serial_number"):
            sets["serial_number"] = _gen_serial("EQ")
        if not e.get("status"):
            sets["status"] = "active"
        if not e.get("notes"):
            sets["notes"] = e.get("name", "")
        if not e.get("plant_id"):
            sets["plant_id"] = "PLANT-OSD-001"
        if not e.get("building_id"):
            sets["building_id"] = "BLDG-MFG-001"

        if sets:
            sets["updated_at"] = datetime.now(timezone.utc)
            doc = {**e, **sets}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("pharmaceutical_equipment", doc)
            await db.pharmaceutical_equipment.update_one({"equipment_id": e["equipment_id"]}, {"$set": sets})
            await safe_mirror(mirror_document("pharmaceutical_equipment", doc, "update"))
            updated += 1
            print(f"  Updated: {e.get('equipment_id')} - filled {list(sets.keys())[:-1]}")
        else:
            doc = {**e}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("pharmaceutical_equipment", doc)
            await db.pharmaceutical_equipment.update_one(
                {"equipment_id": e["equipment_id"]},
                {"$set": {"embedding": doc["embedding"]}},
            )
            await safe_mirror(mirror_document("pharmaceutical_equipment", doc, "update"))

    print(f"Equipment: {updated} updated, {len(equipment)} total")
    return updated


async def fix_plants(db):
    print("\n=== Fixing Plants ===")
    plants = await db.industrial_plants.find().to_list(None)
    updated = 0
    for plant in plants:
        sets = _resolve_plant_updates(plant)

        if sets:
            sets["updated_at"] = datetime.now(timezone.utc)
            doc = {**plant, **sets}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("industrial_plants", doc)
            await db.industrial_plants.update_one({"id": plant["id"]}, {"$set": sets})
            await safe_mirror(mirror_document("industrial_plants", doc, "update"))
            updated += 1
            print(f"  Updated: {plant.get('id')} - filled {sorted(k for k in sets.keys() if k != 'updated_at')}")
        else:
            doc = {**plant}
            doc.pop("_id", None)
            doc["embedding"] = build_embedding("industrial_plants", doc)
            await db.industrial_plants.update_one({"id": plant["id"]}, {"$set": {"embedding": doc["embedding"]}})
            await safe_mirror(mirror_document("industrial_plants", doc, "update"))

    print(f"Plants: {updated} updated, {len(plants)} total")
    return updated


async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    m_count = await db.pharmaceutical_machines.count_documents({})
    e_count = await db.pharmaceutical_equipment.count_documents({})
    p_count = await db.industrial_plants.count_documents({})
    print(f"Found {p_count} plants, {m_count} machines, {e_count} equipment in {settings.DB_NAME}\n")

    plants_updated = await fix_plants(db)
    machines_updated = await fix_machines(db)
    equipment_updated = await fix_equipment(db)

    print(f"\n=== Summary ===")
    print(f"Plants updated: {plants_updated}/{p_count}")
    print(f"Machines updated: {machines_updated}/{m_count}")
    print(f"Equipment updated: {equipment_updated}/{e_count}")
    print("Embeddings rebuilt and Neo4j mirrored for all documents.")

    await close_neo4j_mirror_driver()
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
