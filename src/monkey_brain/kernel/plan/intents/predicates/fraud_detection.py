"""Fraud detection predicates."""

import re


async def fraud_detection_question_answer(client, question, force=False):
    """Handle fraud detection questions."""
    try:
        db = client["demo"]
        collection = db["fraud_alerts"]

        if re.search(r"detect|check|scan|flag", question, re.IGNORECASE):
            transaction_match = re.search(r"transaction\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if transaction_match:
                tx_id = transaction_match.group(1)
                doc = {"transaction_id": tx_id, "status": "flagged", "risk_score": 0.85}
                await collection.insert_one(doc)
                return (
                    f"Transaction {tx_id} flagged for review (risk score: 0.85)",
                    [],
                    [],
                    False,
                )

        if re.search(r"list|show|get|alerts", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} fraud alerts:"]
                for doc in docs:
                    lines.append(f"  - TX {doc.get('transaction_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No fraud alerts found.", [], [], False)

        if re.search(r"clear|dismiss|resolve", question, re.IGNORECASE):
            alert_match = re.search(r"alert\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if alert_match:
                alert_id = alert_match.group(1)
                await collection.update_one({"_id": alert_id}, {"$set": {"status": "resolved"}})
                return (f"Alert {alert_id} resolved", [], [], False)

        return (
            "I can help you detect, list, or resolve fraud alerts. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with fraud detection: {e}", [], [], False)


def is_fraud_detection_question(question):
    """Check if question is about fraud detection."""
    q = question.lower()
    return any(w in q for w in ["fraud", "scam", "suspicious", "unauthorized"])
