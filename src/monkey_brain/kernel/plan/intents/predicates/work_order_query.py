"""work_order_query predicates."""

import re


async def work_order_query_question_answer(client, question, force=False):
    """Query work orders from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["work_orders"]

        # Extract specific work order ID if mentioned
        wo_match = re.search(r"(PM-\w+-\d+|WO-\d+)", question, re.IGNORECASE)
        if wo_match:
            wo_id = wo_match.group(1)
            doc = await collection.find_one({"work_order_id": wo_id})
            if doc:
                answer = (
                    f"Work order {wo_id}:\n"
                    f"  Title: {doc.get('title', 'N/A')}\n"
                    f"  Status: {doc.get('status', 'N/A')}\n"
                    f"  Priority: {doc.get('priority', 'N/A')}\n"
                    f"  Type: {doc.get('work_order_type', 'N/A')}\n"
                    f"  Machine: {doc.get('machine_id', 'N/A')}"
                )
                return (answer, [], [], False)

        # Count questions
        if re.search(r"how many|count|total", question, re.IGNORECASE):
            total = await collection.count_documents({})
            open_count = await collection.count_documents({"status": {"$nin": ["Completed", "Cancelled"]}})
            completed = await collection.count_documents({"status": "Completed"})
            answer = f"We have {total} work orders total: {open_count} open, {completed} completed."
            return (answer, [], [], False)

        # Status filter
        status_match = re.search(
            r"(completed|open|todo|in progress|scheduled|cancelled)",
            question,
            re.IGNORECASE,
        )
        if status_match:
            status = status_match.group(1).title()
            count = await collection.count_documents({"status": status})
            answer = f"There are {count} work orders with status '{status}'."
            return (answer, [], [], False)

        # Priority filter
        priority_match = re.search(r"(high|medium|low|critical)", question, re.IGNORECASE)
        if priority_match:
            priority = priority_match.group(1).title()
            count = await collection.count_documents({"priority": priority})
            answer = f"There are {count} work orders with priority '{priority}'."
            return (answer, [], [], False)

        # Default: list recent
        cursor = collection.find().sort("work_order_id", 1).limit(5)
        docs = await cursor.to_list(length=5)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} work orders. Recent:"]
            for doc in docs:
                lines.append(
                    f"  - {doc.get('work_order_id', '?')}: {doc.get('title', 'N/A')} [{doc.get('status', '?')}]"
                )
            answer = "\n".join(lines)
        else:
            answer = "No work orders found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying work orders: {e}", [], [], False)


def is_work_order_query_question(question):
    """Check if question is about work order query."""
    q = question.lower()
    return any(w in q for w in ["work order", "work_order", "workorder"])
