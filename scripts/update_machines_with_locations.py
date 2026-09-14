#!/usr/bin/env python3
"""Update machines and equipment with location references."""

import asyncio
import sys
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

# Mapping of workstations to locations
WORKSTATION_LOCATION_MAPPING = {
    # Ground Floor - Material Handling
    "WS-DISP-001": "LOC-BAY-001",  # Dispensing Bay A
    "WS-DISP-002": "LOC-BAY-002",  # Dispensing Bay B
    # First Floor - Granulation & Drying
    "WS-GRAN-001": "LOC-BAY-003",  # Granulation Bay 1
    # Second Floor - Tablet Manufacturing
    "WS-COMP-001": "LOC-BAY-004",  # Compression Bay 1
    # Third Floor - Packaging
    "WS-PACK-001": "LOC-BAY-005",  # Blister Packaging Bay 1
}

# Machine to workstation mapping
MACHINE_WORKSTATION_MAPPING = {
    "Analytical Balance": "WS-DISP-001",
    "Rapid Mixer Granulator RMG-01": "WS-GRAN-001",
    "Fluid Bed Dryer FBD-01": "WS-GRAN-001",
    "Bin Blender BB-01": "WS-GRAN-001",
    "Rotary Tablet Press RTP-01": "WS-COMP-001",
    "Tablet Weight Checker TWC-001": "WS-COMP-001",
    "Tablet Hardness Tester THT-001": "WS-COMP-001",
    "Tablet Thickness Tester TTT-001": "WS-COMP-001",
    "Tablet Friability Tester TFT-001": "WS-COMP-001",
    "Tablet Disintegration Tester TDT-001": "WS-COMP-001",
    "Tablet Coater TC-001": "WS-COMP-001",
    "Blister Packaging Machine BPM-001": "WS-PACK-001",
    "Cartoning Machine CM-01": "WS-PACK-001",
    "Labeling Machine LM-01": "WS-PACK-001",
}


async def update_machines_with_locations():
    """Update machines with location references."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Updating machines with location references...")

    # Get all locations for reference
    locations = await db.plant_locations.find().to_list(None)
    location_map = {loc["id"]: loc for loc in locations}

    # Update pharmaceutical machines
    machines = await db.pharmaceutical_machines.find().to_list(None)
    updated_count = 0

    for machine in machines:
        machine_name = machine["name"]
        workstation_id = machine.get("workstation_id")

        # Find location based on workstation or machine name
        location_id = None
        location_path = None

        # Try to find by workstation first
        if workstation_id and workstation_id in WORKSTATION_LOCATION_MAPPING:
            location_id = WORKSTATION_LOCATION_MAPPING[workstation_id]
        # Try to find by machine name
        elif machine_name in MACHINE_WORKSTATION_MAPPING:
            potential_workstation = MACHINE_WORKSTATION_MAPPING[machine_name]
            if potential_workstation in WORKSTATION_LOCATION_MAPPING:
                location_id = WORKSTATION_LOCATION_MAPPING[potential_workstation]

        if location_id and location_id in location_map:
            location = location_map[location_id]
            location_path = location["path"]

            # Build complete location hierarchy
            location_data = {
                "location_id": location_id,
                "location_path": location_path,
                "location_name": location["name"],
                "location_type": location["type"],
                "location_level": location["level"],
                "plant_id": "PLANT-OSD-001",
                "building_id": "BLDG-MFG-001",
                "floor_id": (location.get("parent_id") if location["level"] == 5 else None),
                "room_id": location["parent_id"] if location["level"] == 5 else None,
                "bay_id": location_id if location["type"] == "bay" else None,
                "cleanroom_class": location.get("cleanroom_class"),
                "workstation_id": workstation_id or machine.get("workstation_id"),
                "updated_at": datetime.now(timezone.utc),
            }

            result = await db.pharmaceutical_machines.update_one({"_id": machine["_id"]}, {"$set": location_data})

            if result.modified_count > 0:
                updated_count += 1
                print(f"Updated {machine_name} with location: {location_path}")
        else:
            print(f"No location found for {machine_name} (workstation: {workstation_id})")

    print(f"\nUpdated {updated_count} machines with location references.")
    return updated_count


async def update_equipment_with_locations():
    """Update equipment with location references."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("\nUpdating equipment with location references...")

    # Get all locations for reference
    locations = await db.plant_locations.find().to_list(None)
    location_map = {loc["id"]: loc for loc in locations}

    # Update pharmaceutical equipment
    equipment = await db.pharmaceutical_equipment.find().to_list(None)
    updated_count = 0

    for equip in equipment:
        machine_id = equip.get("machine_id")

        if machine_id:
            # Find the machine to get its location
            machine = await db.pharmaceutical_machines.find_one(
                {"machine_id": machine_id, "location_id": {"$exists": True}}
            )

            if machine:
                location_data = {
                    "location_id": machine["location_id"],
                    "location_path": machine["location_path"],
                    "location_name": machine["location_name"],
                    "location_type": machine["location_type"],
                    "plant_id": machine["plant_id"],
                    "building_id": machine["building_id"],
                    "floor_id": machine["floor_id"],
                    "room_id": machine["room_id"],
                    "bay_id": machine["bay_id"],
                    "cleanroom_class": machine["cleanroom_class"],
                    "updated_at": datetime.now(timezone.utc),
                }

                result = await db.pharmaceutical_equipment.update_one({"_id": equip["_id"]}, {"$set": location_data})

                if result.modified_count > 0:
                    updated_count += 1
                    print(
                        f"Updated equipment {equip['name']} (machine {machine_id}) with location: {machine['location_path']}"
                    )

    print(f"\nUpdated {updated_count} equipment items with location references.")
    return updated_count


async def main():
    """Main function to update locations."""
    print("Starting machine and equipment location update...")
    print("=" * 60)

    # Step 1: Update machines with locations
    machines_updated = await update_machines_with_locations()

    # Step 2: Update equipment with locations
    equipment_updated = await update_equipment_with_locations()

    print("\n" + "=" * 60)
    print(f"SUMMARY:")
    print(f"  Machines updated: {machines_updated}")
    print(f"  Equipment updated: {equipment_updated}")
    print(f"  Total updates: {machines_updated + equipment_updated}")
    print("\nLocation update completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
