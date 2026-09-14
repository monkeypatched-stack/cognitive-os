"""approval_hold predicates."""


async def approval_hold_question_answer(client, question, force=False):
    """Generate approval hold answer."""
    return None


def is_approval_hold_question(question):
    """Check if question is about approval hold."""
    return "approval hold" in question.lower()
