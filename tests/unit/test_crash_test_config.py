"""Unit tests for kernel/edge/crash_test_config.py -- env-var config
loading for the simulator-only crash-test behavior. Mirrors
test_landmark_config.py's structure (the established convention for this
kind of opt-in feature config)."""

from __future__ import annotations

from src.monkey_brain.kernel.edge.crash_test_config import (
    CrashTestConfig,
    load_crash_test_config_from_env,
)


def _clear_env(monkeypatch) -> None:
    for name in (
        "CRASH_TEST_MODE",
        "SIMULATION_ONLY",
        "CRASH_TEST_MAX_VELOCITY",
        "CRASH_TEST_ACCELERATION",
        "CRASH_TEST_TARGET_DISTANCE",
        "CRASH_TEST_COLLISION_RADIUS",
        "CRASH_TEST_MIN_LANDMARK_CONFIDENCE",
        "CRASH_TEST_TARGETS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_disabled_by_default(monkeypatch):
    _clear_env(monkeypatch)
    config = load_crash_test_config_from_env()
    assert config.enabled is False
    assert config.simulation_only is False
    assert config == CrashTestConfig()


def test_requires_both_flags_explicitly_true(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    # SIMULATION_ONLY still unset -> still not armed.
    config = load_crash_test_config_from_env()
    assert config.enabled is True
    assert config.simulation_only is False


def test_reads_all_fields_from_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MODE", "true")
    monkeypatch.setenv("SIMULATION_ONLY", "true")
    monkeypatch.setenv("CRASH_TEST_MAX_VELOCITY", "5.0")
    monkeypatch.setenv("CRASH_TEST_ACCELERATION", "2.0")
    monkeypatch.setenv("CRASH_TEST_TARGET_DISTANCE", "20.0")
    monkeypatch.setenv("CRASH_TEST_COLLISION_RADIUS", "1.5")
    monkeypatch.setenv("CRASH_TEST_MIN_LANDMARK_CONFIDENCE", "0.8")
    monkeypatch.setenv("CRASH_TEST_TARGETS", '{"house_alpha": [15.0, 0.0, -3.0]}')

    config = load_crash_test_config_from_env()

    assert config == CrashTestConfig(
        enabled=True,
        simulation_only=True,
        max_velocity=5.0,
        acceleration=2.0,
        target_distance=20.0,
        collision_radius=1.5,
        min_landmark_confidence=0.8,
        targets={"house_alpha": (15.0, 0.0, -3.0)},
    )


def test_invalid_numeric_value_falls_back_to_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_MAX_VELOCITY", "not-a-number")
    config = load_crash_test_config_from_env()
    assert config.max_velocity == CrashTestConfig().max_velocity


def test_invalid_targets_json_falls_back_to_empty(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_TARGETS", "not json")
    config = load_crash_test_config_from_env()
    assert config.targets == {}


def test_malformed_targets_entry_falls_back_to_empty(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CRASH_TEST_TARGETS", '{"house_alpha": [1.0]}')  # missing coords
    config = load_crash_test_config_from_env()
    assert config.targets == {}


def test_to_dict_round_trips_fields():
    config = CrashTestConfig(enabled=True, simulation_only=True, targets={"house_alpha": (1.0, 2.0, 3.0)})
    d = config.to_dict()
    assert d["enabled"] is True
    assert d["simulation_only"] is True
    assert d["targets"] == {"house_alpha": (1.0, 2.0, 3.0)}
