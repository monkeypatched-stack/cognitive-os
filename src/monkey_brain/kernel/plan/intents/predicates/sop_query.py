"""sop_query predicates — real MongoDB queries."""

import re


async def sop_query_question_answer(client, question, force=False):
    """Query SOPs from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["sops"]

        # Extract specific SOP ID if mentioned
        sop_match = re.search(r"(SOP-[\w-]+)", question, re.IGNORECASE)
        if sop_match:
            sop_id = sop_match.group(1)
            doc = await collection.find_one({"id": sop_id})
            if doc:
                answer = (
                    f"SOP {sop_id}:\n"
                    f"  Title: {doc.get('title', 'N/A')}\n"
                    f"  Version: {doc.get('version', 'N/A')}\n"
                    f"  Approved by: {doc.get('approved_by', 'N/A')}\n"
                    f"  Entity: {doc.get('entity_name', 'N/A')}\n"
                    f"  Effective: {doc.get('effective_date', 'N/A')}"
                )
                return (answer, [], [], False)
            else:
                return (f"SOP {sop_id} not found.", [], [], False)

        # Title-based search
        title_match = re.search(r"for\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            cursor = collection.find({"title": {"$regex": title, "$options": "i"}}).limit(5)
            docs = await cursor.to_list(length=5)
            if docs:
                lines = [f"SOPs matching '{title}':"]
                for doc in docs:
                    lines.append(f"  - {doc.get('id', '?')}: {doc.get('title', 'N/A')} (v{doc.get('version', '?')})")
                return ("\n".join(lines), [], [], False)

        # Department/entity filter
        dept_match = re.search(r"(?:in|for|from)\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if dept_match:
            dept = dept_match.group(1).strip()
            cursor = collection.find({"entity_name": {"$regex": dept, "$options": "i"}}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"SOPs for '{dept}':"]
                for doc in docs:
                    lines.append(f"  - {doc.get('id', '?')}: {doc.get('title', 'N/A')}")
                return ("\n".join(lines), [], [], False)

        # Default: list recent
        cursor = collection.find().sort("effective_date", -1).limit(10)
        docs = await cursor.to_list(length=10)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} SOPs. Recent:"]
            for doc in docs:
                lines.append(f"  - {doc.get('id', '?')}: {doc.get('title', 'N/A')} (v{doc.get('version', '?')})")
            answer = "\n".join(lines)
        else:
            answer = "No SOPs found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying SOPs: {e}", [], [], False)


def is_sop_query_question(question):
    q = question.lower()
    return any(kw in q for kw in ("sop", "standard operating", "procedure"))
