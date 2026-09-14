"""batch_creation predicates."""


async def batch_creation_question_answer(client, question, force=False):
    """Generate batch creation answer."""
    return None


def is_batch_creation_question(question):
    """Check if question is about batch creation."""
    return "batch creation" in question.lower()
