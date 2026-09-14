"""batch_update predicates."""


async def batch_update_question_answer(client, question, force=False):
    """Generate batch update answer."""
    return None


def is_batch_update_question(question):
    """Check if question is about batch update."""
    return "batch update" in question.lower()
