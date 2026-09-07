"""Robot domain capability.

Closes the swarm-readiness audit's finding (docs/ACTOR_CELL_ARCHITECTURE.md
/ this session's audit) that kernel/edge/ros_integration.py's governed ROS
path had zero production callers: no live capability had ever actually
invoked run_ros_action_if_governed, so "every physical action is governed"
was true only vacuously (there was no physical action to govern).

Deliberately a no-op liveness check, not a real motion capability. The
audit was explicit that inventing a fake capability such as MoveRobot to
satisfy the architecture is out of scope -- HeartbeatCapability proves the
full path (ActionExecutor -> ensure_governed -> capability.handle() ->
run_ros_action_if_governed -> ensure_governed(force_authorize=True) ->
actor-bound RosExecutionAdapter) actually works end-to-end without
fabricating a robot task that doesn't exist.

Registered on the same GroceryCapabilityBus every other real capability
uses (see kernel/domains/grocery.py::build_default_capability_bus) --
per ros_integration.py's own module docstring, that IS "the SAME
CapabilityBus every other capability uses," not a separate robot-only
dispatch path.
"""
from __future__ import annotations

from typing import Any


class HeartbeatCapability:
    """A no-op liveness ping against this actor's own bound ROS adapter.

    context["ros_adapter"] is this actor's own RosExecutionAdapter (set by
    ActorCell.ros_adapter -- see kernel/society/actor_cell.py -- and
    injected into context by the same context_factory that already
    injects context["actor_local_knowledge_graph"]). None for any actor
    that isn't a robot deployment (node_class != "robot"), which is the
    normal, honest case for every non-robot actor -- this capability
    degrades to a clear error, never a crash or a silent no-op success.
    """

    name = "Heartbeat"

    async def handle(self, args: dict) -> dict[str, Any]:
        context = args.get("context", {}) or {}
        actor_id = context.get("actor_id", "")
        adapter = context.get("ros_adapter")
        if adapter is None:
            return {"success": False, "error": "no ROS adapter bound to this actor"}

        from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed

        return await run_ros_action_if_governed(
            capability="Heartbeat",
            resource=f"ros:{actor_id}" if actor_id else "ros",
            parameters={},
            adapter=adapter,
            actor_id=actor_id,
        )


# ── PX4 mission capabilities ─────────────────────────────────────────────
#
# Prompt-driven PX4 demo: these expose exactly the four operations
# kernel/edge/px4_ros_adapter.py::Px4RosExecutionAdapter.invoke() actually
# implements (Arm/Takeoff/Waypoint/Land) as real, governed, planner-
# selectable capabilities -- the same ActionExecutor -> ensure_governed ->
# run_ros_action_if_governed -> actor-bound adapter path HeartbeatCapability
# already proved, now carrying real flight parameters instead of an empty
# no-op. No capability here is invented: the adapter itself is the
# authority on what a "PX4 capability" is, and any name not in
# _PX4_CAPABILITY_LIMITS is refused before governance is even asked,
# exactly like an unsupported capability the adapter would reject anyway
# (Px4RosExecutionAdapter.invoke()'s own `else: unsupported PX4
# capability` branch) -- this is defense in depth, not a second source of
# truth about what PX4 can do.
_MAX_ALTITUDE_M = 50.0
_MAX_HORIZONTAL_M = 100.0


def _validate_number(value: Any, name: str, *, minimum: float, maximum: float) -> tuple[float | None, str]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a number, got {value!r}"
    if number != number or number in (float("inf"), float("-inf")):  # NaN/inf
        return None, f"{name} must be finite, got {value!r}"
    if not (minimum <= number <= maximum):
        return None, f"{name}={number} out of range [{minimum}, {maximum}]"
    return number, ""


class _Px4MissionCapabilityBase:
    """Shared context plumbing for every PX4 mission capability -- the
    part that's identical to HeartbeatCapability above."""

    name = "Px4MissionCapabilityBase"  # overridden by subclasses

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        """Subclasses return (validated_parameters, error). error == "" means valid."""
        return parameters, ""

    async def handle(self, args: dict) -> dict[str, Any]:
        context = args.get("context", {}) or {}
        actor_id = context.get("actor_id", "")
        adapter = context.get("ros_adapter")
        if adapter is None:
            return {"success": False, "error": "no ROS adapter bound to this actor"}

        parameters = (args.get("parameters") or {})
        validated, error = self._validate(parameters)
        if error:
            # Fails BEFORE governance/ROS are ever reached -- an unsafe or
            # malformed request never becomes a PX4 command, and never
            # produces a governance decision about something that was
            # never a real, executable action to begin with.
            return {"success": False, "error": error}

        from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed

        return await run_ros_action_if_governed(
            capability=self.name,
            resource=f"px4:{actor_id}" if actor_id else "px4",
            parameters=validated,
            adapter=adapter,
            actor_id=actor_id,
        )


class ArmCapability(_Px4MissionCapabilityBase):
    """Arm the vehicle. No parameters. Matches Px4RosExecutionAdapter's
    VEHICLE_CMD_COMPONENT_ARM_DISARM(p1=1.0) exactly."""

    name = "Arm"


class TakeoffCapability(_Px4MissionCapabilityBase):
    """Take off to a validated altitude (meters, positive, capped at
    _MAX_ALTITUDE_M -- this is a SITL demo, not a real-world flight
    envelope, so the cap is a sanity bound, not a certified limit)."""

    name = "Takeoff"

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        height_m, error = _validate_number(
            parameters.get("height_m", 2.0), "height_m", minimum=0.1, maximum=_MAX_ALTITUDE_M,
        )
        if error:
            return {}, error
        return {"height_m": height_m}, ""


class WaypointCapability(_Px4MissionCapabilityBase):
    """Fly to a validated (x, y) offset (meters, NED-ish local frame used
    by Px4RosExecutionAdapter._position_setpoint) at a validated altitude."""

    name = "Waypoint"

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        x, error = _validate_number(parameters.get("x", 0.0), "x", minimum=-_MAX_HORIZONTAL_M, maximum=_MAX_HORIZONTAL_M)
        if error:
            return {}, error
        y, error = _validate_number(parameters.get("y", 0.0), "y", minimum=-_MAX_HORIZONTAL_M, maximum=_MAX_HORIZONTAL_M)
        if error:
            return {}, error
        height_m, error = _validate_number(
            parameters.get("height_m", 2.0), "height_m", minimum=0.1, maximum=_MAX_ALTITUDE_M,
        )
        if error:
            return {}, error
        return {"x": x, "y": y, "height_m": height_m}, ""


class LandCapability(_Px4MissionCapabilityBase):
    """Land at the current position. No parameters. Matches
    Px4RosExecutionAdapter's VEHICLE_CMD_NAV_LAND exactly."""

    name = "Land"
