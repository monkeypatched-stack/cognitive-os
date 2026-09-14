"""Ecommerce order predicates."""

import re


async def ecommerce_order_question_answer(client, question, force=False):
    """Handle ecommerce order questions."""
    try:
        db = client["demo"]
        collection = db["orders"]

        if re.search(r"place|create|new|submit", question, re.IGNORECASE):
            doc = {"status": "placed", "items": []}
            await collection.insert_one(doc)
            return ("Order placed successfully", [], [], False)

        if re.search(r"list|show|get|my orders", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} orders:"]
                for doc in docs:
                    lines.append(f"  - Order {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No orders found.", [], [], False)

        if re.search(r"status|track|where", question, re.IGNORECASE):
            order_match = re.search(r"order\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if order_match:
                order_id = order_match.group(1)
                doc = await collection.find_one({"_id": order_id})
                if doc:
                    return (
                        f"Order {order_id}: {doc.get('status', 'unknown')}",
                        [],
                        [],
                        False,
                    )
                return (f"Order {order_id} not found.", [], [], False)

        return (
            "I can help you place, list, or track orders. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with orders: {e}", [], [], False)


def is_ecommerce_order_question(question):
    """Check if question is about ecommerce orders."""
    q = question.lower()
    return any(w in q for w in ["order", "purchase", "buy", "cart", "checkout"])
