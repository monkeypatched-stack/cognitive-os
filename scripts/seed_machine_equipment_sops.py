#!/usr/bin/env python3
"""Seed SOPs for machines and equipment."""

import asyncio
import sys
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings
from services.common.embeddings import build_embedding

MACHINE_SOP_TEMPLATES = [
    {
        "title": "Installation Qualification (IQ)",
        "prefix": "IQ",
        "description": "Verifies that equipment is installed correctly per manufacturer specifications.",
    },
    {
        "title": "Operational Qualification (OQ)",
        "prefix": "OQ",
        "description": "Verifies that equipment operates within specified limits under normal conditions.",
    },
    {
        "title": "Performance Qualification (PQ)",
        "prefix": "PQ",
        "description": "Verifies that equipment consistently performs as intended under real production conditions.",
    },
    {
        "title": "Maintenance SOP",
        "prefix": "MAINT",
        "description": "Preventive and corrective maintenance procedures for the machine.",
    },
    {
        "title": "Cleaning SOP",
        "prefix": "CLEAN",
        "description": "Cleaning and sanitization procedures to prevent cross-contamination.",
    },
]

EQUIPMENT_SOP_TEMPLATES = [
    {
        "title": "Installation SOP",
        "prefix": "INSTALL",
        "description": "Installation and setup procedures for the equipment.",
    },
    {
        "title": "Maintenance SOP",
        "prefix": "MAINT",
        "description": "Preventive and corrective maintenance procedures for the equipment.",
    },
    {
        "title": "Cleaning SOP",
        "prefix": "CLEAN",
        "description": "Cleaning and sanitization procedures for the equipment.",
    },
    {
        "title": "Calibration SOP",
        "prefix": "CALIB",
        "description": "Calibration procedures and frequency requirements for the equipment.",
    },
]


async def seed_sops():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    now = datetime.now(timezone.utc)
    sops_to_insert = []

    # Machine SOPs
    machines = await db["pharmaceutical_machines"].find({}).to_list(200)
    print(f"Found {len(machines)} machines")

    for machine in machines:
        machine_id = machine.get("machine_id") or machine.get("_id")
        machine_name = machine.get("name") or machine_id
        workstation_id = machine.get("workstation_id")

        for template in MACHINE_SOP_TEMPLATES:
            sop_id = f"SOP-{machine_id}-{template['prefix']}"
            sop = {
                "_id": sop_id,
                "id": sop_id,
                "title": f"{machine_name} - {template['title']}",
                "description": template["description"],
                "version": "Rev 01",
                "entity_type": "Machine",
                "entity_id": machine_id,
                "entity_name": machine_name,
                "workstation_id": workstation_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            sop["embedding"] = build_embedding("industrial_sops", sop)
            sops_to_insert.append(sop)

    # Equipment SOPs
    equipment = await db["pharmaceutical_equipment"].find({}).to_list(500)
    print(f"Found {len(equipment)} equipment")

    for eq in equipment:
        eq_id = eq.get("equipment_id") or eq.get("_id")
        eq_name = eq.get("name") or eq_id
        machine_id = eq.get("machine_id")

        for template in EQUIPMENT_SOP_TEMPLATES:
            sop_id = f"SOP-{eq_id}-{template['prefix']}"
            sop = {
                "_id": sop_id,
                "id": sop_id,
                "title": f"{eq_name} - {template['title']}",
                "description": template["description"],
                "version": "Rev 01",
                "entity_type": "Equipment",
                "entity_id": eq_id,
                "entity_name": eq_name,
                "machine_id": machine_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
            sop["embedding"] = build_embedding("industrial_sops", sop)
            sops_to_insert.append(sop)

    if sops_to_insert:
        # Use upsert to avoid duplicates
        collection = db["industrial_sops"]
        inserted = 0
        for sop in sops_to_insert:
            await collection.update_one(
                {"_id": sop["_id"]},
                {"$set": sop},
                upsert=True,
            )
            inserted += 1
        print(f"Inserted/updated {inserted} SOPs")
    else:
        print("No SOPs to insert")

    # Verify
    total = await db["industrial_sops"].count_documents({})
    machine_sops = await db["industrial_sops"].count_documents({"entity_type": "Machine"})
    equipment_sops = await db["industrial_sops"].count_documents({"entity_type": "Equipment"})
    print(f"\nTotal SOPs: {total}")
    print(f"Machine SOPs: {machine_sops}")
    print(f"Equipment SOPs: {equipment_sops}")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed_sops())
