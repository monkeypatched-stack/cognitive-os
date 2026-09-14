#!/usr/bin/env python3
"""Update pharmaceutical machines and equipment with taxonomy references and sync to Neo4j."""

import asyncio
import sys
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from neo4j import AsyncGraphDatabase

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

# Taxonomy mapping for pharmaceutical machines
MACHINE_TAXONOMY_MAPPING = {
    # Primary Processing Equipment
    "Rapid Mixer Granulator": {
        "family_id": "fam_primary_processing",
        "class_id": "cls_mixing",
        "subclass_id": "sub_high_shear_mixer",
    },
    "Granule Sampling Station": {
        "family_id": "fam_primary_processing",
        "class_id": "cls_mixing",
        "subclass_id": "sub_high_shear_mixer",
    },
    "Fluid Bed Dryer": {
        "family_id": "fam_primary_processing",
        "class_id": "cls_milling",
        "subclass_id": "sub_jet_mill",
    },
    "Bin Blender": {
        "family_id": "fam_primary_processing",
        "class_id": "cls_mixing",
        "subclass_id": "sub_ribbon_blender",
    },
    "Analytical Balance": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    # Dosage Form Production Equipment
    "Rotary Tablet Press": {
        "family_id": "fam_dosage_form",
        "class_id": "cls_tablet_manufacturing",
        "subclass_id": "sub_rotary_tablet_press",
    },
    "Tablet Weight Checker": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    "Tablet Hardness Tester": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    "Tablet Thickness Tester": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    "Tablet Friability Tester": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    "Tablet Disintegration Tester": {
        "family_id": "fam_qc_testing",
        "class_id": "cls_particle_analysis",
        "subclass_id": "sub_particle_counter",
    },
    "Tablet Coater": {
        "family_id": "fam_dosage_form",
        "class_id": "cls_tablet_manufacturing",
        "subclass_id": "sub_tablet_coater",
    },
    "Capsule Filling Machine": {
        "family_id": "fam_dosage_form",
        "class_id": "cls_capsule_manufacturing",
        "subclass_id": "sub_capsule_filler",
    },
    "Capsule Polishing Machine": {
        "family_id": "fam_dosage_form",
        "class_id": "cls_capsule_manufacturing",
        "subclass_id": "sub_capsule_polisher",
    },
    # Packaging Equipment
    "Blister Packaging Machine": {
        "family_id": "fam_packaging",
        "class_id": "cls_primary_packaging",
        "subclass_id": "sub_blister_machine",
    },
    "Cartoning Machine": {
        "family_id": "fam_packaging",
        "class_id": "cls_secondary_packaging",
        "subclass_id": "sub_cartoning_machine",
    },
    "Labeling Machine": {
        "family_id": "fam_packaging",
        "class_id": "cls_secondary_packaging",
        "subclass_id": "sub_labeling_machine",
    },
}


async def update_machines_with_taxonomy():
    """Update pharmaceutical machines with taxonomy references."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Updating pharmaceutical machines with taxonomy references...")

    # Get all taxonomy data
    families = await db.equipment_families.find().to_list(None)
    classes = await db.equipment_classes.find().to_list(None)
    subclasses = await db.equipment_subclasses.find().to_list(None)

    family_map = {f["id"]: f for f in families}
    class_map = {c["id"]: c for c in classes}
    subclass_map = {s["id"]: s for s in subclasses}

    # Update machines
    machines = await db.pharmaceutical_machines.find().to_list(None)
    updated_count = 0

    for machine in machines:
        machine_name = machine["name"]

        # Find matching taxonomy (try exact match first, then partial match)
        taxonomy = None
        for key in MACHINE_TAXONOMY_MAPPING:
            if key in machine_name:
                taxonomy = MACHINE_TAXONOMY_MAPPING[key]
                break

        if taxonomy:
            # Add taxonomy references
            update_data = {
                "taxonomy_family_id": taxonomy["family_id"],
                "taxonomy_class_id": taxonomy["class_id"],
                "taxonomy_subclass_id": taxonomy["subclass_id"],
                "taxonomy_family_name": family_map[taxonomy["family_id"]]["name"],
                "taxonomy_class_name": class_map[taxonomy["class_id"]]["name"],
                "taxonomy_subclass_name": subclass_map[taxonomy["subclass_id"]]["name"],
                "updated_at": datetime.now(timezone.utc),
            }

            result = await db.pharmaceutical_machines.update_one({"_id": machine["_id"]}, {"$set": update_data})

            if result.modified_count > 0:
                updated_count += 1
                print(
                    f"Updated {machine_name} with taxonomy: {update_data['taxonomy_family_name']} > {update_data['taxonomy_class_name']} > {update_data['taxonomy_subclass_name']}"
                )

    print(f"\nUpdated {updated_count} machines with taxonomy references.")
    return updated_count


async def sync_to_neo4j():
    """Sync taxonomy and machine data to Neo4j."""
    print("\nSyncing data to Neo4j...")

    # Connect to MongoDB
    mongo_client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = mongo_client[settings.DB_NAME]

    # Connect to Neo4j
    neo4j_uri = settings.NEO4J_URI or "bolt://localhost:7687"
    neo4j_user = settings.NEO4J_USER or "neo4j"
    neo4j_password = settings.NEO4J_PASSWORD or "administrator"

    neo4j_driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    async with neo4j_driver.session() as session:
        # Clear existing taxonomy data
        await session.run("MATCH (n:EquipmentFamily) DELETE n")
        await session.run("MATCH (n:EquipmentClass) DELETE n")
        await session.run("MATCH (n:EquipmentSubclass) DELETE n")
        await session.run("MATCH (n:PharmaceuticalMachine) DELETE n")

        print("Cleared existing Neo4j data.")

        # Import taxonomy families
        families = await db.equipment_families.find().to_list(None)
        for family in families:
            await session.run(
                """
                CREATE (f:EquipmentFamily {
                    id: $id,
                    name: $name,
                    description: $description,
                    tags: $tags,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
            """,
                id=family["id"],
                name=family["name"],
                description=family.get("description", ""),
                tags=family.get("tags", []),
                created_at=family["created_at"].isoformat(),
                updated_at=family["updated_at"].isoformat(),
            )

        print(f"Imported {len(families)} equipment families to Neo4j.")

        # Import taxonomy classes
        classes = await db.equipment_classes.find().to_list(None)
        for cls in classes:
            await session.run(
                """
                MATCH (f:EquipmentFamily {id: $family_id})
                CREATE (c:EquipmentClass {
                    id: $id,
                    name: $name,
                    description: $description,
                    tags: $tags,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                CREATE (f)-[:HAS_CLASS]->(c)
            """,
                id=cls["id"],
                name=cls["name"],
                family_id=cls["family_id"],
                description=cls.get("description", ""),
                tags=cls.get("tags", []),
                created_at=cls["created_at"].isoformat(),
                updated_at=cls["updated_at"].isoformat(),
            )

        print(f"Imported {len(classes)} equipment classes to Neo4j.")

        # Import taxonomy subclasses
        subclasses = await db.equipment_subclasses.find().to_list(None)
        for subclass in subclasses:
            await session.run(
                """
                MATCH (c:EquipmentClass {id: $class_id})
                CREATE (s:EquipmentSubclass {
                    id: $id,
                    name: $name,
                    description: $description,
                    tags: $tags,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                CREATE (c)-[:HAS_SUBCLASS]->(s)
            """,
                id=subclass["id"],
                name=subclass["name"],
                class_id=subclass["class_id"],
                description=subclass.get("description", ""),
                tags=subclass.get("tags", []),
                created_at=subclass["created_at"].isoformat(),
                updated_at=subclass["updated_at"].isoformat(),
            )

        print(f"Imported {len(subclasses)} equipment subclasses to Neo4j.")

        # Import machines with taxonomy relationships
        machines = await db.pharmaceutical_machines.find({"taxonomy_family_id": {"$exists": True}}).to_list(None)

        for machine in machines:
            await session.run(
                """
                MATCH (s:EquipmentSubclass {id: $subclass_id})
                CREATE (m:PharmaceuticalMachine {
                    machine_id: $machine_id,
                    name: $name,
                    type_category: $type_category,
                    manufacturer: $manufacturer,
                    model_no: $model_no,
                    workstation_id: $workstation_id,
                    plant_id: $plant_id
                })
                CREATE (m)-[:BELONGS_TO_SUBCLASS]->(s)
            """,
                machine_id=machine["machine_id"],
                name=machine["name"],
                type_category=machine.get("type_category", ""),
                manufacturer=machine.get("manufacturer", ""),
                model_no=machine.get("model_no", ""),
                workstation_id=machine.get("workstation_id", ""),
                plant_id=machine.get("plant_id", ""),
                subclass_id=machine["taxonomy_subclass_id"],
            )

        print(f"Imported {len(machines)} pharmaceutical machines to Neo4j with taxonomy relationships.")

    await neo4j_driver.close()
    print("Neo4j sync completed successfully!")


async def main():
    """Main function to update machines and sync to Neo4j."""
    print("Starting pharmaceutical machine taxonomy update and Neo4j sync...")

    # Step 1: Update machines with taxonomy
    updated_count = await update_machines_with_taxonomy()

    if updated_count > 0:
        # Step 2: Sync to Neo4j
        await sync_to_neo4j()
    else:
        print("No machines were updated with taxonomy. Skipping Neo4j sync.")

    print("\nProcess completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
