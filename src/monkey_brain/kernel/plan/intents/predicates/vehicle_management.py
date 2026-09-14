"""Vehicle management predicates."""

import re


async def vehicle_management_question_answer(client, question, force=False):
    """Handle vehicle management questions."""
    try:
        db = client["demo"]
        collection = db["vehicles"]

        if re.search(r"track|status|location|telemetry", question, re.IGNORECASE):
            vehicle_match = re.search(r"vehicle\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if vehicle_match:
                vehicle_id = vehicle_match.group(1)
                doc = await collection.find_one({"vehicle_id": vehicle_id})
                if doc:
                    return (
                        f"Vehicle {vehicle_id}: {doc.get('status', 'unknown')}",
                        [],
                        [],
                        False,
                    )
                return (f"Vehicle {vehicle_id} not found.", [], [], False)

            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} vehicles:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('vehicle_id', '?')}: {doc.get('status', '?')}")
                return ("\n".join(lines), [], [], False)
            return ("No vehicles found.", [], [], False)

        if re.search(r"service|maintenance|recall", question, re.IGNORECASE):
            vehicle_match = re.search(r"vehicle\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if vehicle_match:
                vehicle_id = vehicle_match.group(1)
                doc = {"vehicle_id": vehicle_id, "status": "service_scheduled"}
                await collection.insert_one(doc)
                return (f"Service scheduled for vehicle {vehicle_id}", [], [], False)

        return (
            "I can help you track vehicles or schedule service. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with vehicles: {e}", [], [], False)


def is_vehicle_management_question(question):
    """Check if question is about vehicle management."""
    q = question.lower()
    return any(w in q for w in ["vehicle", "car", "truck", "fleet", "dealer"])
