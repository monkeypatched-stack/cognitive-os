"""Permit processing predicates."""

import re


async def permit_processing_question_answer(client, question, force=False):
    """Handle permit processing questions."""
    try:
        db = client["demo"]
        collection = db["permits"]

        if re.search(r"apply|submit|request|permit", question, re.IGNORECASE):
            type_match = re.search(r"(?:permit|license)\s+(?:for|type)\s+(\w+)", question, re.IGNORECASE)
            permit_type = type_match.group(1) if type_match else "general"
            doc = {"type": permit_type, "status": "submitted"}
            await collection.insert_one(doc)
            return (f"Permit application submitted: {permit_type}", [], [], False)

        if re.search(r"list|show|get|permits", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} permits:"]
                for doc in docs:
                    lines.append(f"  - Permit {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No permits found.", [], [], False)

        if re.search(r"approve|deny|issue", question, re.IGNORECASE):
            permit_match = re.search(r"permit\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if permit_match:
                permit_id = permit_match.group(1)
                status = "approved" if "approve" in question.lower() else "denied"
                await collection.update_one({"_id": permit_id}, {"$set": {"status": status}})
                return (f"Permit {permit_id} {status}", [], [], False)

        return (
            "I can help you apply for, check status, or process permits. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with permits: {e}", [], [], False)


def is_permit_processing_question(question):
    """Check if question is about permit processing."""
    q = question.lower()
    return any(w in q for w in ["permit", "license", "approval", "zoning"])
