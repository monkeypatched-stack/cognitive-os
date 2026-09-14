"""batch_record predicates — real MongoDB queries."""

import re


async def batch_record_question_answer(client, question, force=False):
    """Query production batches from MongoDB and generate answer."""
    try:
        db = client["demo"]
        collection = db["production_batches"]

        # Extract specific batch number if mentioned
        batch_match = re.search(r"(BATCH-\d+|PB-\w+)", question, re.IGNORECASE)
        if batch_match:
            batch_id = batch_match.group(1)
            doc = await collection.find_one(
                {
                    "$or": [
                        {"batch_number": batch_id},
                        {"batch_id": batch_id},
                    ]
                }
            )
            if doc:
                answer = (
                    f"Batch {doc.get('batch_number', batch_id)}:\n"
                    f"  Product: {doc.get('product_name', 'N/A')}\n"
                    f"  Status: {doc.get('status', 'N/A')}\n"
                    f"  Batch ID: {doc.get('batch_id', 'N/A')}\n"
                    f"  Line: {doc.get('line_id', 'N/A')}\n"
                    f"  Plant: {doc.get('plant_id', 'N/A')}"
                )
                return (answer, [], [], False)
            else:
                return (f"Batch {batch_id} not found.", [], [], False)

        # Count questions
        if re.search(r"how many|count|total", question, re.IGNORECASE):
            total = await collection.count_documents({})
            statuses = await collection.distinct("status")
            counts = {}
            for s in statuses:
                counts[s] = await collection.count_documents({"status": s})
            lines = [f"Total batches: {total}"]
            for s, c in sorted(counts.items()):
                lines.append(f"  {s}: {c}")
            return ("\n".join(lines), [], [], False)

        # Status filter
        status_match = re.search(
            r"(created|in progress|approved|rejected|on hold|completed|released)",
            question,
            re.IGNORECASE,
        )
        if status_match:
            status = status_match.group(1).title()
            cursor = collection.find({"status": status}).limit(10)
            docs = await cursor.to_list(length=10)
            total = await collection.count_documents({"status": status})
            if docs:
                lines = [f"Found {total} batches with status '{status}':"]
                for doc in docs:
                    lines.append(
                        f"  - {doc.get('batch_number', '?')}: {doc.get('product_name', 'N/A')} [{doc.get('status', '?')}]"
                    )
            else:
                lines = [f"No batches found with status '{status}'."]
            return ("\n".join(lines), [], [], False)

        # Product filter
        product_match = re.search(r"for\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if product_match:
            product = product_match.group(1).strip()
            cursor = collection.find({"product_name": {"$regex": product, "$options": "i"}}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Batches for '{product}':"]
                for doc in docs:
                    lines.append(f"  - {doc.get('batch_number', '?')}: {doc.get('status', 'N/A')}")
                return ("\n".join(lines), [], [], False)

        # Yield / quality
        if re.search(r"yield|quality|deviation", question, re.IGNORECASE):
            total = await collection.count_documents({})
            approved = await collection.count_documents({"status": "Approved"})
            rejected = await collection.count_documents({"status": "Rejected"})
            on_hold = await collection.count_documents({"status": "On Hold"})
            answer = (
                f"Batch Quality Summary:\n"
                f"  Total: {total}\n"
                f"  Approved: {approved}\n"
                f"  Rejected: {rejected}\n"
                f"  On Hold: {on_hold}\n"
                f"  Approval Rate: {approved / total * 100:.1f}%"
                if total > 0
                else "No batches."
            )
            return (answer, [], [], False)

        # Default: list recent
        cursor = collection.find().sort("created_at", -1).limit(10)
        docs = await cursor.to_list(length=10)
        total = await collection.count_documents({})
        if docs:
            lines = [f"Found {total} production batches. Recent:"]
            for doc in docs:
                lines.append(
                    f"  - {doc.get('batch_number', '?')}: {doc.get('product_name', 'N/A')} [{doc.get('status', '?')}]"
                )
            answer = "\n".join(lines)
        else:
            answer = "No production batches found."
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying batches: {e}", [], [], False)


def is_batch_record_question(question):
    """Check if question is about batch record."""
    q = question.lower()
    if "batch" not in q:
        return False
    if any(kw in q for kw in ("hold", "close", "delete", "remove", "cancel", "update", "create")):
        return False
    return True
