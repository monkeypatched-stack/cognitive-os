"""Drone telemetry -> belief observation pipeline (kernel/edge/drone_state.py,
kernel/pipeline/observations.py's WorldPollingProvider extension).

Not executed via pytest in this session (per standing instruction) — written
so the suite covers this module the next time the full suite is run.
"""

from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.edge.drone_state import (
    DroneCommand,
    DroneCommandResult,
    DroneCommandType,
    DroneState,
    get_drone_adapter,
    is_fresh,
    register_drone_adapter,
    unregister_drone_adapter,
)
from src.monkey_brain.kernel.pipeline.observations import WorldPollingProvider


class _FakeDroneAdapter:
    def __init__(self, state: DroneState | None) -> None:
        self._state = state

    def latest_state(self) -> DroneState | None:
        return self._state


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    unregister_drone_adapter("drone1")


def _state(*, armed=True, x=1.0, y=2.0, z=-3.0, age_s=0.0) -> DroneState:
    return DroneState(
        actor_id="drone1",
        namespace="px4_1",
        armed=armed,
        position_x=x,
        position_y=y,
        position_z=z,
        timestamp=time.time() - age_s,
    )


def test_fresh_telemetry_produces_observations():
    register_drone_adapter("drone1", _FakeDroneAdapter(_state()))
    obs_set = WorldPollingProvider().observe("drone1", None)

    by_attr = {o.attribute: o.value for o in obs_set.observations}
    assert by_attr == {"armed": True, "position_x": 1.0, "position_y": 2.0, "position_z": -3.0}
    assert all(o.provenance.source == "px4_ros" for o in obs_set.observations)


def test_enrichment_fields_are_included_when_present():
    state = DroneState(
        actor_id="drone1",
        namespace="px4_1",
        armed=True,
        position_x=1.0,
        position_y=2.0,
        position_z=-3.0,
        timestamp=time.time(),
        heading=90.0,
        battery=0.6,
        flight_mode="14",
        gps_state="3",
    )
    register_drone_adapter("drone1", _FakeDroneAdapter(state))
    obs_set = WorldPollingProvider().observe("drone1", None)

    by_attr = {o.attribute: o.value for o in obs_set.observations}
    assert by_attr["heading"] == 90.0
    assert by_attr["battery"] == 0.6
    assert by_attr["flight_mode"] == "14"
    assert by_attr["gps_state"] == "3"


def test_missing_enrichment_fields_are_simply_absent_not_none_values():
    """battery=None must not produce an Observation(attribute="battery",
    value=None) -- the whole point of the None-default is "we don't know,"
    which is different from "we observed it to be nothing.\""""
    register_drone_adapter("drone1", _FakeDroneAdapter(_state()))  # no heading/battery/etc.
    obs_set = WorldPollingProvider().observe("drone1", None)

    attrs = {o.attribute for o in obs_set.observations}
    assert attrs == {"armed", "position_x", "position_y", "position_z"}


def test_stale_telemetry_is_never_reported_as_current():
    register_drone_adapter("drone1", _FakeDroneAdapter(_state(age_s=999)))
    obs_set = WorldPollingProvider().observe("drone1", None)

    assert obs_set.observations == ()


def test_no_registered_adapter_is_not_an_error():
    obs_set = WorldPollingProvider().observe("drone1", None)

    assert obs_set.observations == ()


def test_unregistered_actor_never_sees_another_actors_telemetry():
    register_drone_adapter("drone1", _FakeDroneAdapter(_state()))
    obs_set = WorldPollingProvider().observe("some_other_actor", None)

    assert obs_set.observations == ()


def test_adapter_exception_degrades_to_empty_not_a_crash():
    class _BoomAdapter:
        def latest_state(self):
            raise RuntimeError("telemetry read failed")

    register_drone_adapter("drone1", _BoomAdapter())
    obs_set = WorldPollingProvider().observe("drone1", None)

    assert obs_set.observations == ()


def test_world_polling_still_runs_when_world_is_none_and_no_drone_registered():
    """Guards the exact bug this change could have introduced: the early
    `if world is None` return must still fire correctly for every actor
    that has no drone adapter at all (the overwhelming common case)."""
    obs_set = WorldPollingProvider().observe("not-a-drone", None)

    assert obs_set.is_empty()


def test_drone_command_to_capability_parameters_matches_robot_py_shape():
    """kernel/domains/robot.py's TakeoffCapability/WaypointCapability
    expect exactly these keys — this is the contract DroneCommand must not
    silently drift from."""
    assert DroneCommand(DroneCommandType.ARM).to_capability_parameters() == {}
    assert DroneCommand(DroneCommandType.TAKEOFF, height_m=5.0).to_capability_parameters() == {"height_m": 5.0}
    assert DroneCommand(DroneCommandType.WAYPOINT, x=1.0, y=2.0, height_m=3.0).to_capability_parameters() == {
        "x": 1.0,
        "y": 2.0,
        "height_m": 3.0,
    }
    assert DroneCommand(DroneCommandType.LAND).to_capability_parameters() == {}


def test_drone_command_result_from_capability_result_round_trips():
    raw = {"actor_id": "drone1", "namespace": "px4_1", "success": True, "altitude_m": 5.0}
    result = DroneCommandResult.from_capability_result(raw)

    assert result.success is True
    assert result.actor_id == "drone1"
    assert result.extra == {"altitude_m": 5.0}


def test_is_fresh_boundary():
    assert is_fresh(_state(age_s=0.0)) is True
    assert is_fresh(_state(age_s=1000.0)) is False
    assert is_fresh(None) is False


def test_register_then_get_round_trips():
    adapter = _FakeDroneAdapter(_state())
    register_drone_adapter("drone1", adapter)

    assert get_drone_adapter("drone1") is adapter

    unregister_drone_adapter("drone1")

    assert get_drone_adapter("drone1") is None


# ── Simulator-only crash-test collision (kernel/edge/px4_ros_adapter.py's
# "CrashTest" branch, kernel/domains/robot.py::CrashTestCapability) ────────


def _collision_state(**overrides) -> DroneState:
    fields = dict(
        actor_id="drone1",
        namespace="px4_1",
        armed=True,
        position_x=8.25,
        position_y=0.0,
        position_z=-3.0,
        timestamp=time.time(),
        collision_event={"target_landmark": "house_alpha", "simulation_only": True, "crash_test": True},
        disabled=True,
    )
    fields.update(overrides)
    return DroneState(**fields)


def test_collision_event_produces_its_own_observation_with_simulator_provenance():
    register_drone_adapter("drone1", _FakeDroneAdapter(_collision_state()))
    obs_set = WorldPollingProvider().observe("drone1", None)

    collision_obs = [o for o in obs_set.observations if o.attribute == "collision_event"]
    assert len(collision_obs) == 1
    obs = collision_obs[0]
    assert obs.entity == "drone1"
    assert obs.value == {"target_landmark": "house_alpha", "simulation_only": True, "crash_test": True}
    assert obs.confidence == 1.0
    assert obs.provenance.source == "simulator"
    assert obs.provenance.method == "collision_detection"
    assert obs.provenance.reliability == 1.0


def test_collision_event_absent_when_no_collision_has_occurred():
    register_drone_adapter("drone1", _FakeDroneAdapter(_state()))  # collision_event defaults to None
    obs_set = WorldPollingProvider().observe("drone1", None)

    assert all(o.attribute != "collision_event" for o in obs_set.observations)


def test_disabled_true_is_reported_alongside_other_telemetry():
    register_drone_adapter("drone1", _FakeDroneAdapter(_collision_state()))
    obs_set = WorldPollingProvider().observe("drone1", None)

    by_attr = {o.attribute: o.value for o in obs_set.observations}
    assert by_attr["disabled"] is True


def test_disabled_field_defaulting_none_never_appears_as_an_observation():
    """DroneState(...) constructed without mentioning `disabled` at all
    (every pre-existing test fixture, and any future caller that hasn't
    been updated) must not silently start reporting disabled=False --
    None means "not populated," matching every other optional field."""
    register_drone_adapter("drone1", _FakeDroneAdapter(_state()))
    obs_set = WorldPollingProvider().observe("drone1", None)

    assert all(o.attribute != "disabled" for o in obs_set.observations)


def test_collision_event_enters_belief_facts_via_belief_fusion():
    """The collision Observation reaches belief through the SAME
    BeliefFusion.update() every other observation uses -- no special
    crash-specific fusion logic."""
    from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
    from src.monkey_brain.kernel.pipeline.observations import BeliefFusion

    register_drone_adapter("drone1", _FakeDroneAdapter(_collision_state()))
    obs_set = WorldPollingProvider().observe("drone1", None)

    belief = BeliefState(actor_id="drone1")
    BeliefFusion().update(belief, obs_set)

    collision_facts = [f for f in belief.facts if f.attribute == "collision_event"]
    assert len(collision_facts) == 1
    assert collision_facts[0].entity == "drone1"
    assert collision_facts[0].value == {"target_landmark": "house_alpha", "simulation_only": True, "crash_test": True}
