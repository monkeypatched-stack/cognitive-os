"""approval_decision predicates."""


async def approval_decision_question_answer(client, question, force=False):
    """Generate approval decision answer."""
    return None


def is_approval_decision_question(question):
    """Check if question is about approval decision."""
    return "approval decision" in question.lower()
