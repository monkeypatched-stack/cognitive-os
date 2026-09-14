"""KYC processing predicates."""

import re


async def kyc_processing_question_answer(client, question, force=False):
    """Handle KYC processing questions."""
    try:
        db = client["demo"]
        collection = db["kyc_records"]

        if re.search(r"submit|verify|perform|check", question, re.IGNORECASE):
            customer_match = re.search(r"customer\s+(\w+)", question, re.IGNORECASE)
            customer_id = customer_match.group(1) if customer_match else "customer_001"
            doc = {"customer_id": customer_id, "status": "pending", "documents": []}
            await collection.insert_one(doc)
            return (
                f"KYC verification started for customer {customer_id}",
                [],
                [],
                False,
            )

        if re.search(r"list|show|get|status", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} KYC records:"]
                for doc in docs:
                    lines.append(f"  - Customer {doc.get('customer_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No KYC records found.", [], [], False)

        if re.search(r"approve|reject|complete", question, re.IGNORECASE):
            customer_match = re.search(r"customer\s+(\w+)", question, re.IGNORECASE)
            if customer_match:
                customer_id = customer_match.group(1)
                status = "approved" if "approve" in question.lower() else "rejected"
                await collection.update_one({"customer_id": customer_id}, {"$set": {"status": status}})
                return (f"KYC for customer {customer_id} {status}", [], [], False)

        return (
            "I can help you submit, check status, or process KYC. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with KYC: {e}", [], [], False)


def is_kyc_processing_question(question):
    """Check if question is about KYC."""
    q = question.lower()
    return any(w in q for w in ["kyc", "know your customer", "identity verification"])
