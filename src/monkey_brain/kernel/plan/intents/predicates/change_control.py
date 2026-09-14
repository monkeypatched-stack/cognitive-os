"""change_control predicates — real MongoDB queries."""

import re


async def change_control_question_answer(client, question, force=False):
    """Query change controls from MongoDB."""
    return await change_control_query_question_answer(client, question, force)


async def change_control_query_question_answer(client, question, force=False):
    """Query change controls from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["change_controls"]

        # Extract specific CC ID if mentioned
        cc_match = re.search(r"(CC-\d{4}-\d+)", question, re.IGNORECASE)
        if cc_match:
            cc_id = cc_match.group(1)
            doc = await collection.find_one({"change_control_id": cc_id})
            if doc:
                answer = (
                    f"Change Control {cc_id}:\n"
                    f"  Title: {doc.get('title', 'N/A')}\n"
                    f"  Status: {doc.get('status', 'N/A')}\n"
                    f"  Type: {doc.get('type', 'N/A')}\n"
                    f"  Requested by: {doc.get('requested_by_name', 'N/A')}\n"
                    f"  Line: {doc.get('line_name', 'N/A')}\n"
                    f"  Stage: {doc.get('stage_name', 'N/A')}"
                )
                return (answer, [], [], False)
            else:
                return (f"Change control {cc_id} not found.", [], [], False)

        # Count questions
        if re.search(r"how many|count|total", question, re.IGNORECASE):
            total = await collection.count_documents({})
            open_cc = await collection.count_documents({"status": "Open"})
            under_review = await collection.count_documents({"status": "Under Review"})
            approved = await collection.count_documents({"status": "Approved"})
            answer = (
                f"Change Controls: {total} total\n"
                f"  Open: {open_cc}\n"
                f"  Under Review: {under_review}\n"
                f"  Approved: {approved}"
            )
            return (answer, [], [], False)

        # Status filter
        status_match = re.search(
            r"(open|under review|approved|rejected|closed|pending)",
            question,
            re.IGNORECASE,
        )
        if status_match:
            status = status_match.group(1).title()
            cursor = collection.find({"status": status}).limit(10)
            docs = await cursor.to_list(length=10)
            total = await collection.count_documents({"status": status})
            if docs:
                lines = [f"Found {total} {status.lower()} change controls:"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('change_control_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                    )
            else:
                lines = [f"No {status.lower()} change controls found."]
            return ("\n".join(lines), [], [], False)

        # Department/stage filter
        dept_match = re.search(r"(?:in|for|from)\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if dept_match:
            dept = dept_match.group(1).strip()
            cursor = collection.find(
                {
                    "$or": [
                        {"stage_name": {"$regex": dept, "$options": "i"}},
                        {"line_name": {"$regex": dept, "$options": "i"}},
                    ]
                }
            ).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Change controls related to '{dept}':"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('change_control_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                    )
                return ("\n".join(lines), [], [], False)

        # Default: list recent
        cursor = collection.find().sort("created_at", -1).limit(10)
        docs = await cursor.to_list(length=10)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} change controls. Recent:"]
            for doc in docs:
                lines.append(
                    f"  - {doc.get('change_control_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                )
            answer = "\n".join(lines)
        else:
            answer = "No change controls found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying change controls: {e}", [], [], False)


def is_change_control_question(question):
    return "change control" in question.lower()


def is_change_control_create_question(question):
    return "change control" in question.lower() and ("create" in question.lower() or "new" in question.lower())


def is_change_control_query_question(question):
    return "change control" in question.lower() and (
        "query" in question.lower() or "show" in question.lower() or "list" in question.lower()
    )


def is_change_control_update_question(question):
    return "change control" in question.lower() and "update" in question.lower()


def is_change_control_decision_question(question):
    return "change control" in question.lower() and "decision" in question.lower()
