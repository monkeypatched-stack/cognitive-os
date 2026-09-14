#!/usr/bin/env python3
"""Seed work orders, spare parts, and raw materials."""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings
from services.common.embeddings import build_embedding


def utc_now():
    return datetime.now(timezone.utc)


WORK_ORDER_TYPES = ["Maintenance", "Inspection", "Calibration", "Cleaning", "Repair"]
WORK_ORDER_STATUSES = ["Todo", "In Progress", "Completed", "On Hold"]
WORK_ORDER_PRIORITIES = ["Low", "Medium", "High", "Critical"]
PART_CATEGORIES = [
    "Mechanical",
    "Electrical",
    "Hydraulic",
    "Pneumatic",
    "Electronic",
    "Consumable",
]
PART_STATUSES = ["In Stock", "Low Stock", "Out of Stock", "On Order"]
MATERIAL_TYPES = [
    "Raw Material",
    "Excipient",
    "Solvent",
    "API",
    "Lubricant",
    "Packaging Material",
]


async def seed_work_orders(db, machines, equipment):
    collection = db["work_orders"]
    now = utc_now()
    work_orders = []

    for i, machine in enumerate(machines[:10], 1):
        machine_id = machine.get("machine_id")
        machine_name = machine.get("name")
        workstation_id = machine.get("workstation_id")

        wo_id = f"WO-{machine_id}-{i:03d}"
        wo_type = WORK_ORDER_TYPES[i % len(WORK_ORDER_TYPES)]
        wo = {
            "_id": wo_id,
            "work_order_id": wo_id,
            "title": f"{wo_type} - {machine_name}",
            "message": f"Scheduled {wo_type.lower()} for {machine_name}",
            "work_order_type": wo_type,
            "status": WORK_ORDER_STATUSES[i % len(WORK_ORDER_STATUSES)],
            "priority": WORK_ORDER_PRIORITIES[i % len(WORK_ORDER_PRIORITIES)],
            "machine_id": machine_id,
            "workstation_id": workstation_id,
            "assigned_to": f"WRK-WS-{i:03d}",
            "created_at": now - timedelta(days=i),
            "expected_completion": now + timedelta(days=i),
            "updated_at": now,
        }
        wo["embedding"] = build_embedding("work_orders", wo)
        work_orders.append(wo)

    for eq in equipment[:5]:
        eq_id = eq.get("equipment_id")
        eq_name = eq.get("name")
        machine_id = eq.get("machine_id")

        wo_id = f"WO-{eq_id}-001"
        wo = {
            "_id": wo_id,
            "work_order_id": wo_id,
            "title": f"Calibration - {eq_name}",
            "message": f"Scheduled calibration for {eq_name}",
            "work_order_type": "Calibration",
            "status": "Todo",
            "priority": "Medium",
            "equipment_id": eq_id,
            "machine_id": machine_id,
            "created_at": now,
            "expected_completion": now + timedelta(days=7),
            "updated_at": now,
        }
        wo["embedding"] = build_embedding("work_orders", wo)
        work_orders.append(wo)

    if work_orders:
        for wo in work_orders:
            await collection.update_one({"_id": wo["_id"]}, {"$set": wo}, upsert=True)
        print(f"  Inserted/updated {len(work_orders)} work orders")
    return len(work_orders)


async def seed_spare_parts(db, machines):
    collection = db["machine_parts"]
    now = utc_now()
    parts = []

    part_templates = [
        {"part_name": "Impeller", "category": "Mechanical", "manufacturer": "GEA"},
        {
            "part_name": "Heating Element",
            "category": "Electrical",
            "manufacturer": "Watlow",
        },
        {
            "part_name": "Pressure Sensor",
            "category": "Electronic",
            "manufacturer": "Endress+Hauser",
        },
        {
            "part_name": "Pneumatic Valve",
            "category": "Pneumatic",
            "manufacturer": "SMC",
        },
        {
            "part_name": "Hydraulic Pump",
            "category": "Hydraulic",
            "manufacturer": "Bosch",
        },
        {
            "part_name": "Filter Cartridge",
            "category": "Consumable",
            "manufacturer": "Pall",
        },
        {
            "part_name": "Bearing Assembly",
            "category": "Mechanical",
            "manufacturer": "SKF",
        },
        {
            "part_name": "Motor Controller",
            "category": "Electrical",
            "manufacturer": "Siemens",
        },
    ]

    for machine in machines:
        machine_id = machine.get("machine_id")
        machine_name = machine.get("name")

        for j, template in enumerate(part_templates[:3], 1):
            part_id = f"PART-{machine_id}-{j:03d}"
            part = {
                "_id": part_id,
                "id": part_id,
                "part_id": part_id,
                "machine_id": machine_id,
                "part_number": f"PN-{machine_id}-{j:03d}",
                "part_name": f"{template['part_name']} - {machine_name}",
                "category": template["category"],
                "status": PART_STATUSES[j % len(PART_STATUSES)],
                "manufacturer": template["manufacturer"],
                "notes": f"Replacement part for {machine_name}",
            }
            part["embedding"] = build_embedding("machine_parts", part)
            parts.append(part)

    if parts:
        for part in parts:
            await collection.update_one({"_id": part["_id"]}, {"$set": part}, upsert=True)
        print(f"  Inserted/updated {len(parts)} spare parts")
    return len(parts)


async def seed_raw_materials(db):
    collection = db["raw_materials"]
    now = utc_now()
    materials = []

    raw_materials_data = [
        {
            "name": "Paracetamol (Acetaminophen)",
            "type": "API",
            "grade": "USP",
            "unit": "kg",
        },
        {
            "name": "Microcrystalline Cellulose",
            "type": "Excipient",
            "grade": "PH-102",
            "unit": "kg",
        },
        {
            "name": "Lactose Monohydrate",
            "type": "Excipient",
            "grade": "Spray Dried",
            "unit": "kg",
        },
        {
            "name": "Magnesium Stearate",
            "type": "Lubricant",
            "grade": "Vegetable",
            "unit": "kg",
        },
        {
            "name": "Povidone (PVP K30)",
            "type": "Excipient",
            "grade": "Pharma",
            "unit": "kg",
        },
        {
            "name": "Starch (Maize)",
            "type": "Excipient",
            "grade": "Native",
            "unit": "kg",
        },
        {
            "name": "Colloidal Silicon Dioxide",
            "type": "Glidant",
            "grade": "Aerosil 200",
            "unit": "kg",
        },
        {"name": "Talc", "type": "Lubricant", "grade": "Pharma", "unit": "kg"},
        {
            "name": "Hydroxypropyl Methylcellulose",
            "type": "Coating Agent",
            "grade": "HPMC E5",
            "unit": "kg",
        },
        {
            "name": "Polyethylene Glycol",
            "type": "Plasticizer",
            "grade": "PEG 6000",
            "unit": "kg",
        },
        {"name": "Purified Water", "type": "Solvent", "grade": "Pharma", "unit": "L"},
        {"name": "Isopropyl Alcohol", "type": "Solvent", "grade": "99%", "unit": "L"},
        {
            "name": "Titanium Dioxide",
            "type": "Coating Agent",
            "grade": "Pharma",
            "unit": "kg",
        },
        {
            "name": "Iron Oxide Yellow",
            "type": "Colorant",
            "grade": "Pharma",
            "unit": "kg",
        },
        {
            "name": "Ethyl Cellulose",
            "type": "Film Former",
            "grade": "N-200",
            "unit": "kg",
        },
    ]

    for i, rm in enumerate(raw_materials_data, 1):
        mat_id = f"RM-{i:03d}"
        material = {
            "_id": mat_id,
            "material_id": mat_id,
            "name": rm["name"],
            "material_type": rm["type"],
            "grade": rm["grade"],
            "quantity": round(50 + (i * 13.7) % 200, 1),
            "quantity_units": rm["unit"],
            "current_stock": round(20 + (i * 7.3) % 150, 1),
            "minimum_stock": round(10 + (i * 2.1) % 30, 1),
            "maximum_stock": round(100 + (i * 15.5) % 300, 1),
            "lot_no": f"LOT-2026-{i:04d}",
            "storage_temperature": "15-25 °C",
            "storage_conditions": "Store in a dry place, protect from light",
            "date_received": (now - timedelta(days=i * 5)).isoformat(),
        }
        material["embedding"] = build_embedding("raw_materials", material)
        materials.append(material)

    if materials:
        for mat in materials:
            await collection.update_one({"_id": mat["_id"]}, {"$set": mat}, upsert=True)
        print(f"  Inserted/updated {len(materials)} raw materials")
    return len(materials)


async def sync_to_neo4j(db, driver):
    async with driver.session() as session:
        # Work orders
        work_orders = await db["work_orders"].find({}).to_list(200)
        for wo in work_orders:
            await session.run(
                """
                MERGE (w:WorkOrder {entity_id: $wo_id})
                SET w.title = $title,
                    w.type = $wo_type,
                    w.status = $status,
                    w.priority = $priority,
                    w.updated_at = datetime()
                """,
                wo_id=wo.get("work_order_id"),
                title=wo.get("title"),
                wo_type=wo.get("work_order_type"),
                status=wo.get("status"),
                priority=wo.get("priority"),
            )
            if wo.get("machine_id"):
                await session.run(
                    """
                    MATCH (w:WorkOrder {entity_id: $wo_id})
                    MATCH (m:Machine {machine_id: $machine_id})
                    MERGE (m)-[:HAS_WORK_ORDER]->(w)
                    """,
                    wo_id=wo.get("work_order_id"),
                    machine_id=wo.get("machine_id"),
                )
        print(f"  Synced {len(work_orders)} work orders to Neo4j")

        # Spare parts
        parts = await db["machine_parts"].find({}).to_list(500)
        for part in parts:
            await session.run(
                """
                MERGE (p:Part {entity_id: $part_id})
                SET p.name = $name,
                    p.category = $category,
                    p.status = $status,
                    p.updated_at = datetime()
                """,
                part_id=part.get("part_id"),
                name=part.get("part_name"),
                category=part.get("category"),
                status=part.get("status"),
            )
            if part.get("machine_id"):
                await session.run(
                    """
                    MATCH (p:Part {entity_id: $part_id})
                    MATCH (m:Machine {machine_id: $machine_id})
                    MERGE (m)-[:HAS_PART]->(p)
                    """,
                    part_id=part.get("part_id"),
                    machine_id=part.get("machine_id"),
                )
        print(f"  Synced {len(parts)} spare parts to Neo4j")

        # Raw materials
        materials = await db["raw_materials"].find({}).to_list(200)
        for mat in materials:
            await session.run(
                """
                MERGE (r:RawMaterial {entity_id: $mat_id})
                SET r.name = $name,
                    r.type = $type,
                    r.grade = $grade,
                    r.updated_at = datetime()
                """,
                mat_id=mat.get("material_id"),
                name=mat.get("name"),
                type=mat.get("material_type"),
                grade=mat.get("grade"),
            )
        print(f"  Synced {len(materials)} raw materials to Neo4j")


async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    machines = await db["pharmaceutical_machines"].find({}).to_list(200)
    equipment = await db["pharmaceutical_equipment"].find({}).to_list(500)
    print(f"Found {len(machines)} machines, {len(equipment)} equipment")

    print("\nSeeding work orders...")
    wo_count = await seed_work_orders(db, machines, equipment)

    print("\nSeeding spare parts...")
    parts_count = await seed_spare_parts(db, machines)

    print("\nSeeding raw materials...")
    materials_count = await seed_raw_materials(db)

    print("\nSyncing to Neo4j...")
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
    await sync_to_neo4j(db, driver)
    await driver.close()

    # Verify
    print("\n--- Verification ---")
    total_wo = await db["work_orders"].count_documents({})
    total_parts = await db["machine_parts"].count_documents({})
    total_materials = await db["raw_materials"].count_documents({})
    print(f"Work orders: {total_wo}")
    print(f"Spare parts: {total_parts}")
    print(f"Raw materials: {total_materials}")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())
