"""batch_hold predicates."""


async def batch_hold_question_answer(client, question, force=False):
    """Generate batch hold answer."""
    return None


def is_batch_hold_question(question):
    """Check if question is about batch hold."""
    q = question.lower()
    return "batch" in q and any(kw in q for kw in ("hold", "on hold", "pause", "suspend", "freeze"))
