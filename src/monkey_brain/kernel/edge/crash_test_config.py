"""Configuration for the simulator-only crash-test behavior
(kernel/domains/robot.py::CrashTestCapability, kernel/edge/
px4_ros_adapter.py's "CrashTest" branch).

Follows kernel/edge/landmark_config.py's exact shape (plain frozen
dataclass + to_dict(), env-var loader that never raises on a bad value) --
the established, and only, convention for this kind of opt-in feature
config in this codebase.

THIS MODULE IS NOT A SAFETY GATE BY ITSELF. `enabled`/`simulation_only`
being True here is necessary but not sufficient to arm a crash-test --
CrashTestCapability additionally requires the actor-bound adapter's own
`is_simulation` attribute to be True (kernel-controlled, not env-driven),
and run_ros_action_if_governed/agentos_governance.rego independently
recompute simulation_only/crash_test_mode from these SAME env vars (not
from anything this module's caller passes them) before the OPA policy
layer allows the action through. See px4_ros_adapter.py's `is_simulation`
docstring and ros_integration.py's `run_ros_action_if_governed` for the
other layers.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.edge.crash_test_config")


@dataclass(frozen=True)
class CrashTestConfig:
    enabled: bool = False  # CRASH_TEST_MODE
    simulation_only: bool = False  # SIMULATION_ONLY -- both must be true to arm
    max_velocity: float = 3.0  # m/s
    acceleration: float = 1.0  # m/s^2
    target_distance: float = 15.0  # m, max flight distance toward target
    collision_radius: float = 2.0  # m, distance-to-target counted as impact
    min_landmark_confidence: float = 0.7
    # landmark_id -> (x, y, z) local NED offset from the vehicle's own EKF
    # origin, matching Px4RosExecutionAdapter._position_setpoint's frame.
    targets: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "simulation_only": self.simulation_only,
            "max_velocity": self.max_velocity,
            "acceleration": self.acceleration,
            "target_distance": self.target_distance,
            "collision_radius": self.collision_radius,
            "min_landmark_confidence": self.min_landmark_confidence,
            "targets": dict(self.targets),
        }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _env_number(name: str, default: float, cast: type) -> Any:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning("crash_test_config: invalid %s=%r, using default %r", name, raw, default)
        return default


def _env_targets(name: str) -> dict[str, tuple[float, float, float]]:
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return {
            str(landmark_id): (float(coords[0]), float(coords[1]), float(coords[2]))
            for landmark_id, coords in parsed.items()
        }
    except (TypeError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        logger.warning("crash_test_config: invalid %s=%r, ignoring (no targets configured)", name, raw)
        return {}


def load_crash_test_config_from_env() -> CrashTestConfig:
    """Never raises -- an invalid value falls back to a safe (disabled)
    default for that field, same posture as landmark_config.py."""
    defaults = CrashTestConfig()
    return CrashTestConfig(
        enabled=_env_bool("CRASH_TEST_MODE", defaults.enabled),
        simulation_only=_env_bool("SIMULATION_ONLY", defaults.simulation_only),
        max_velocity=_env_number("CRASH_TEST_MAX_VELOCITY", defaults.max_velocity, float),
        acceleration=_env_number("CRASH_TEST_ACCELERATION", defaults.acceleration, float),
        target_distance=_env_number("CRASH_TEST_TARGET_DISTANCE", defaults.target_distance, float),
        collision_radius=_env_number("CRASH_TEST_COLLISION_RADIUS", defaults.collision_radius, float),
        min_landmark_confidence=_env_number(
            "CRASH_TEST_MIN_LANDMARK_CONFIDENCE", defaults.min_landmark_confidence, float
        ),
        targets=_env_targets("CRASH_TEST_TARGETS"),
    )
