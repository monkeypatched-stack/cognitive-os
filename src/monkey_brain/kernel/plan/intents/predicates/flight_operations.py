"""Flight operations predicates."""

import re


async def flight_operations_question_answer(client, question, force=False):
    """Handle flight operations questions."""
    try:
        db = client["demo"]
        collection = db["flights"]

        if re.search(r"schedule|book|create|flight", question, re.IGNORECASE):
            origin_match = re.search(r"from\s+(\w+)", question, re.IGNORECASE)
            dest_match = re.search(r"to\s+(\w+)", question, re.IGNORECASE)
            origin = origin_match.group(1) if origin_match else "JFK"
            dest = dest_match.group(1) if dest_match else "LAX"
            doc = {"origin": origin, "destination": dest, "status": "scheduled"}
            await collection.insert_one(doc)
            return (f"Flight scheduled from {origin} to {dest}", [], [], False)

        if re.search(r"list|show|get|flights", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} flights:"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('origin', '?')} → {doc.get('destination', '?')} [{doc.get('status', '?')}]"
                    )
                return ("\n".join(lines), [], [], False)
            return ("No flights found.", [], [], False)

        if re.search(r"delay|cancel|status", question, re.IGNORECASE):
            flight_match = re.search(r"flight\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if flight_match:
                flight_id = flight_match.group(1)
                doc = await collection.find_one({"_id": flight_id})
                if doc:
                    return (
                        f"Flight {flight_id}: {doc.get('status', 'unknown')}",
                        [],
                        [],
                        False,
                    )
                return (f"Flight {flight_id} not found.", [], [], False)

        return (
            "I can help you schedule, list, or check flight status. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with flights: {e}", [], [], False)


def is_flight_operations_question(question):
    """Check if question is about flight operations."""
    q = question.lower()
    return any(w in q for w in ["flight", "airline", "airport", "plane", "aviation"])
