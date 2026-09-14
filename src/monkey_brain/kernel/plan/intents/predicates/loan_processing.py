"""Loan processing predicates."""

import re


async def loan_processing_question_answer(client, question, force=False):
    """Handle loan processing questions."""
    try:
        db = client["demo"]
        collection = db["loans"]

        if re.search(r"apply|submit|application", question, re.IGNORECASE):
            doc = {"status": "pending", "amount": 0}
            await collection.insert_one(doc)
            return ("Loan application submitted", [], [], False)

        if re.search(r"list|show|get|status", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} loan applications:"]
                for doc in docs:
                    lines.append(f"  - Loan {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No loan applications found.", [], [], False)

        if re.search(r"approve|deny|reject", question, re.IGNORECASE):
            loan_match = re.search(r"loan\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if loan_match:
                loan_id = loan_match.group(1)
                action = "approved" if "approve" in question.lower() else "denied"
                await collection.update_one({"_id": loan_id}, {"$set": {"status": action}})
                return (f"Loan {loan_id} {action}", [], [], False)

        return (
            "I can help you apply for, check status, or process loans. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with loans: {e}", [], [], False)


def is_loan_processing_question(question):
    """Check if question is about loan processing."""
    q = question.lower()
    return any(w in q for w in ["loan", "mortgage", "credit", "borrow"])
