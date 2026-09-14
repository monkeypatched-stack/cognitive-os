"""Network monitoring predicates."""

import re


async def network_monitoring_question_answer(client, question, force=False):
    """Handle network monitoring questions."""
    try:
        db = client["demo"]
        collection = db["network_nodes"]

        if re.search(r"monitor|status|check|health", question, re.IGNORECASE):
            node_match = re.search(r"(?:node|tower|switch)\s+(\w+)", question, re.IGNORECASE)
            if node_match:
                node_id = node_match.group(1)
                doc = await collection.find_one({"node_id": node_id})
                if doc:
                    return (
                        f"Node {node_id}: {doc.get('status', 'unknown')}",
                        [],
                        [],
                        False,
                    )
                return (f"Node {node_id} not found.", [], [], False)

            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} network nodes:"]
                for doc in docs:
                    lines.append(f"  - {doc.get('node_id', '?')}: {doc.get('status', '?')}")
                return ("\n".join(lines), [], [], False)
            return ("No network nodes found.", [], [], False)

        if re.search(r"fault|down|issue|problem", question, re.IGNORECASE):
            cursor = collection.find({"status": "fault"}).limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Active faults ({len(docs)} nodes):"]
                for doc in docs:
                    lines.append(f"  - {doc.get('node_id', '?')}: {doc.get('status', '?')}")
                return ("\n".join(lines), [], [], False)
            return ("No active faults.", [], [], False)

        return (
            "I can help you monitor network status or check faults. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with network monitoring: {e}", [], [], False)


def is_network_monitoring_question(question):
    """Check if question is about network monitoring."""
    q = question.lower()
    return any(w in q for w in ["network", "telecom", "tower", "signal", "fault"])
