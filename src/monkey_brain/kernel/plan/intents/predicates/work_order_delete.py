"""work_order_delete predicates."""


async def work_order_delete_question_answer(client, question, force=False):
    """Generate work order delete answer."""
    return None


def is_work_order_delete_question(question):
    """Check if question is about work order delete."""
    return "work order delete" in question.lower()
