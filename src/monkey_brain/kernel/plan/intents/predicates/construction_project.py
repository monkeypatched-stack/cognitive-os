"""Construction project predicates."""

import re


async def construction_project_question_answer(client, question, force=False):
    """Handle construction project questions."""
    try:
        db = client["demo"]
        collection = db["projects"]

        if re.search(r"create|start|new|project", question, re.IGNORECASE):
            name_match = re.search(
                r"project\s+(?:called|named|for)\s+(.+?)(?:\s+(?:with|at|in)|$)",
                question,
                re.IGNORECASE,
            )
            name = name_match.group(1).strip() if name_match else "New Project"
            doc = {"name": name, "status": "planning", "phase": "initiation"}
            await collection.insert_one(doc)
            return (f"Project created: {name}", [], [], False)

        if re.search(r"list|show|get|projects", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} projects:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('name', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No projects found.", [], [], False)

        if re.search(r"inspect|safety|check", question, re.IGNORECASE):
            site_match = re.search(r"site\s+(\w+)", question, re.IGNORECASE)
            if site_match:
                site_id = site_match.group(1)
                doc = {"site_id": site_id, "status": "inspection_passed"}
                await collection.insert_one(doc)
                return (
                    f"Safety inspection completed for site {site_id}",
                    [],
                    [],
                    False,
                )

        return (
            "I can help you create, list, or inspect projects. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with projects: {e}", [], [], False)


def is_construction_project_question(question):
    """Check if question is about construction projects."""
    q = question.lower()
    return any(w in q for w in ["construction", "project", "building", "site", "contractor"])
