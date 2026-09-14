import logging

logger = logging.getLogger(__name__)

try:
    import spacy

    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    logger.warning("Could not load spaCy model: %s", e)
    nlp = None


class _MinimalNLP:
    def __call__(self, text):
        return _MinimalDoc(text)


class _MinimalDoc:
    def __init__(self, text):
        self.text = text
        self.tokens = [_MinimalToken(t) for t in text.split()]
        self.ents = []
        self.noun_chunks = []

    def __iter__(self):
        return iter(self.tokens)


class _MinimalToken:
    def __init__(self, text):
        self.text = text
        self.lemma_ = text.lower()
        self.pos_ = "NOUN"
        self.dep_ = "nsubj"


def get_nlp():
    return nlp or _MinimalNLP()
