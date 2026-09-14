import logging

from src.monkey_brain.kernel.plan.parser.nlp.spacy_parser import get_nlp

logger = logging.getLogger(__name__)

_INTERROGATIVES = {"what", "who", "which", "where", "when", "how", "why"}


def _normalize_phrase(text: str) -> str:
    """
    Normalize extracted phrases.

    Examples:
        "A compression workstation" -> "compression workstation"
        "the workstation SOP" -> "workstation SOP"
    """
    nlp = get_nlp()
    if nlp is None:
        return text

    doc = nlp(text)

    tokens = [token.text for token in doc if token.pos_ not in ("DET", "PRON")]

    return " ".join(tokens).strip()


def _deduplicate_and_rank(phrases: set[str]) -> list[str]:
    """
    Keep only the richest phrases.

    Example:

        Tablet Line A      ✓
        Line A             ✗

        Batch BATCH-2026-001 ✓
        Batch 001            ✗

        workstation SOP      ✓
        SOP                  ✗
    """

    cleaned = []

    for phrase in phrases:
        phrase = " ".join(phrase.split()).strip()

        if not phrase:
            continue

        if len(phrase) < 2:
            continue

        cleaned.append(phrase)

    cleaned = list(set(cleaned))

    # Longest / richest phrases first
    cleaned.sort(key=lambda p: (len(p.split()), len(p)), reverse=True)

    kept = []

    for candidate in cleaned:
        candidate_lower = candidate.lower()

        skip = False

        for existing in kept:
            existing_lower = existing.lower()

            # If candidate is mostly represented by a richer phrase
            if candidate_lower in existing_lower:
                skip = True
                break

        if not skip:
            kept.append(candidate)

    return kept


# Does question analysis and extracts rems for normalization
def analyze_question(question: str):

    nlp = get_nlp()

    result = {
        "verb": "",
        "target": "",
        "entities": [],
        "filters": [],
        "projections": [],
        "noun_chunks": [],
        "phrases": [],
    }

    if nlp is None:
        return result

    doc = nlp(question)

    # ----------------------------------------------------------
    # Main verb
    # ----------------------------------------------------------

    for token in doc:
        if token.pos_ in ("VERB", "AUX") and token.dep_ != "det":
            result["verb"] = token.lemma_.lower()
            break

    # ----------------------------------------------------------
    # Filters
    # ----------------------------------------------------------

    for token in doc:
        if token.pos_ == "ADJ":
            result["filters"].append(token.lemma_.lower())

    # ----------------------------------------------------------
    # Named entities
    # ----------------------------------------------------------

    for ent in doc.ents:
        result["entities"].append(ent.text)

    # ----------------------------------------------------------
    # Target object
    # ----------------------------------------------------------

    for token in doc:
        if token.dep_ in ("dobj", "attr", "pobj", "nsubj") and token.lemma_.lower() not in _INTERROGATIVES:
            result["target"] = token.lemma_.lower()
            break

    # ----------------------------------------------------------
    # Candidate phrase extraction
    # ----------------------------------------------------------

    phrases = set()

    for chunk in doc.noun_chunks:
        phrase = _normalize_phrase(chunk.text)
        if phrase:
            phrases.add(phrase)
            result["noun_chunks"].append(phrase)

    for ent in doc.ents:
        phrase = ent.text.strip()
        if phrase:
            phrases.add(phrase)

    result["phrases"] = _deduplicate_and_rank(phrases)

    return result


def analyze_prompt(prompt: str):
    """
    Extract candidate phrases from arbitrary text.

    Returns a ranked list of phrases suitable for
    embedding classification.
    """

    nlp = get_nlp()
    if nlp is None:
        return []

    doc = nlp(prompt)

    phrases = set()

    # Noun chunks
    for chunk in doc.noun_chunks:
        phrase = _normalize_phrase(chunk.text)

        if phrase:
            phrases.add(phrase)

    # Named entities
    for ent in doc.ents:
        phrase = ent.text.strip()

        if phrase:
            phrases.add(phrase)

    result = _deduplicate_and_rank(phrases)

    logger.debug("Candidate phrases: %s", result)

    return result
