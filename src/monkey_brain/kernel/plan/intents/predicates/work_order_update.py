"""work_order_update predicates."""


async def work_order_update_question_answer(client, question, force=False):
    """Generate work order update answer."""
    return None


def is_work_order_update_question(question):
    """Check if question is about work order update."""
    return "work order update" in question.lower()
