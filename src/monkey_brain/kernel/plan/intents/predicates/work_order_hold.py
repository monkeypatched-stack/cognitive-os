"""work_order_hold predicates."""


async def work_order_hold_question_answer(client, question, force=False):
    """Generate work order hold answer."""
    return None


def is_work_order_hold_question(question):
    """Check if question is about work order hold."""
    return "work order hold" in question.lower()
