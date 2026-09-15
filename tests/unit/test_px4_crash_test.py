"""Unit tests for the simulator-only crash-test kinematics
(kernel/edge/px4_ros_adapter.py::run_crash_test_kinematics) and the
"disabled vehicle refuses further commands" invariant on
Px4RosExecutionAdapter.invoke().

Px4RosExecutionAdapter itself cannot be constructed in this environment
(its __init__ requires a real ROS 2/rclpy install, which is not
pip-installable -- see that module's own docstring), so:
  - run_crash_test_kinematics is tested directly: it's a pure function
    taking get_position/set_target as plain callables, extracted
    specifically so this logic is testable without a live ROS 2
    environment.
  - the "disabled" early-return in invoke() is tested by calling
    Px4RosExecutionAdapter.invoke as an unbound function against a
    lightweight duck-typed stand-in object -- valid because that check
    runs BEFORE any capability branch touches an rclpy-specific
    attribute, so no real adapter construction is needed to exercise it.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from src.monkey_brain.kernel.edge.px4_ros_adapter import (
    Px4RosExecutionAdapter,
    run_crash_test_kinematics,
)


def _fake_clock():
    clock = [0.0]
    return (lambda: clock[0]), (lambda dt: clock.__setitem__(0, clock[0] + dt))


def _tracking_position(start: tuple[float, float, float]) -> tuple[Any, Any]:
    """get_position/set_target pair where telemetry perfectly tracks the
    commanded setpoint -- isolates the kinematics profile itself
    (ramp/bounds/stop conditions) from any telemetry-lag concern."""
    state = {"pos": start}

    def get_position() -> tuple[float, float, float] | None:
        return state["pos"]

    def set_target(x: float, y: float, z: float) -> None:
        state["pos"] = (x, y, z)

    return get_position, set_target


def test_velocity_never_exceeds_max_velocity():
    get_position, set_target = _tracking_position((0.0, 0.0, 0.0))
    monotonic, sleep = _fake_clock()
    positions: list[tuple[float, float, float]] = []

    def recording_set_target(x, y, z):
        positions.append((x, y, z))
        set_target(x, y, z)

    run_crash_test_kinematics(
        start=(0.0, 0.0, 0.0),
        target=(100.0, 0.0, 0.0),  # far away -- never collides, exercises the full ramp
        max_velocity=3.0,
        acceleration=1.0,
        target_distance=15.0,
        collision_radius=2.0,
        get_position=get_position,
        set_target=recording_set_target,
        control_hz=10.0,
        timeout_s=60.0,
        sleep=sleep,
        monotonic=monotonic,
    )

    period = 1.0 / 10.0
    deltas = [positions[i + 1][0] - positions[i][0] for i in range(len(positions) - 1)]
    assert all(d <= 3.0 * period + 1e-9 for d in deltas)


def test_travel_never_exceeds_target_distance_when_no_collision():
    get_position, set_target = _tracking_position((0.0, 0.0, 0.0))
    monotonic, sleep = _fake_clock()

    outcome = run_crash_test_kinematics(
        start=(0.0, 0.0, 0.0),
        target=(1000.0, 0.0, 0.0),  # unreachable within target_distance
        max_velocity=3.0,
        acceleration=1.0,
        target_distance=15.0,
        collision_radius=2.0,
        get_position=get_position,
        set_target=set_target,
        control_hz=10.0,
        timeout_s=60.0,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert outcome["collided"] is False
    assert outcome["traveled"] <= 15.0


def test_collision_detected_within_collision_radius_of_target():
    get_position, set_target = _tracking_position((0.0, 0.0, 0.0))
    monotonic, sleep = _fake_clock()

    outcome = run_crash_test_kinematics(
        start=(0.0, 0.0, 0.0),
        target=(10.0, 0.0, 0.0),
        max_velocity=3.0,
        acceleration=1.0,
        target_distance=15.0,
        collision_radius=2.0,
        get_position=get_position,
        set_target=set_target,
        control_hz=10.0,
        timeout_s=60.0,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert outcome["collided"] is True
    assert outcome["traveled"] <= 15.0


def test_timeout_is_a_hard_backstop_even_without_telemetry():
    # get_position always returns None (telemetry never arrives) -- the
    # loop must still terminate via the timeout, never hang.
    monotonic, sleep = _fake_clock()

    outcome = run_crash_test_kinematics(
        start=(0.0, 0.0, 0.0),
        target=(1000.0, 0.0, 0.0),
        max_velocity=3.0,
        acceleration=1.0,
        target_distance=1_000_000.0,  # would otherwise never stop on distance alone
        collision_radius=2.0,
        get_position=lambda: None,
        set_target=lambda x, y, z: None,
        control_hz=10.0,
        timeout_s=5.0,
        sleep=sleep,
        monotonic=monotonic,
    )

    assert outcome["collided"] is False


@pytest.mark.asyncio
async def test_disabled_vehicle_refuses_any_subsequent_command():
    """Px4RosExecutionAdapter.invoke()'s disabled check runs before any
    capability branch touches rclpy, so a duck-typed stand-in (not a real
    adapter) is sufficient to exercise it."""
    fake_self = types.SimpleNamespace(actor_id="drone-a", namespace="px4_1", _disabled=True)

    result = await Px4RosExecutionAdapter.invoke(fake_self, capability="Waypoint", parameters={"x": 1.0, "y": 2.0})

    assert result["success"] is False
    assert "disabled" in result["error"]


@pytest.mark.asyncio
async def test_non_disabled_vehicle_reaches_capability_dispatch():
    """Same duck-typed technique, confirming the disabled check does NOT
    block a normal (non-CrashTest, non-disabled) capability -- an
    unsupported capability name reaching the final `else` branch proves
    dispatch was actually attempted, not short-circuited."""
    fake_self = types.SimpleNamespace(actor_id="drone-a", namespace="px4_1", _disabled=False)

    result = await Px4RosExecutionAdapter.invoke(fake_self, capability="NotARealCapability", parameters={})

    assert result["success"] is False
    assert "unsupported" in result["error"]
