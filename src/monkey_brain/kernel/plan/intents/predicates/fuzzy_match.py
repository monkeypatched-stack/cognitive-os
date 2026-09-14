"""fuzzy_match predicates."""


async def fuzzy_match_question_answer(client, question, force=False):
    """Generate fuzzy match answer."""
    return None


def is_fuzzy_match_question(question):
    """Check if question is about fuzzy match."""
    return "fuzzy match" in question.lower()


def fuzzy_match_pattern(text: str, pattern: str) -> bool:
    """Check if text fuzzy matches pattern."""
    return pattern.lower() in text.lower()


def extract_spelling_corrections(text: str) -> list[str]:
    """Extract potential spelling corrections."""
    return []
