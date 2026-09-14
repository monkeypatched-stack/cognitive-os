"""Embedding-based intent classifier with logistic regression head."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.classifier.intent_examples import INTENT_EXAMPLES


import logging
import numpy as np
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singleton
_classifier: "EmbedClassifier | None" = None


class EmbedClassifier:
    """
    Intent classifier using embeddings + logistic regression.
    Model-agnostic: swap the SentenceTransformer and retrain.
    ~30ms per call after warm-up. No generation, no Ollama round trip.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu")
        self.intent_labels: list[str] = []
        self.intent_matrix: np.ndarray = self._build_matrix()
        self.classifier = self._train_classifier()

    def _build_matrix(self) -> np.ndarray:
        sentences, labels = [], []
        for intent, examples in INTENT_EXAMPLES.items():
            for ex in examples:
                sentences.append(ex)
                labels.append(intent)
        self.intent_labels = labels
        vecs = self.model.encode(sentences, normalize_embeddings=True, show_progress_bar=False)
        return vecs

    def _train_classifier(self):
        """Train a logistic regression classifier on embeddings."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import LabelEncoder

        X = self.intent_matrix
        y = np.array(self.intent_labels)

        le = LabelEncoder()
        y_encoded = le.fit_transform(y)

        clf = LogisticRegression(
            max_iter=1000,
            C=10.0,
            class_weight="balanced",
            solver="lbfgs",
        )
        clf.fit(X, y_encoded)
        self._label_encoder = le
        return clf

    def invalidate(self):
        """Force matrix rebuild on next classify() call — called after DB example updates."""
        self.intent_matrix = None
        self.classifier = None

    def classify(self, prompt: str, question_analysis: dict, threshold: float = 0.45) -> dict[str, Any] | None:
        if self.intent_matrix is None:
            self.intent_matrix = self._build_matrix()
            self.classifier = self._train_classifier()

        qvec = self.model.encode([prompt], normalize_embeddings=True)[0]

        proba = self.classifier.predict_proba([qvec])[0]
        best_idx = int(np.argmax(proba))
        confidence = float(proba[best_idx])
        intent = self._label_encoder.inverse_transform([best_idx])[0]

        if confidence < threshold:
            logger.info("Below threshold (%.2f) — keyword fallback", confidence)
            return None

        return {"pipeline_id": intent, "confidence": confidence}


# --- Module-level functions — these are what callers import ---


def get_classifier() -> EmbedClassifier:
    """Singleton — model loads once at startup, stays resident."""
    global _classifier
    if _classifier is None:
        logger.info("Loading intent embedding model (one-time)...")
        _classifier = EmbedClassifier()
        logger.info("Intent model ready.")
    return _classifier


def classify_intent(prompt: str, question_analysis: dict, threshold: float = 0.0) -> dict[str, Any] | None:
    """~30ms, synchronous, safe to call from async context without await.

    Returns a dict with:
      - intent: the intent class string (e.g. "work_order_query")
      - confidence: float 0-1
      - pipeline_id: resolved pipeline name
    Or None if below threshold.
    """
    try:
        result = get_classifier().classify(prompt, question_analysis, threshold)

        if result is None:
            return None
        return {
            "intent": str(result["pipeline_id"]),
            "confidence": float(result["confidence"]),
            "pipeline_id": str(result["pipeline_id"]),
        }
    except Exception as e:
        logger.warning("Embed classifier error (%s): %s", type(e).__name__, e)
        return None
