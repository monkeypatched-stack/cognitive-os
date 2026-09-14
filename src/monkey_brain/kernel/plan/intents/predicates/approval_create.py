"""Approval create predicates."""


async def approval_create_question_answer(client, question, force=False):
    """Generate approval create answer."""
    return None


def is_approval_create_question(question):
    """Check if question is about approval creation."""
    q = question.lower()
    if "approval" not in q and "approve" not in q:
        return False
    return any(
        kw in q
        for kw in (
            "create",
            "submit",
            "initiate",
            "raise",
            "new approval",
            "open approval",
        )
    )
