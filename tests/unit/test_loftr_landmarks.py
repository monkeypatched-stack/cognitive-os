"""Unit tests for kernel/edge/loftr_landmarks.py -- the LoFTR-backed visual
landmark matcher (CognitiveOS Drone Camera Video Telemetry, "visual landmark
matching" addition). Uses a fake VisualMatcher (never real torch/kornia/cv2)
matching this repo's existing mocking style (test_video_command_runtime.py)."""

from __future__ import annotations

import sys
import types
from typing import Any

import numpy as np

from src.monkey_brain.kernel.edge.loftr_landmarks import (
    LandmarkMatcher,
    LandmarkReference,
    LoFTRMatcher,
    MatchResult,
    _verify_correspondences,
    load_landmarks,
)


class _FakeMatcher:
    """VisualMatcher stand-in: returns a fixed MatchResult per reference
    image (this test suite uses the reference's `image` field as its own
    name, so results can be keyed by it directly), or raises if configured to."""

    def __init__(self, results_by_ref_name: dict[str, MatchResult] | None = None, raise_for: set[str] | None = None):
        self.results_by_ref_name = results_by_ref_name or {}
        self.raise_for = raise_for or set()

    def match(self, query_frame: Any, reference_image: Any) -> MatchResult:
        if reference_image in self.raise_for:
            raise RuntimeError("boom")
        return self.results_by_ref_name.get(reference_image, MatchResult())


def _ref(landmark_id: str, name: str) -> LandmarkReference:
    return LandmarkReference(landmark_id=landmark_id, name=name, image=name)


def _verified_result(candidate_matches=20, geometric_inliers=15, inlier_ratio=0.8, confidence_mean=0.9) -> MatchResult:
    return MatchResult(
        candidate_matches=candidate_matches,
        geometric_inliers=geometric_inliers,
        inlier_ratio=inlier_ratio,
        confidence_mean=confidence_mean,
    )


def test_verify_correspondences_below_min_matches_never_verifies():
    # candidates < 4 -> geometric_inliers stays 0, so LandmarkMatcher's
    # `>= min_inliers` check can never pass regardless of confidence.
    result = _verify_correspondences(p0=np.zeros((2, 2)), p1=np.zeros((2, 2)), confidence=np.array([0.99, 0.99]))
    assert result.geometric_inliers == 0
    assert result.verified is False


def test_match_frame_successful_verified_match():
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": _verified_result()})
    lm = LandmarkMatcher([ref], matcher, actor_id="drone-a")

    results = lm.match_frame("frame")

    assert len(results) == 1
    assert results[0].landmark_id == "house_alpha"
    assert results[0].verified is True


def test_match_frame_insufficient_correspondences_not_verified():
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": MatchResult(candidate_matches=3, geometric_inliers=0, inlier_ratio=0.0)})
    lm = LandmarkMatcher([ref], matcher, min_matches=12)

    assert lm.match_frame("frame") == []


def test_match_frame_geometrically_inconsistent_not_verified():
    # Plenty of candidates and high confidence, but geometric verification
    # produced too few inliers -- must never be verified off confidence alone.
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher(
        {
            "reference_01": MatchResult(
                candidate_matches=50, geometric_inliers=2, inlier_ratio=0.04, confidence_mean=0.95
            )
        }
    )
    lm = LandmarkMatcher([ref], matcher, min_matches=12, min_inliers=8, min_inlier_ratio=0.45)

    assert lm.match_frame("frame") == []


def test_match_frame_low_confidence_rejected():
    # Matches/inliers thresholds met, but combined score falls below min_score.
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher(
        {"reference_01": MatchResult(candidate_matches=20, geometric_inliers=10, inlier_ratio=0.5, confidence_mean=0.1)}
    )
    lm = LandmarkMatcher([ref], matcher, min_matches=12, min_inliers=8, min_inlier_ratio=0.45, min_score=0.55)

    assert lm.match_frame("frame") == []


def test_match_frame_multiple_references_same_landmark_picks_best_score():
    refs = [_ref("house_alpha", "ref_low"), _ref("house_alpha", "ref_high")]
    matcher = _FakeMatcher(
        {
            "ref_low": _verified_result(inlier_ratio=0.5, confidence_mean=0.5),
            "ref_high": _verified_result(inlier_ratio=0.95, confidence_mean=0.95),
        }
    )
    lm = LandmarkMatcher(refs, matcher)

    results = lm.match_frame("frame")

    assert len(results) == 1
    assert results[0].reference_name == "ref_high"


def test_match_frame_multiple_competing_landmarks_returns_one_per_id():
    refs = [_ref("house_alpha", "a"), _ref("house_beta", "b")]
    matcher = _FakeMatcher({"a": _verified_result(), "b": _verified_result()})
    lm = LandmarkMatcher(refs, matcher)

    results = lm.match_frame("frame")

    assert {r.landmark_id for r in results} == {"house_alpha", "house_beta"}


def test_observations_builds_correct_observation_and_provenance_shape():
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": _verified_result()})
    lm = LandmarkMatcher([ref], matcher, actor_id="drone-a")

    observations = lm.observations("frame")

    assert len(observations) == 1
    obs = observations[0]
    assert obs.entity == "drone-a"
    assert obs.attribute == "visual_landmark_match"
    assert obs.value["landmark_id"] == "house_alpha"
    assert obs.value["geometric_verified"] is True
    assert "match_score" in obs.value and "inlier_ratio" in obs.value
    assert obs.provenance.source == "drone_camera"
    assert obs.provenance.method == "loftr_geometric_match"
    assert set(obs.provenance.metadata) == {"candidate_matches", "geometric_inliers", "reference_name"}


def test_observations_rounds_score_and_inlier_ratio_for_stable_debounce():
    # VideoCommandRuntime debounces on dict equality of obs.value -- a raw
    # unrounded score would differ almost every sampled frame and defeat
    # that debounce for an otherwise-stationary landmark.
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": _verified_result(inlier_ratio=0.8013, confidence_mean=0.9021)})
    lm = LandmarkMatcher([ref], matcher)

    obs = lm.observations("frame")[0]

    assert obs.value["match_score"] == round(obs.value["match_score"], 2)
    assert obs.value["inlier_ratio"] == round(obs.value["inlier_ratio"], 2)


def test_temporal_confirmation_requires_n_verified_before_emitting():
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": _verified_result()})
    lm = LandmarkMatcher([ref], matcher, confirmations=2)

    assert lm.observations("frame1") == ()
    assert len(lm.observations("frame2")) == 1


def test_temporal_confirmation_resets_after_gap(monkeypatch):
    ref = _ref("house_alpha", "reference_01")
    matcher = _FakeMatcher({"reference_01": _verified_result()})
    lm = LandmarkMatcher([ref], matcher, confirmations=2, max_confirmation_gap_seconds=1.0)

    clock = [0.0]
    monkeypatch.setattr("src.monkey_brain.kernel.edge.loftr_landmarks.time.monotonic", lambda: clock[0])

    assert lm.observations("frame1") == ()  # 1st verified sample
    clock[0] = 5.0  # gap exceeds max_confirmation_gap_seconds -> history resets
    assert lm.observations("frame2") == ()  # counts as a fresh 1st sample
    clock[0] = 5.5  # within window
    assert len(lm.observations("frame3")) == 1  # now 2 consecutive


def test_matcher_exception_for_one_reference_does_not_abort_others():
    refs = [_ref("house_alpha", "bad"), _ref("house_beta", "good")]
    matcher = _FakeMatcher({"good": _verified_result()}, raise_for={"bad"})
    lm = LandmarkMatcher(refs, matcher)

    results = lm.match_frame("frame")

    assert [r.landmark_id for r in results] == ["house_beta"]


def test_load_landmarks_loads_multiple_references_per_landmark_id(tmp_path):
    from PIL import Image

    for folder, filenames in {"house_alpha": ["a1.jpg", "a2.png"], "house_beta": ["b1.jpg"]}.items():
        d = tmp_path / folder
        d.mkdir()
        for name in filenames:
            Image.new("RGB", (8, 8), color=(255, 0, 0)).save(d / name)

    refs = load_landmarks(tmp_path)

    assert len(refs) == 3
    assert {r.landmark_id for r in refs} == {"house_alpha", "house_beta"}
    assert all("path" in r.metadata for r in refs)


def test_load_landmarks_skips_invalid_files_and_non_image_suffixes(tmp_path):
    d = tmp_path / "house_alpha"
    d.mkdir()
    (d / "notes.txt").write_text("not an image")
    (d / "corrupt.jpg").write_bytes(b"not actually a jpeg")

    assert load_landmarks(tmp_path) == []


def test_loftr_matcher_ensure_loaded_mps_branch(monkeypatch):
    torch_stub = types.ModuleType("torch")
    torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch_stub.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True))

    loaded_model = object()

    class _FakeLoFTR:
        last_device: str | None = None

        def __init__(self, pretrained):
            self.pretrained = pretrained

        def eval(self):
            return self

        def to(self, device):
            _FakeLoFTR.last_device = device
            return loaded_model

    kornia_stub = types.ModuleType("kornia")
    kornia_feature_stub = types.ModuleType("kornia.feature")
    kornia_feature_stub.LoFTR = _FakeLoFTR
    kornia_stub.feature = kornia_feature_stub

    monkeypatch.setitem(sys.modules, "torch", torch_stub)
    monkeypatch.setitem(sys.modules, "kornia", kornia_stub)
    monkeypatch.setitem(sys.modules, "kornia.feature", kornia_feature_stub)

    matcher = LoFTRMatcher(device="auto")
    matcher._ensure_loaded()

    assert _FakeLoFTR.last_device == "mps"
    assert matcher._model is loaded_model
