"""Canonical drone state/command contract — a typed layer over what already
exists, not a new action vocabulary.

`DroneCommand`'s four members are exactly the four capabilities
`Px4RosExecutionAdapter.invoke()` (px4_ros_adapter.py) implements and
`kernel/domains/robot.py`'s Arm/Takeoff/Waypoint/Land capabilities already
expose through governance — this module does not invent anything new to
fly, it just gives the existing dict-shaped parameters/results a name.

`DroneState.armed`/`.position_*` come from telemetry `Px4RosExecutionAdapter`
already subscribed to for its own Arm/Takeoff/Waypoint/Land outcome
confirmation (vehicle_status, vehicle_local_position). `.battery`/
`.heading`/`.flight_mode`/`.gps_state` come from enrichment subscriptions
added alongside those (battery_status, vehicle_gps_position, vehicle_
attitude, plus vehicle_status's own nav_state) — every field is read via
getattr with a None default in px4_ros_adapter.py's latest_state(), so an
unexpected px4_msgs field shape degrades that one field to None rather than
raising. SensorGps's field names (latitude_deg/longitude_deg/
altitude_msl_m, topic vehicle_gps_position) are confirmed from this repo's
own deploy/k8s/px4-sim-deployment.yaml Foxglove bridge; BatteryStatus/
VehicleAttitude are standard, long-stable PX4 messages but weren't
independently confirmed against a running px4_msgs build while writing
this (none was available) — see px4_ros_adapter.py's own __init__ comment
for the exact confidence level per field. `.sim_timestamp` is PX4's own
onboard clock (microseconds since boot, from vehicle_status.timestamp),
deliberately distinct from `.timestamp` (this process's wall clock).

This module also owns the process-local actor_id -> adapter registry
`DroneObservationProvider` (kernel/pipeline/observations.py's
WorldPollingProvider, extended) reads from — same module-level-singleton-
registry shape this codebase already uses for get_audit_log()/
get_governance_engine(), not a new registry pattern.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DroneCommandType(str, Enum):
    ARM = "Arm"
    TAKEOFF = "Takeoff"
    WAYPOINT = "Waypoint"
    LAND = "Land"


@dataclass(frozen=True)
class DroneCommand:
    """A typed view over what kernel/domains/robot.py's PX4 mission
    capabilities already accept as `parameters` — construct one of these
    and call `.to_capability_parameters()` where a dict is expected, rather
    than hand-building the dict inline."""

    command: DroneCommandType
    height_m: float | None = None  # Takeoff, Waypoint
    x: float | None = None  # Waypoint
    y: float | None = None  # Waypoint

    def to_capability_parameters(self) -> dict[str, Any]:
        if self.command == DroneCommandType.TAKEOFF:
            return {"height_m": self.height_m if self.height_m is not None else 2.0}
        if self.command == DroneCommandType.WAYPOINT:
            return {
                "x": self.x if self.x is not None else 0.0,
                "y": self.y if self.y is not None else 0.0,
                "height_m": self.height_m if self.height_m is not None else 2.0,
            }
        return {}


@dataclass(frozen=True)
class DroneCommandResult:
    """Typed view over what Px4RosExecutionAdapter.invoke() already
    returns — success/error plus whatever extra fields that capability's
    branch included (altitude_m for Takeoff, x/y for Waypoint)."""

    success: bool
    actor_id: str = ""
    namespace: str = ""
    error: str = ""
    extra: dict[str, Any] | None = None

    @classmethod
    def from_capability_result(cls, result: dict[str, Any]) -> "DroneCommandResult":
        known = {"success", "actor_id", "namespace", "error"}
        return cls(
            success=bool(result.get("success", False)),
            actor_id=str(result.get("actor_id", "")),
            namespace=str(result.get("namespace", "")),
            error=str(result.get("error", "")),
            extra={k: v for k, v in result.items() if k not in known} or None,
        )


@dataclass(frozen=True)
class DroneState:
    """Populated fields reflect exactly what Px4RosExecutionAdapter tracks
    today (see that module's `latest_state()`). Fields this repo's adapter
    doesn't subscribe to yet stay `None` — never fabricated."""

    actor_id: str
    namespace: str
    armed: bool | None
    position_x: float | None
    position_y: float | None
    position_z: float | None
    timestamp: float
    # Populated by Px4RosExecutionAdapter.latest_state() from its enrichment
    # subscriptions (battery_status / vehicle_gps_position / vehicle_attitude
    # / vehicle_status.nav_state) — each read via getattr with a None
    # default, so a missing px4_msgs message TYPE or PUBLISHER degrades that
    # one field, never the snapshot. None here means "this build/publisher
    # did not supply it," not "not implemented."
    heading: float | None = None
    battery: float | None = None
    flight_mode: str | None = None
    gps_state: str | None = None
    sim_timestamp: float | None = None
    # Simulator-only crash-test support (kernel/domains/robot.py::
    # CrashTestCapability, kernel/edge/px4_ros_adapter.py's "CrashTest"
    # branch). collision_event mirrors the exact dict shape that becomes a
    # collision_event Observation's value (kernel/pipeline/observations.py::
    # WorldPollingProvider.observe()) -- None means no collision has
    # occurred for this adapter's lifetime, never fabricated.
    collision_event: dict[str, Any] | None = None
    # bool | None (not a plain bool defaulting False): matches the
    # "None = not populated" convention every other optional field on this
    # dataclass already uses -- a plain False default would silently appear
    # in every EXISTING DroneState(...) test fixture that doesn't mention
    # this field at all (dataclass defaults apply even when a caller never
    # asked for it), breaking exact by-attribute assertions elsewhere in
    # this codebase. Px4RosExecutionAdapter.latest_state() always passes a
    # concrete True/False explicitly regardless of this default, so real
    # telemetry is unaffected.
    disabled: bool | None = None


_registry_lock = threading.Lock()
_adapters: dict[str, Any] = {}


def register_drone_adapter(actor_id: str, adapter: Any) -> None:
    """Called once by actor_runtime.py's ActorRuntime.start(), right after
    it binds a Px4RosExecutionAdapter to this Pod's own actor_id — the same
    place ActorCell.ros_adapter itself gets set. Process-local: a robot
    deployment hosts exactly one actor per Pod (Actor Artifact model), so
    this dict holds at most one real entry in production; kept as a dict
    (not a single module global) so tests can register/clear multiple
    fakes without cross-talk.
    """
    with _registry_lock:
        _adapters[actor_id] = adapter


def unregister_drone_adapter(actor_id: str) -> None:
    with _registry_lock:
        _adapters.pop(actor_id, None)


def get_drone_adapter(actor_id: str) -> Any:
    with _registry_lock:
        return _adapters.get(actor_id)


# Telemetry older than this is treated as absent, not current — matches
# Section 17's "never treat stale telemetry as current state." PX4 publishes
# at well over 1Hz in normal operation; a multi-second gap means the link
# (or the simulator) is actually down, not just a slow tick.
STALE_TELEMETRY_SECONDS = 5.0


def is_fresh(state: DroneState | None, *, now: float | None = None) -> bool:
    if state is None:
        return False
    return (now if now is not None else time.time()) - state.timestamp <= STALE_TELEMETRY_SECONDS
