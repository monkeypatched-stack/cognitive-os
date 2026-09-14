"""Todo management predicates."""

import re


async def todo_management_question_answer(client, question, force=False):
    """Handle todo management questions."""
    try:
        db = client["demo"]
        collection = db["todos"]

        if re.search(r"create|add|new|make", question, re.IGNORECASE):
            title_match = re.search(
                r"todo\s+(?:called|named|for|to)\s+(.+?)(?:\s+(?:with|due|priority)|$)",
                question,
                re.IGNORECASE,
            )
            title = title_match.group(1).strip() if title_match else "New Todo"
            doc = {"title": title, "status": "pending", "priority": "medium"}
            await collection.insert_one(doc)
            return (f"Created todo: {title}", [], [], False)

        if re.search(r"list|show|get|what", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} todos:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('title', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No todos found.", [], [], False)

        if re.search(r"complete|done|finish", question, re.IGNORECASE):
            title_match = re.search(
                r"(?:complete|done|finish)\s+(.+?)(?:\s+todo|$)",
                question,
                re.IGNORECASE,
            )
            if title_match:
                title = title_match.group(1).strip()
                await collection.update_one(
                    {"title": {"$regex": title, "$options": "i"}},
                    {"$set": {"status": "completed"}},
                )
                return (f"Marked todo as completed: {title}", [], [], False)

        return (
            "I can help you create, list, or complete todos. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with todo management: {e}", [], [], False)


def is_todo_management_question(question):
    """Check if question is about todo management."""
    q = question.lower()
    return any(w in q for w in ["todo", "to-do", "task list", "to do list"])
