"""batch_close predicates."""


async def batch_close_question_answer(client, question, force=False):
    """Generate batch close answer."""
    return None


def is_batch_close_question(question):
    """Check if question is about batch close."""
    return "batch close" in question.lower()
