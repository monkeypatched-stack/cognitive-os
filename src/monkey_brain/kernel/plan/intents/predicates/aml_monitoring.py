"""AML monitoring predicates."""

import re


async def aml_monitoring_question_answer(client, question, force=False):
    """Handle AML monitoring questions."""
    try:
        db = client["demo"]
        collection = db["aml_alerts"]

        if re.search(r"monitor|scan|check|flag", question, re.IGNORECASE):
            doc = {"status": "monitoring", "alerts": 0}
            await collection.insert_one(doc)
            return ("AML monitoring started", [], [], False)

        if re.search(r"list|show|get|alerts", question, re.IGNORECASE):
            cursor = collection.find().limit(10)
            docs = await cursor.to_list(length=10)
            if docs:
                lines = [f"Found {len(docs)} AML alerts:"]
                for doc in docs:
                    lines.append(f"  - Alert {doc.get('_id', '?')} [{doc.get('status', '?')}]")
                return ("\n".join(lines), [], [], False)
            return ("No AML alerts found.", [], [], False)

        if re.search(r"report|sar|suspicious", question, re.IGNORECASE):
            transaction_match = re.search(r"transaction\s*(?:#?)(\w+)", question, re.IGNORECASE)
            if transaction_match:
                tx_id = transaction_match.group(1)
                doc = {"transaction_id": tx_id, "status": "reported", "type": "SAR"}
                await collection.insert_one(doc)
                return (f"SAR filed for transaction {tx_id}", [], [], False)

        return (
            "I can help you monitor, list alerts, or file SARs. What would you like to do?",
            [],
            [],
            False,
        )

    except Exception as e:
        return (f"Error with AML: {e}", [], [], False)


def is_aml_monitoring_question(question):
    """Check if question is about AML monitoring."""
    q = question.lower()
    return any(w in q for w in ["aml", "anti-money", "money laundering", "sar"])
