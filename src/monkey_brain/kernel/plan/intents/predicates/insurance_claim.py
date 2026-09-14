"""Insurance claim predicates."""

import re


async def insurance_claim_question_answer(client, question, force=False):
    """Handle insurance claim questions."""
    try:
        db = client["demo"]
        collection = db["claims"]

        if re.search(r"file|submit|create|claim", question, re.IGNORECASE):
            policy_match = re.search(r"policy\s*(?:#?)(\w+)", question, re.IGNORECASE)
            policy_id = policy_match.group(1) if policy_match else "policy_001"
            doc = {"policy_id": policy_id, "status": "filed", "amount": 0}
            await collection.insert_one(doc)
            return (f"Claim filed for policy {policy_id}", [], [], False)

        if re.search(r"list|show|get|status", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} claims:"]
                for doc in docs:
                    lines.append(f"  - Claim {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No claims found.", [], [], False)

        if re.search(r"approve|deny|settle", question, re.IGNORECASE):
            claim_match = re.search(r"claim\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if claim_match:
                claim_id = claim_match.group(1)
                status = "approved" if "approve" in question.lower() else "denied"
                await collection.update_one({"_id": claim_id}, {"$set": {"status": status}})
                return (f"Claim {claim_id} {status}", [], [], False)

        return (
            "I can help you file, check status, or process claims. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with claims: {e}", [], [], False)


def is_insurance_claim_question(question):
    """Check if question is about insurance claims."""
    q = question.lower()
    return any(w in q for w in ["claim", "insurance claim", "file claim", "settlement"])
