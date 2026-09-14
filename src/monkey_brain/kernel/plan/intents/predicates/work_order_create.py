"""work_order_create predicates — real MongoDB insert."""

import re
import uuid
from datetime import datetime, timedelta


async def work_order_create_question_answer(client, question, force=False):
    """Create a work order in MongoDB."""
    try:
        db = client["demo"]
        collection = db["work_orders"]

        # Generate new work order ID
        wo_id = f"WO-{uuid.uuid4().hex[:5].upper()}"

        # Extract machine from question if mentioned
        machine_match = re.search(
            r"for\s+(.+?)(?:\s+(?:maintenance|calibration|inspection|repair)|$)",
            question,
            re.IGNORECASE,
        )
        machine_name = machine_match.group(1).strip() if machine_match else "General"

        # Determine work order type
        wo_type = "Maintenance"
        if "calibration" in question.lower():
            wo_type = "Calibration"
        elif "inspection" in question.lower():
            wo_type = "Inspection"
        elif "repair" in question.lower():
            wo_type = "Repair"

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        expected = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        doc = {
            "work_order_id": wo_id,
            "title": f"{wo_type} - {machine_name}",
            "message": question,
            "work_order_type": wo_type,
            "status": "Todo",
            "priority": "Medium",
            "machine_id": "MACH-UNKNOWN",
            "line_id": "LINE-TAB-001",
            "workstation_id": "WS-TAB-01-01",
            "assigned_to": "WRK-WS-010",
            "created_at": now,
            "updated_at": now,
            "remarks": "",
            "attachments": [],
            "expected_completion": expected,
            "assigned_to_name": "System",
        }

        await collection.insert_one(doc)

        answer = (
            f"✅ Work order created successfully!\n"
            f"  ID: {wo_id}\n"
            f"  Title: {doc['title']}\n"
            f"  Type: {wo_type}\n"
            f"  Status: Todo\n"
            f"  Priority: Medium\n"
            f"  Expected completion: {expected}"
        )
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error creating work order: {e}", [], [], False)


def is_work_order_create_question(question):
    q = question.lower()
    return any(kw in q for kw in ("create", "new", "open", "initiate", "raise", "submit")) and any(
        kw in q for kw in ("work order", "workorder", "work_order")
    )
