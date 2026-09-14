"""batch_delete predicates."""


async def batch_delete_question_answer(client, question, force=False):
    """Generate batch delete answer."""
    return None


def is_batch_delete_question(question):
    """Check if question is about batch delete."""
    return "batch delete" in question.lower()
