"""sop_create predicates."""


async def sop_create_question_answer(client, question, force=False):
    """Generate sop create answer."""
    return None


def is_sop_create_question(question):
    """Check if question is about sop create."""
    return "sop create" in question.lower()
