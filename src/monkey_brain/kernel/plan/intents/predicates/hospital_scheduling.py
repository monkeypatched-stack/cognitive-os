"""Hospital scheduling predicates."""

import re


async def hospital_scheduling_question_answer(client, question, force=False):
    """Handle hospital scheduling questions."""
    try:
        db = client["demo"]
        collection = db["appointments"]

        if re.search(r"schedule|book|appointment", question, re.IGNORECASE):
            patient_match = re.search(r"patient\s+(\w+)", question, re.IGNORECASE)
            patient_id = patient_match.group(1) if patient_match else "patient_001"
            doc = {"patient_id": patient_id, "status": "scheduled"}
            await collection.insert_one(doc)
            return (f"Scheduled appointment for patient {patient_id}", [], [], False)

        if re.search(r"list|show|get|today", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} appointments:"]
                for doc in docs:
                    lines.append(f"  - Patient {doc.get('patient_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No appointments found.", [], [], False)

        if re.search(r"cancel|reschedule", question, re.IGNORECASE):
            patient_match = re.search(r"patient\s+(\w+)", question, re.IGNORECASE)
            if patient_match:
                patient_id = patient_match.group(1)
                await collection.update_one({"patient_id": patient_id}, {"$set": {"status": "cancelled"}})
                return (
                    f"Cancelled appointment for patient {patient_id}",
                    [],
                    [],
                    False,
                )

        return (
            "I can help you schedule, list, or cancel appointments. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with scheduling: {e}", [], [], False)


def is_hospital_scheduling_question(question):
    """Check if question is about hospital scheduling."""
    q = question.lower()
    return any(w in q for w in ["appointment", "patient", "doctor", "hospital", "clinic"])
