"""work_order_status predicates."""


async def work_order_status_question_answer(client, question, force=False):
    """Generate work order status answer."""
    return None


def is_work_order_status_question(question):
    """Check if question is about work order status."""
    return "work order status" in question.lower()
