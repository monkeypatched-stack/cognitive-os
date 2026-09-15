"""Unit tests for kernel/edge/visual_perception.py -- the CLIP zero-shot
classifier (CognitiveOS Drone Camera Video Telemetry, spec TESTS section
6/7/9: "Visual perception -> Observation conversion", "Observation
provenance/confidence", "Perception failure handling").

Fakes the underlying CLIPImageEmbedder/CLIPTextEmbedder (setting
VisualPerceptionEngine's already-loaded state directly) rather than
requiring transformers/torch -- this environment genuinely has neither
installed, which is itself exercised for real by
test_classify_raises_unavailable_when_model_cannot_load below."""

from __future__ import annotations

import numpy as np
import pytest

from src.monkey_brain.kernel.edge.visual_perception import (
    VisualPerceptionEngine,
    VisualPerceptionUnavailableError,
)


class _FakeEmbedding:
    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector


class _FakeImageEmbedder:
    def __init__(self, vector: np.ndarray, *, raises: bool = False) -> None:
        self._vector = vector
        self._raises = raises

    def embed(self, _frame):
        if self._raises:
            raise RuntimeError("decode failed")
        return _FakeEmbedding(self._vector)


def _engine_with_fakes(image_vector: np.ndarray, prompt_vectors: dict[str, np.ndarray], threshold: float = 0.15):
    engine = VisualPerceptionEngine(threshold=threshold)
    engine._image_embedder = _FakeImageEmbedder(image_vector)
    engine._prompt_vectors = prompt_vectors
    return engine


def test_classify_emits_observation_above_threshold():
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    engine = _engine_with_fakes(vec, {"obstacle_detected": vec})  # identical vector -> similarity 1.0
    results = engine.classify("fake-frame")
    assert len(results) == 1
    assert results[0].attribute == "obstacle_detected"
    assert results[0].value is True
    assert results[0].confidence == pytest.approx(1.0)
    assert results[0].method == "clip_zero_shot_similarity"


def test_classify_omits_below_threshold_candidates():
    image_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    orthogonal_prompt = np.array([0.0, 1.0, 0.0], dtype=np.float32)  # similarity 0.0
    engine = _engine_with_fakes(image_vec, {"person_detected": orthogonal_prompt})
    results = engine.classify("fake-frame")
    assert results == []


def test_classify_can_emit_multiple_recognized_attributes():
    image_vec = np.array([1.0, 1.0, 0.0], dtype=np.float32)
    prompts = {
        "obstacle_detected": np.array([1.0, 0.0, 0.0], dtype=np.float32),
        "person_detected": np.array([0.0, 1.0, 0.0], dtype=np.float32),
        "landing_zone_clear": np.array([0.0, 0.0, 1.0], dtype=np.float32),  # orthogonal -> excluded
    }
    engine = _engine_with_fakes(image_vec, prompts, threshold=0.5)
    attrs = {r.attribute for r in engine.classify("fake-frame")}
    assert attrs == {"obstacle_detected", "person_detected"}


def test_classify_never_raises_on_per_frame_embedding_failure():
    engine = VisualPerceptionEngine()
    engine._image_embedder = _FakeImageEmbedder(np.zeros(3), raises=True)
    engine._prompt_vectors = {"obstacle_detected": np.ones(3)}
    assert engine.classify("fake-frame") == []  # degrades to no observations, never raises


def test_classify_raises_unavailable_when_model_cannot_load():
    # Real path, not mocked: this environment genuinely has no
    # transformers/torch installed, so _ensure_loaded's own import fails.
    engine = VisualPerceptionEngine()
    with pytest.raises(VisualPerceptionUnavailableError):
        engine.classify("fake-frame")
