"""Procurement management predicates."""

import re


async def procurement_management_question_answer(client, question, force=False):
    """Handle procurement management questions."""
    try:
        db = client["demo"]
        collection = db["purchase_orders"]

        if re.search(r"create|place|new|order", question, re.IGNORECASE):
            vendor_match = re.search(r"vendor\s+(\w+)", question, re.IGNORECASE)
            vendor_id = vendor_match.group(1) if vendor_match else "vendor_001"
            doc = {"vendor_id": vendor_id, "status": "pending", "items": []}
            await collection.insert_one(doc)
            return (f"Purchase order created for vendor {vendor_id}", [], [], False)

        if re.search(r"list|show|get|orders", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} purchase orders:"]
                for doc in docs:
                    lines.append(f"  - PO {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No purchase orders found.", [], [], False)

        if re.search(r"approve|reject", question, re.IGNORECASE):
            po_match = re.search(r"po\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if po_match:
                po_id = po_match.group(1)
                status = "approved" if "approve" in question.lower() else "rejected"
                await collection.update_one({"_id": po_id}, {"$set": {"status": status}})
                return (f"Purchase order {po_id} {status}", [], [], False)

        return (
            "I can help you create, list, or process purchase orders. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with procurement: {e}", [], [], False)


def is_procurement_management_question(question):
    """Check if question is about procurement."""
    q = question.lower()
    return any(w in q for w in ["procurement", "purchase order", "vendor", "supplier"])
