"""Grid monitoring predicates."""

import re


async def grid_monitoring_question_answer(client, question, force=False):
    """Handle grid monitoring questions."""
    try:
        db = client["demo"]
        collection = db["grid_assets"]

        if re.search(r"monitor|status|check|health", question, re.IGNORECASE):
            asset_match = re.search(r"(?:substation|transformer|line)\s+(\w+)", question, re.IGNORECASE)
            if asset_match:
                asset_id = asset_match.group(1)
                doc = await collection.find_one({"asset_id": asset_id})
                if doc:
                    return (
                        f"Asset {asset_id}: {doc.get('status', 'unknown')}",
                        [],
                        [],
                        False,
                    )
                return (f"Asset {asset_id} not found.", [], [], False)

            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} grid assets:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('asset_id', '?')}: {doc.get('status', '?')}")
                return ("\n".join(lines), [], [], False)
            return ("No grid assets found.", [], [], False)

        if re.search(r"outage|down|fault", question, re.IGNORECASE):
            cursor = collection.find({"status": "offline"}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Active outages ({len(docs)} assets):"]
                for doc in docs:
                    lines.append(f"  - {doc.get('asset_id', '?')}: {doc.get('status', '?')}")
                return ("\n".join(lines), [], [], False)
            return ("No active outages.", [], [], False)

        return (
            "I can help you monitor grid status or check outages. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with grid monitoring: {e}", [], [], False)


def is_grid_monitoring_question(question):
    """Check if question is about grid monitoring."""
    q = question.lower()
    return any(w in q for w in ["grid", "power", "electricity", "substation", "outage"])
