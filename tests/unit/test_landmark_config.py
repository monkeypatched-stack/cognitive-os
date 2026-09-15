"""Unit tests for kernel/edge/landmark_config.py -- env-var config loading
and the process-wide shared-resources singleton for visual landmark
matching (kernel/edge/loftr_landmarks.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.edge import landmark_config
from src.monkey_brain.kernel.edge.landmark_config import (
    LandmarkMatcherConfig,
    get_shared_landmark_resources,
    load_landmark_config_from_env,
)
from src.monkey_brain.kernel.edge.loftr_landmarks import LandmarkReference, LoFTRUnavailableError


@pytest.fixture(autouse=True)
def _reset_singleton():
    """get_shared_landmark_resources() caches process-wide -- reset between
    tests so each test observes its own env/mocking, matching this file's
    own "load once" contract without leaking state across tests."""
    landmark_config._loaded = False
    landmark_config._resources = None
    yield
    landmark_config._loaded = False
    landmark_config._resources = None


def test_load_config_from_env_returns_none_when_reference_dir_unset(monkeypatch):
    monkeypatch.delenv("LANDMARK_REFERENCE_DIR", raising=False)
    assert load_landmark_config_from_env() is None


def test_load_config_from_env_reads_all_thresholds(monkeypatch):
    monkeypatch.setenv("LANDMARK_REFERENCE_DIR", "/some/dir")
    monkeypatch.setenv("LANDMARK_MIN_MATCHES", "20")
    monkeypatch.setenv("LANDMARK_MIN_INLIERS", "10")
    monkeypatch.setenv("LANDMARK_MIN_INLIER_RATIO", "0.6")
    monkeypatch.setenv("LANDMARK_MIN_SCORE", "0.7")
    monkeypatch.setenv("LANDMARK_CONFIRMATIONS", "3")
    monkeypatch.setenv("LANDMARK_MAX_CONFIRMATION_GAP_SECONDS", "20.0")
    monkeypatch.setenv("LANDMARK_DEVICE", "cpu")

    config = load_landmark_config_from_env()

    assert config == LandmarkMatcherConfig(
        reference_directory="/some/dir",
        min_matches=20,
        min_inliers=10,
        min_inlier_ratio=0.6,
        min_score=0.7,
        confirmations=3,
        max_confirmation_gap_seconds=20.0,
        device="cpu",
    )


def test_load_config_from_env_falls_back_on_invalid_values(monkeypatch):
    monkeypatch.setenv("LANDMARK_REFERENCE_DIR", "/some/dir")
    monkeypatch.setenv("LANDMARK_MIN_MATCHES", "not-a-number")

    config = load_landmark_config_from_env()

    assert config.min_matches == LandmarkMatcherConfig(reference_directory="/some/dir").min_matches


def test_config_to_dict_round_trips_fields():
    config = LandmarkMatcherConfig(reference_directory="/d")
    d = config.to_dict()
    assert d["reference_directory"] == "/d"
    assert d["min_matches"] == config.min_matches


def test_get_shared_landmark_resources_returns_none_when_config_absent(monkeypatch):
    monkeypatch.delenv("LANDMARK_REFERENCE_DIR", raising=False)
    assert get_shared_landmark_resources() is None


def test_get_shared_landmark_resources_is_a_singleton(monkeypatch):
    monkeypatch.setenv("LANDMARK_REFERENCE_DIR", "/some/dir")
    fake_refs = [LandmarkReference(landmark_id="house_alpha", name="ref", image="img")]
    load_mock = MagicMock(return_value=fake_refs)
    monkeypatch.setattr(landmark_config, "load_landmarks", load_mock)

    first = get_shared_landmark_resources()
    second = get_shared_landmark_resources()

    load_mock.assert_called_once()
    assert first is second
    references, matcher, config = first
    assert references == tuple(fake_refs)
    assert config.reference_directory == "/some/dir"


def test_get_shared_landmark_resources_returns_none_on_load_failure(monkeypatch):
    monkeypatch.setenv("LANDMARK_REFERENCE_DIR", "/some/dir")
    monkeypatch.setattr(landmark_config, "load_landmarks", MagicMock(side_effect=LoFTRUnavailableError("no pillow")))

    assert get_shared_landmark_resources() is None


def test_get_shared_landmark_resources_returns_none_when_no_references_found(monkeypatch):
    monkeypatch.setenv("LANDMARK_REFERENCE_DIR", "/some/dir")
    monkeypatch.setattr(landmark_config, "load_landmarks", MagicMock(return_value=[]))

    assert get_shared_landmark_resources() is None
