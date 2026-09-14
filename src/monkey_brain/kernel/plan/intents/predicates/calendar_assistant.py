"""Calendar assistant predicates."""

import re


async def calendar_assistant_question_answer(client, question, force=False):
    """Handle calendar assistant questions."""
    try:
        db = client["demo"]
        collection = db["events"]

        if re.search(r"schedule|create|add|book", question, re.IGNORECASE):
            title_match = re.search(
                r"(?:schedule|create|add|book)\s+(.+?)(?:\s+(?:on|at|for|from)|$)",
                question,
                re.IGNORECASE,
            )
            title = title_match.group(1).strip() if title_match else "New Event"
            doc = {"title": title, "status": "scheduled"}
            await collection.insert_one(doc)
            return (f"Scheduled event: {title}", [], [], False)

        if re.search(r"list|show|what|my events", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} events:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('title', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No events found.", [], [], False)

        if re.search(r"cancel|delete|remove", question, re.IGNORECASE):
            title_match = re.search(
                r"(?:cancel|delete|remove)\s+(.+?)(?:\s+event|$)",
                question,
                re.IGNORECASE,
            )
            if title_match:
                title = title_match.group(1).strip()
                await collection.delete_one({"title": {"$regex": title, "$options": "i"}})
                return (f"Cancelled event: {title}", [], [], False)

        return (
            "I can help you schedule, list, or cancel events. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with calendar: {e}", [], [], False)


def is_calendar_assistant_question(question):
    """Check if question is about calendar."""
    q = question.lower()
    return any(w in q for w in ["calendar", "event", "meeting", "schedule"])
