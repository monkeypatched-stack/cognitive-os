"""compliance_audit predicates — real MongoDB queries."""

import re


async def compliance_audit_question_answer(client, question, force=False):
    """Query audit log from MongoDB."""
    try:
        db = client["demo"]
        collection = db["part11_audit_trail"]

        # Count questions
        if re.search(r"how many|count|total", question, re.IGNORECASE):
            total = await collection.count_documents({})
            return (f"Audit trail entries: {total}", [], [], False)

        # Default: list recent
        cursor = collection.find().sort("timestamp", -1).limit(10)
        docs = await cursor.to_list(length=10)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Recent audit events ({total} total):"]
            for doc in docs:
                action = doc.get("action", "N/A")
                coll = doc.get("collection", "N/A")
                user = doc.get("user_id", "N/A")
                ts = doc.get("timestamp", "N/A")
                lines.append(f"  - {action} on {coll} by {user} at {ts}")
            answer = "\n".join(lines)
        else:
            answer = "No audit events found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying audit log: {e}", [], [], False)


async def compliance_audit_approval_question_answer(client, question, force=False):
    """Alias for audit log query."""
    return await compliance_audit_question_answer(client, question, force)


def is_compliance_audit_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "audit",
            "compliance",
            "part 11",
            "audit trail",
            "compliance event",
            "audit log",
            "change history",
        )
    )
