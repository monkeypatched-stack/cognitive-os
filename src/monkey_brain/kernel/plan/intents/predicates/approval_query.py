"""approval_query predicates — real MongoDB queries."""

import re


async def approval_query_question_answer(client, question, force=False):
    """Query approvals from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["approvals"]

        # Extract specific approval ID if mentioned
        apr_match = re.search(r"(APR-\d+)", question, re.IGNORECASE)
        if apr_match:
            apr_id = apr_match.group(1)
            doc = await collection.find_one({"approval_id": apr_id})
            if doc:
                answer = (
                    f"Approval {apr_id}:\n"
                    f"  Title: {doc.get('title', 'N/A')}\n"
                    f"  Status: {doc.get('status', 'N/A')}\n"
                    f"  Type: {doc.get('type', 'N/A')}\n"
                    f"  Requested by: {doc.get('requested_by_name', 'N/A')}\n"
                    f"  Department: {doc.get('department_name', 'N/A')}"
                )
                return (answer, [], [], False)
            else:
                return (f"Approval {apr_id} not found.", [], [], False)

        # Count questions
        if re.search(r"how many|count|total", question, re.IGNORECASE):
            total = await collection.count_documents({})
            pending = await collection.count_documents({"status": "Pending"})
            approved = await collection.count_documents({"status": "Approved"})
            rejected = await collection.count_documents({"status": "Rejected"})
            answer = f"Approvals: {total} total\n  Pending: {pending}\n  Approved: {approved}\n  Rejected: {rejected}"
            return (answer, [], [], False)

        # Status filter
        status_match = re.search(r"(pending|approved|rejected|open|closed)", question, re.IGNORECASE)
        if status_match:
            status = status_match.group(1).title()
            cursor = collection.find({"status": status}).limit(10)
            docs = await cursor.to_list(length=10)
            total = await collection.count_documents({"status": status})
            if docs:
                lines = [f"Found {total} {status.lower()} approvals:"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('approval_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                    )
            else:
                lines = [f"No {status.lower()} approvals found."]
            return ("\n".join(lines), [], [], False)

        # Department filter
        dept_match = re.search(r"(?:in|for|from)\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if dept_match:
            dept = dept_match.group(1).strip()
            cursor = collection.find({"department_name": {"$regex": dept, "$options": "i"}}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Approvals in '{dept}':"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('approval_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                    )
                return ("\n".join(lines), [], [], False)

        # Default: list pending
        cursor = collection.find().sort("created_at", -1).limit(10)
        docs = await cursor.to_list(length=10)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} approvals. Recent:"]
            for doc in docs:
                lines.append(f"  - {doc.get('approval_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]")
            answer = "\n".join(lines)
        else:
            answer = "No approvals found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying approvals: {e}", [], [], False)


def is_approval_query_question(question):
    """Check if question is about approval query."""
    q = question.lower()
    if "approval" not in q and "approve" not in q:
        return False
    if any(
        kw in q
        for kw in (
            "create",
            "submit",
            "initiate",
            "raise",
            "approve ",
            "reject",
            "deny",
            "on hold",
            "hold approval",
        )
    ):
        return False
    return any(
        kw in q
        for kw in (
            "show",
            "list",
            "get",
            "find",
            "pending",
            "status",
            "how many",
            "all approval",
            "approval status",
            "approval list",
            "approval request",
        )
    )
