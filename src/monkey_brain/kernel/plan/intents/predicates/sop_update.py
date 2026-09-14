"""sop_update predicates."""


async def sop_update_question_answer(client, question, force=False):
    """Generate sop update answer."""
    return None


def is_sop_update_question(question):
    """Check if question is about sop update."""
    return "sop update" in question.lower()
