"""facility_query predicates — plant/facility counts and details from MongoDB."""

import re


async def facility_query_question_answer(client, question, force=False):
    """Answer questions about plants, facilities, and sites."""
    try:
        db = client["demo"]
        q = question.lower()

        if re.search(r"\bplant[s]?\b", q):
            locs = db["plant_locations"]
            # Deduplicate by location_id to count unique plants
            unique_ids = await locs.distinct("location_id", {"type": "plant", "level": 1})
            count = len(unique_ids)
            if count == 0:
                # fallback: count industrial_plants
                count = await db["industrial_plants"].count_documents({})
            names = []
            seen = set()
            async for doc in locs.find({"type": "plant", "level": 1}):
                lid = doc.get("location_id", "")
                if lid not in seen:
                    seen.add(lid)
                    names.append(doc.get("name", lid))
            name_list = ", ".join(names) if names else "none"
            answer = f"There {'is' if count == 1 else 'are'} {count} plant{'s' if count != 1 else ''}: {name_list}."
            return (answer, [], [], True)

        if re.search(r"facilit|site|location", q):
            locs = db["plant_locations"]
            total = await locs.count_documents({})
            by_type = {}
            async for doc in locs.find({}, {"type": 1}):
                t = doc.get("type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            breakdown = ", ".join(f"{v} {k}(s)" for k, v in sorted(by_type.items()))
            answer = f"Facility locations: {total} total — {breakdown}."
            return (answer, [], [], True)

        # Default: plant summary
        unique_ids = await db["plant_locations"].distinct("location_id", {"type": "plant", "level": 1})
        count = len(unique_ids)
        answer = f"There {'is' if count == 1 else 'are'} {count} plant{'s' if count != 1 else ''} in the system."
        return (answer, [], [], True)

    except Exception as e:
        return (f"Error querying facilities: {e}", [], [], False)


def is_facility_query(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "how many plant",
            "plant count",
            "number of plant",
            "how many facilit",
            "facilit",
            "how many site",
            "how many location",
            "plant location",
        )
    )
