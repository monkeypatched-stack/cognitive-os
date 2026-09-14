"""warehouse_shipping predicates — real MongoDB queries."""

import re


async def warehouse_shipping_question_answer(client, question, force=False):
    """Query warehouse/shipping data from MongoDB."""
    try:
        db = client["demo"]
        batches = db["production_batches"]

        # Batch-specific shipping
        batch_match = re.search(r"(BATCH-\d+|PB-\w+)", question, re.IGNORECASE)
        if batch_match:
            batch_id = batch_match.group(1)
            doc = await batches.find_one(
                {
                    "$or": [
                        {"batch_number": batch_id},
                        {"batch_id": batch_id},
                    ]
                }
            )
            if doc:
                answer = (
                    f"Shipping Status for {batch_id}:\n"
                    f"  Product: {doc.get('product_name', 'N/A')}\n"
                    f"  Status: {doc.get('status', 'N/A')}\n"
                    f"  Line: {doc.get('line_id', 'N/A')}"
                )
                return (answer, [], [], False)

        # Product-specific inventory
        product_match = re.search(r"for\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if product_match:
            product = product_match.group(1).strip()
            cursor = batches.find({"product_name": {"$regex": product, "$options": "i"}}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Inventory for '{product}':"]
                for doc in docs:
                    lines.append(f"  - {doc.get('batch_number', '?')}: {doc.get('status', 'N/A')}")
                return ("\n".join(lines), [], [], False)

        # Default: warehouse overview
        total = await batches.count_documents({})
        approved = await batches.count_documents({"status": "Approved"})
        in_progress = await batches.count_documents({"status": "In Progress"})
        created = await batches.count_documents({"status": "Created"})
        answer = (
            f"Warehouse Overview:\n"
            f"  Total batches: {total}\n"
            f"  Approved (ready to ship): {approved}\n"
            f"  In Progress: {in_progress}\n"
            f"  Created (pending): {created}"
        )
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying warehouse data: {e}", [], [], False)


def is_warehouse_shipping_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "warehouse",
            "shipment",
            "shipping",
            "dispatch",
            "outbound",
            "freight",
            "delivery",
            "logistics",
            "inventory",
            "stock",
        )
    )
