#!/usr/bin/env python3
"""Complete update script to ensure ALL machines and equipment have locations and required fields."""

import asyncio
import sys
from datetime import datetime, timezone
import random
import string

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

# Complete workstation to location mapping
WORKSTATION_LOCATION_MAPPING = {
    # Ground Floor - Material Handling
    "WS-DISP-001": "LOC-BAY-001",  # Dispensing Bay A
    "WS-DISP-002": "LOC-BAY-002",  # Dispensing Bay B
    # First Floor - Granulation & Drying
    "WS-GRAN-001": "LOC-BAY-003",  # Granulation Bay 1
    "WS-GRAN-002": "LOC-BAY-003",  # Granulation Bay 1 (backup)
    "WS-DRY-001": "LOC-ROOM-006",  # Drying Room
    "WS-MILL-001": "LOC-ROOM-004",  # Granulation Suite 1
    "WS-BLEND-001": "LOC-ROOM-005",  # Granulation Suite 2
    # Second Floor - Tablet Manufacturing
    "WS-COMP-001": "LOC-BAY-004",  # Compression Bay 1
    "WS-COMP-002": "LOC-BAY-004",  # Compression Bay 1 (backup)
    "WS-COMP-003": "LOC-ROOM-009",  # Coating Room
    "WS-COAT-001": "LOC-ROOM-009",  # Coating Room
    "WS-COAT-002": "LOC-ROOM-009",  # Coating Room
    # Third Floor - Packaging
    "WS-PACK-001": "LOC-BAY-005",  # Blister Packaging Bay 1
    "WS-PACK-002": "LOC-BAY-005",  # Blister Packaging Bay 1 (backup)
    "WS-PACK-003": "LOC-ROOM-012",  # Bottle Packaging Room
    "WS-PACK-004": "LOC-ROOM-013",  # Secondary Packaging Room
}

# Equipment categories by machine type
EQUIPMENT_CATEGORIES = {
    "Analytical Balance": "Weighing & Measurement",
    "Floor Scale": "Weighing & Measurement",
    "High Shear Granulator": "Granulation",
    "Fluid Bed Dryer": "Drying",
    "Cone Mill": "Milling",
    "Bin Blender": "Blending",
    "Tablet Press": "Compression",
    "Tablet Deduster": "Finishing",
    "Metal Detector": "Quality Control",
    "Coating Pan": "Coating",
    "Solution Preparation Vessel": "Coating",
    "Blister Packaging Machine": "Packaging",
    "Cartoner": "Packaging",
    "Serialization System": "Packaging",
    "Checkweigher": "Packaging",
}

# Common manufacturers
MANUFACTURERS = {
    "Weighing & Measurement": ["Mettler Toledo", "Sartorius", "Ohaus", "A&D"],
    "Granulation": ["GEA", "Bosch", "Romaco", "Diosna"],
    "Drying": ["GEA", "Hosokawa", "Glatt", "Vector"],
    "Milling": ["Frewitt", "Quadro", "Fitzpatrick", "IKA"],
    "Blending": ["GEA", "Patterson-Kelley", "Gemco", "Paul O. Abbe"],
    "Compression": ["Fette", "Korsch", "Kikusui", "Cadmach"],
    "Finishing": ["Vector", "Key International", "Erweka", "Copley"],
    "Quality Control": ["Mettler Toledo", "Sartorius", "Thermofisher", "Shimadzu"],
    "Coating": ["Thomas Engineering", "O'Hara", "Vector", "Driam"],
    "Packaging": ["Uhlmann", "IMA", "Marchesini", "MG2"],
}


def generate_serial_number(category: str, prefix: str = "SN") -> str:
    """Generate a realistic serial number."""
    year = datetime.now().year % 100
    random_part = "".join(random.choices(string.digits, k=6))
    return f"{prefix}-{year}-{category[:3].upper()}-{random_part}"


def get_manufacturer(category: str) -> str:
    """Get a random manufacturer for the category."""
    for cat, manufacturers in MANUFACTURERS.items():
        if cat in category:
            return random.choice(manufacturers)
    return "Generic Manufacturer"


async def ensure_all_machines_have_locations():
    """Ensure all machines have location data."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Ensuring all machines have locations...")

    # Get all locations
    locations = await db.plant_locations.find().to_list(None)
    location_map = {loc["id"]: loc for loc in locations}

    # Get all machines
    machines = await db.pharmaceutical_machines.find().to_list(None)
    updated_count = 0

    for machine in machines:
        workstation_id = machine.get("workstation_id")

        # Skip if already has location
        if machine.get("location_id"):
            continue

        # Find location based on workstation
        location_id = None
        if workstation_id and workstation_id in WORKSTATION_LOCATION_MAPPING:
            location_id = WORKSTATION_LOCATION_MAPPING[workstation_id]

        if location_id and location_id in location_map:
            location = location_map[location_id]

            location_data = {
                "location_id": location_id,
                "location_path": location["path"],
                "location_name": location["name"],
                "location_type": location["type"],
                "location_level": location["level"],
                "plant_id": "PLANT-OSD-001",
                "building_id": "BLDG-MFG-001",
                "floor_id": (location.get("parent_id") if location["level"] == 5 else None),
                "room_id": location["parent_id"] if location["level"] == 5 else None,
                "bay_id": location_id if location["type"] == "bay" else None,
                "cleanroom_class": location.get("cleanroom_class"),
                "workstation_id": workstation_id,
                "updated_at": datetime.now(timezone.utc),
            }

            result = await db.pharmaceutical_machines.update_one({"_id": machine["_id"]}, {"$set": location_data})

            if result.modified_count > 0:
                updated_count += 1
                print(f"Added location to {machine['name']}: {location['path']}")
        else:
            print(f"Warning: No location found for {machine['name']} (workstation: {workstation_id})")

    print(f"\nAdded locations to {updated_count} machines.")
    return updated_count


async def add_required_fields_to_machines():
    """Add required fields to all machines."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("\nAdding required fields to machines...")

    machines = await db.pharmaceutical_machines.find().to_list(None)
    updated_count = 0

    for machine in machines:
        machine_name = machine["name"]

        # Skip if already has all required fields
        if (
            machine.get("category")
            and machine.get("manufacturer")
            and machine.get("model_no")
            and machine.get("serial_no")
        ):
            continue

        # Determine category based on machine name
        category = "Unknown"
        for key in EQUIPMENT_CATEGORIES:
            if key in machine_name:
                category = EQUIPMENT_CATEGORIES[key]
                break

        manufacturer = get_manufacturer(category)
        model_no = f"{manufacturer.replace(' ', '')}-{machine_name.replace(' ', '')[:10]}-{random.randint(100, 999)}"
        serial_no = generate_serial_number(category, "EQ")

        # Get location info if available
        location_path = machine.get("location_path", "Unknown")

        machine_data = {
            "category": category,
            "manufacturer": manufacturer,
            "model_no": model_no,
            "serial_no": serial_no,
            "plant": "PLANT-OSD-001",
            "building": "BLDG-MFG-001",
            "zone_floor": (location_path.split(" > ")[2] if " > " in location_path else "Unknown"),
            "area_room": (location_path.split(" > ")[3] if " > " in location_path else "Unknown"),
            "bay": (location_path.split(" > ")[4] if " > " in location_path else "Unknown"),
            "workstation": machine.get("workstation_id", "Unknown"),
            "notes": f"Installed in {location_path}",
            "updated_at": datetime.now(timezone.utc),
        }

        result = await db.pharmaceutical_machines.update_one({"_id": machine["_id"]}, {"$set": machine_data})

        if result.modified_count > 0:
            updated_count += 1
            print(f"Added required fields to {machine_name}")

    print(f"\nAdded required fields to {updated_count} machines.")
    return updated_count


async def add_required_fields_to_equipment():
    """Add required fields to all equipment."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("\nAdding required fields to equipment...")

    equipment = await db.pharmaceutical_equipment.find().to_list(None)
    updated_count = 0

    for equip in equipment:
        # Skip if already has all required fields
        if equip.get("category") and equip.get("manufacturer") and equip.get("model_no") and equip.get("serial_no"):
            continue

        # Get parent machine info
        machine_id = equip.get("machine_id")
        machine = None
        if machine_id:
            machine = await db.pharmaceutical_machines.find_one({"machine_id": machine_id})

        # Determine category based on equipment name or machine
        category = "Unknown"
        if machine:
            category = machine.get("category", "Unknown")
        else:
            for cat in EQUIPMENT_CATEGORIES.values():
                if cat in equip["name"]:
                    category = cat
                    break

        manufacturer = get_manufacturer(category)
        model_no = f"{manufacturer.replace(' ', '')}-{equip['name'].replace(' ', '')[:8]}-{random.randint(10, 99)}"
        serial_no = generate_serial_number(category, "COMP")

        # Get location info from machine if available
        location_path = "Unknown"
        workstation = "Unknown"
        if machine:
            location_path = machine.get("location_path", "Unknown")
            workstation = machine.get("workstation_id", "Unknown")

        equipment_data = {
            "category": category,
            "manufacturer": manufacturer,
            "model_no": model_no,
            "serial_no": serial_no,
            "plant": "PLANT-OSD-001",
            "building": "BLDG-MFG-001",
            "zone_floor": (location_path.split(" > ")[2] if " > " in location_path else "Unknown"),
            "area_room": (location_path.split(" > ")[3] if " > " in location_path else "Unknown"),
            "bay": (location_path.split(" > ")[4] if " > " in location_path else "Unknown"),
            "workstation": workstation,
            "notes": f"Component of {machine['name'] if machine else 'standalone equipment'} in {location_path}",
            "updated_at": datetime.now(timezone.utc),
        }

        result = await db.pharmaceutical_equipment.update_one({"_id": equip["_id"]}, {"$set": equipment_data})

        if result.modified_count > 0:
            updated_count += 1
            print(f"Added required fields to equipment {equip['name']}")

    print(f"\nAdded required fields to {updated_count} equipment items.")
    return updated_count


async def verify_all_items_have_locations():
    """Verify that all machines and equipment have locations."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("\nVerifying all items have locations...")

    # Check machines
    machines_without_location = await db.pharmaceutical_machines.count_documents({"location_id": {"$exists": False}})

    # Check equipment
    equipment_without_location = await db.pharmaceutical_equipment.count_documents({"location_id": {"$exists": False}})

    # Check required fields
    machines_without_fields = await db.pharmaceutical_machines.count_documents(
        {
            "$or": [
                {"category": {"$exists": False}},
                {"manufacturer": {"$exists": False}},
                {"model_no": {"$exists": False}},
                {"serial_no": {"$exists": False}},
            ]
        }
    )

    equipment_without_fields = await db.pharmaceutical_equipment.count_documents(
        {
            "$or": [
                {"category": {"$exists": False}},
                {"manufacturer": {"$exists": False}},
                {"model_no": {"$exists": False}},
                {"serial_no": {"$exists": False}},
            ]
        }
    )

    print(f"Machines without location: {machines_without_location}")
    print(f"Equipment without location: {equipment_without_location}")
    print(f"Machines without required fields: {machines_without_fields}")
    print(f"Equipment without required fields: {equipment_without_fields}")

    if (
        machines_without_location == 0
        and equipment_without_location == 0
        and machines_without_fields == 0
        and equipment_without_fields == 0
    ):
        print("\n✅ All machines and equipment have locations and required fields!")
        return True
    else:
        print("\n❌ Some items are still missing locations or fields!")
        return False


async def main():
    """Main function to update all machines and equipment."""
    print("Starting complete machine and equipment update...")
    print("=" * 70)

    # Step 1: Ensure all machines have locations
    locations_added = await ensure_all_machines_have_locations()

    # Step 2: Add required fields to machines
    machine_fields_added = await add_required_fields_to_machines()

    # Step 3: Add required fields to equipment
    equipment_fields_added = await add_required_fields_to_equipment()

    # Step 4: Verify all items have locations and fields
    all_complete = await verify_all_items_have_locations()

    print("\n" + "=" * 70)
    print("SUMMARY:")
    print(f"  Locations added to machines: {locations_added}")
    print(f"  Required fields added to machines: {machine_fields_added}")
    print(f"  Required fields added to equipment: {equipment_fields_added}")
    print(f"  All items complete: {'✅ YES' if all_complete else '❌ NO'}")

    if all_complete:
        print("\n🎉 All machines and equipment are now fully updated!")
    else:
        print("\n⚠️  Some items still need attention.")


if __name__ == "__main__":
    asyncio.run(main())
