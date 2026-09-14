"""drug_research predicates — real MongoDB queries."""

import re


async def drug_research_question_answer(client, question, force=False):
    """Query drug research data from MongoDB."""
    try:
        db = client["demo"]
        batches = db["production_batches"]

        # Product-specific research
        product_match = re.search(r"for\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
        if product_match:
            product = product_match.group(1).strip()
            cursor = batches.find({"product_name": {"$regex": product, "$options": "i"}}).limit(5)
            docs = await cursor.to_list(length=5)
            if docs:
                lines = [f"Research data for '{product}':"]
                for doc in docs:
                    lines.append(
                        f"  - Batch {doc.get('batch_number', '?')}: {doc.get('status', 'N/A')} on {doc.get('line_id', 'N/A')}"
                    )
                return ("\n".join(lines), [], [], False)

        # Default: research overview
        products = await batches.distinct("product_name")
        total = await batches.count_documents({})
        answer = (
            f"Drug Research Overview:\n"
            f"  Products tracked: {len(products)}\n"
            f"  Total batches: {total}\n"
            f"  Products: {', '.join(products[:10])}"
        )
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error querying drug research: {e}", [], [], False)


def is_drug_research_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "drug",
            "research",
            "formulation",
            "r&d",
            "development",
            "compound",
            "api",
            "excipient",
            "clinical",
        )
    )
