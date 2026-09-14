"""Email management predicates."""

import re


async def email_management_question_answer(client, question, force=False):
    """Handle email management questions."""
    try:
        db = client["demo"]
        collection = db["emails"]

        if re.search(r"send|compose|write", question, re.IGNORECASE):
            to_match = re.search(r"to\s+(\S+@\S+)", question, re.IGNORECASE)
            to_email = to_match.group(1) if to_match else "recipient@example.com"
            doc = {"to": to_email, "status": "sent"}
            await collection.insert_one(doc)
            return (f"Email sent to {to_email}", [], [], False)

        if re.search(r"list|show|get|inbox|unread", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} emails:"]
                for doc in docs:
                    lines.append(f"  - To: {doc.get('to', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No emails found.", [], [], False)

        if re.search(r"delete|remove|trash", question, re.IGNORECASE):
            subject_match = re.search(
                r"(?:delete|remove|trash)\s+(.+?)(?:\s+email|$)",
                question,
                re.IGNORECASE,
            )
            if subject_match:
                subject = subject_match.group(1).strip()
                await collection.delete_one({"subject": {"$regex": subject, "$options": "i"}})
                return (f"Deleted email: {subject}", [], [], False)

        return (
            "I can help you send, list, or delete emails. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with email: {e}", [], [], False)


def is_email_management_question(question):
    """Check if question is about email."""
    q = question.lower()
    return any(w in q for w in ["email", "mail", "inbox", "send mail"])
