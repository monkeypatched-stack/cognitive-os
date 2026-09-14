"""Inventory management predicates."""

import re


async def inventory_management_question_answer(client, question, force=False):
    """Handle inventory management questions."""
    try:
        db = client["demo"]
        collection = db["inventory"]

        if re.search(r"update|add|stock|replenish", question, re.IGNORECASE):
            product_match = re.search(r"(?:product|item|sku)\s+(\w+)", question, re.IGNORECASE)
            if product_match:
                product_id = product_match.group(1)
                await collection.update_one(
                    {"product_id": product_id},
                    {"$set": {"status": "updated"}},
                    upsert=True,
                )
                return (f"Updated inventory for {product_id}", [], [], False)

        if re.search(r"list|show|get|check", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} inventory items:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('product_id', '?')}: {doc.get('quantity', '?')} units")
                return ("\n".join(lines), [], [], False)
            return ("No inventory items found.", [], [], False)

        if re.search(r"low|out of stock|alert", question, re.IGNORECASE):
            cursor = collection.find({"quantity": {"$lt": 10}}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Low stock alerts ({len(docs)} items):"]
                for doc in docs:
                    lines.append(f"  - {doc.get('product_id', '?')}: {doc.get('quantity', '?')} units")
                return ("\n".join(lines), [], [], False)
            return ("No low stock alerts.", [], [], False)

        return (
            "I can help you update, list, or check inventory. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with inventory: {e}", [], [], False)


def is_inventory_management_question(question):
    """Check if question is about inventory."""
    q = question.lower()
    return any(w in q for w in ["inventory", "stock", "warehouse", "sku"])
