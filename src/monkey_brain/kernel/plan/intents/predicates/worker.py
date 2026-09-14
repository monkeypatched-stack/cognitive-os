"""worker predicates — real MongoDB queries."""

import re


async def worker_question_answer(client, question, force=False):
    """Query workers from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["workers"]
        work_orders = db["work_orders"]

        # Specific worker lookup
        for w in await collection.find({}).to_list(100):
            name = w.get("name", "")
            if name.lower() in question.lower():
                # Find their work orders
                wo_cursor = work_orders.find({"assigned_to_name": name}).limit(5)
                wos = await wo_cursor.to_list(length=5)
                lines = [
                    f"Worker: {name}",
                    f"  Title: {w.get('title', 'N/A')}",
                    f"  Role: {w.get('role', 'N/A')}",
                    f"  Status: {w.get('status', 'N/A')}",
                    f"  Reports to: {w.get('reports_to_name', 'N/A')}",
                ]
                if wos:
                    lines.append(f"  Assigned Work Orders ({len(wos)}):")
                    for wo in wos:
                        lines.append(
                            f"    - {wo.get('work_order_id', '?')}: {wo.get('title', 'N/A')} [{wo.get('status', '?')}]"
                        )
                else:
                    lines.append("  No work orders currently assigned.")
                return ("\n".join(lines), [], [], False)

        # Department filter
        dept_match = re.search(r"(?:in|for|from)\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if dept_match:
            # Workers don't have department field directly, list all
            cursor = collection.find().limit(20)
            docs = await cursor.to_list(length=20)
            if docs:
                lines = [f"Workers ({len(docs)} shown):"]
                for doc in docs:
                    lines.append(f"  - {doc.get('name', '?')}: {doc.get('title', 'N/A')} [{doc.get('role', '?')}]")
                return ("\n".join(lines), [], [], False)

        # Default: list all workers
        cursor = collection.find().limit(20)
        docs = await cursor.to_list(length=20)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} workers:"]
            for doc in docs:
                lines.append(f"  - {doc.get('name', '?')}: {doc.get('title', 'N/A')} [{doc.get('role', '?')}]")
            answer = "\n".join(lines)
        else:
            answer = "No workers found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying workers: {e}", [], [], False)


def is_worker_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "worker",
            "worker",
            "employee",
            "staff",
            "team member",
            "operator",
            "technician",
            "assigned to",
            "tasks for",
            "who is",
            "who works",
        )
    )
