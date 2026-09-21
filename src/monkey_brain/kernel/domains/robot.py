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

import logging
from typing import Any

logger = logging.getLogger("agentos.domains.robot")


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

        from src.monkey_brain.kernel.edge.ros_integration import (
            run_ros_action_if_governed,
        )

        return await run_ros_action_if_governed(
            capability="Heartbeat",
            resource=f"ros:{actor_id}" if actor_id else "ros",
            parameters={},
            adapter=adapter,
            actor_id=actor_id,
            idempotency_key=args.get("idempotency_key"),
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


def _validate_number(
    value: Any, name: str, *, minimum: float, maximum: float
) -> tuple[float | None, str]:
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

        parameters = args.get("parameters") or {}
        validated, error = self._validate(parameters)
        if error:
            # Fails BEFORE governance/ROS are ever reached -- an unsafe or
            # malformed request never becomes a PX4 command, and never
            # produces a governance decision about something that was
            # never a real, executable action to begin with.
            return {"success": False, "error": error}

        from src.monkey_brain.kernel.edge.ros_integration import (
            run_ros_action_if_governed,
        )

        return await run_ros_action_if_governed(
            capability=self.name,
            resource=f"px4:{actor_id}" if actor_id else "px4",
            parameters=validated,
            adapter=adapter,
            actor_id=actor_id,
            # Physical-effect idempotency (ros_integration.py::
            # _invoke_idempotent): threaded from ActionExecutor's own
            # stable per-step key so a resumed/replayed plan step can
            # never move the drone a second time. None for any caller
            # that doesn't supply one (e.g. the standalone demo), which
            # preserves the prior always-execute behavior exactly.
            idempotency_key=args.get("idempotency_key"),
        )


class ArmCapability(_Px4MissionCapabilityBase):
    """Arm the vehicle. No parameters. Matches Px4RosExecutionAdapter's
    VEHICLE_CMD_COMPONENT_ARM_DISARM(p1=1.0) exactly."""

    name = "Arm"
    # Surfaced in the planner's "Available actions" prompt (context_engine.py's
    # _retrieve_available_capabilities) -- confirmed live that without this,
    # a real plan for "take off and hover" never included an Arm step at
    # all: Px4RosExecutionAdapter.invoke()'s own Takeoff branch never arms
    # (that's Arm's job, a separately governed capability on purpose), so
    # the vehicle sat disarmed for the plan's entire Takeoff wait budget.
    # Nothing in the bare capability name told the model these are two
    # required, ordered steps rather than one.
    description = "arms the vehicle; required before Takeoff will climb"


class TakeoffCapability(_Px4MissionCapabilityBase):
    """Take off to a validated altitude (meters, positive, capped at
    _MAX_ALTITUDE_M -- this is a SITL demo, not a real-world flight
    envelope, so the cap is a sanity bound, not a certified limit)."""

    name = "Takeoff"
    description = "climbs to altitude; the vehicle must already be armed (call Arm first)"

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        height_m, error = _validate_number(
            parameters.get("height_m", 2.0),
            "height_m",
            minimum=0.1,
            maximum=_MAX_ALTITUDE_M,
        )
        if error:
            return {}, error
        return {"height_m": height_m}, ""


class WaypointCapability(_Px4MissionCapabilityBase):
    """Fly to a validated (x, y) offset (meters, NED-ish local frame used
    by Px4RosExecutionAdapter._position_setpoint) at a validated altitude."""

    name = "Waypoint"
    description = "flies to an (x, y) offset at altitude; the vehicle must already be airborne (Takeoff first)"

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        x, error = _validate_number(
            parameters.get("x", 0.0),
            "x",
            minimum=-_MAX_HORIZONTAL_M,
            maximum=_MAX_HORIZONTAL_M,
        )
        if error:
            return {}, error
        y, error = _validate_number(
            parameters.get("y", 0.0),
            "y",
            minimum=-_MAX_HORIZONTAL_M,
            maximum=_MAX_HORIZONTAL_M,
        )
        if error:
            return {}, error
        height_m, error = _validate_number(
            parameters.get("height_m", 2.0),
            "height_m",
            minimum=0.1,
            maximum=_MAX_ALTITUDE_M,
        )
        if error:
            return {}, error
        return {"x": x, "y": y, "height_m": height_m}, ""


class LandCapability(_Px4MissionCapabilityBase):
    """Land at the current position. No parameters. Matches
    Px4RosExecutionAdapter's VEHICLE_CMD_NAV_LAND exactly."""

    name = "Land"
    description = "lands and disarms at the current position; ends the flight"


# ── Nav2 ground-robot capabilities ───────────────────────────────────────
#
# Ground-robot analog of the PX4 mission capabilities above: exposes exactly
# the two operations kernel/edge/nav2_ros_adapter.py::Nav2RosExecutionAdapter.
# invoke() implements (NavigateToPose/Stop) as real, governed, planner-
# selectable capabilities, through the SAME ActionExecutor -> ensure_governed
# -> run_ros_action_if_governed -> actor-bound adapter path every other
# robot capability in this file already uses. No capability here is
# invented: the adapter itself is the authority on what a "Nav2 capability"
# is, and any name not handled here is refused before governance is even
# asked, same defense-in-depth posture _Px4MissionCapabilityBase's own
# comment describes.
_MAX_GROUND_DISTANCE_M = 100.0


class _Nav2MissionCapabilityBase:
    """Shared context plumbing for every Nav2 mission capability -- same
    shape as _Px4MissionCapabilityBase above, kept as a separate class
    (not a shared base) so a future change to one vehicle kind's plumbing
    can never accidentally change the other's."""

    name = "Nav2MissionCapabilityBase"  # overridden by subclasses

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        return parameters, ""

    async def handle(self, args: dict) -> dict[str, Any]:
        context = args.get("context", {}) or {}
        actor_id = context.get("actor_id", "")
        adapter = context.get("ros_adapter")
        if adapter is None:
            return {"success": False, "error": "no ROS adapter bound to this actor"}

        parameters = args.get("parameters") or {}
        validated, error = self._validate(parameters)
        if error:
            return {"success": False, "error": error}

        from src.monkey_brain.kernel.edge.ros_integration import (
            run_ros_action_if_governed,
        )

        return await run_ros_action_if_governed(
            capability=self.name,
            resource=f"nav2:{actor_id}" if actor_id else "nav2",
            parameters=validated,
            adapter=adapter,
            actor_id=actor_id,
            # Same physical-effect idempotency threading as the PX4
            # capabilities above.
            idempotency_key=args.get("idempotency_key"),
        )


class NavigateToPoseCapability(_Nav2MissionCapabilityBase):
    """Drive to a validated (x, y) pose at a validated heading. Matches
    Nav2RosExecutionAdapter's NavigateToPose branch (the stock Nav2
    `navigate_to_pose` action) exactly."""

    name = "NavigateToPose"
    description = "drives to an (x, y) pose at a heading (yaw_deg); the robot must already be spawned in the map"

    def _validate(self, parameters: dict) -> tuple[dict, str]:
        x, error = _validate_number(
            parameters.get("x", 0.0),
            "x",
            minimum=-_MAX_GROUND_DISTANCE_M,
            maximum=_MAX_GROUND_DISTANCE_M,
        )
        if error:
            return {}, error
        y, error = _validate_number(
            parameters.get("y", 0.0),
            "y",
            minimum=-_MAX_GROUND_DISTANCE_M,
            maximum=_MAX_GROUND_DISTANCE_M,
        )
        if error:
            return {}, error
        yaw_deg, error = _validate_number(
            parameters.get("yaw_deg", 0.0),
            "yaw_deg",
            minimum=-180.0,
            maximum=180.0,
        )
        if error:
            return {}, error
        return {"x": x, "y": y, "yaw_deg": yaw_deg}, ""


class StopCapability(_Nav2MissionCapabilityBase):
    """Cancel any in-flight NavigateToPose goal. No parameters. Matches
    Nav2RosExecutionAdapter's Stop branch exactly."""

    name = "Stop"
    description = "cancels the current navigation goal, if any, and halts the robot"


def _find_actor_belief(context: dict, actor_id: str) -> Any:
    """Same tiny actor-state lookup kernel/edge/video_command_runtime.py's
    own _find_actor_state does -- duplicated rather than imported (routes/
    kernel edge modules depend on kernel, never the reverse; three lines,
    not a second architecture). Returns None if the planetary_runtime
    isn't in context or the actor can't be found -- never raises.

    Prefers state.actor.pipeline_belief() over state.belief_state --
    exactly the precedent api/routes/actors.py's own GET /actors/{id}/
    beliefs route already established: state.belief_state (ActorRuntimeState's
    own field, the OLDER kernel/society/belief.py::BeliefState) is only
    ever written by POST /actors/{id}/observe and the society-level
    coordinated-tick path -- the REAL per-actor cognitive tick that LoFTR
    observations flow through (WorldPollingProvider -> BeliefFusion) never
    touches it. The canonical, actually-live belief is
    state.actor.pipeline_belief() (kernel/pipeline/belief_state.py::
    BeliefState) -- checking the wrong one here would mean a landmark that
    IS visually verified could never satisfy this check."""
    pr = context.get("planetary_runtime")
    if pr is None:
        return None
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is None:
            continue
        pipeline_belief = getattr(getattr(state, "actor", None), "pipeline_belief", None)
        if callable(pipeline_belief):
            return pipeline_belief()
        return getattr(state, "belief_state", None)
    return None


class CrashTestCapability:
    """SIMULATOR-ONLY. Deliberate crash-test demo behavior: flies the
    simulated vehicle into an already visually-identified landmark to
    demonstrate the full perception (LoFTR) -> cognition (belief) ->
    action (this capability) -> consequence (simulated collision) ->
    observation (kernel/pipeline/observations.py's WorldPollingProvider)
    loop end-to-end, inside Gazebo SITL only.

    This is ONE of four independent safety layers (the others: kernel/edge/
    ros_integration.py::run_ros_action_if_governed recomputing simulation
    signals from kernel-trusted evidence, opa/policies/agentos_governance.rego's
    crash_test_unsafe deny rule, and Px4RosExecutionAdapter.is_simulation).
    Every check below runs BEFORE governance is even asked, matching
    _Px4MissionCapabilityBase's own "an unsafe/malformed request never
    produces a governance decision" contract -- but this class does NOT
    subclass it, because it needs context["planetary_runtime"] (for the
    belief check below) which that base class's parameter-only _validate()
    has no way to reach.

    Never accepts an arbitrary real-world target: `landmark_id` is only an
    intent selector -- the actual authority is whether that landmark is
    ALREADY recorded as a geometric_verified=True visual_landmark_match
    fact in THIS actor's own belief state (populated by LoFTR via
    kernel/edge/loftr_landmarks.py -> kernel/edge/video_command_runtime.py
    -> WorldPollingProvider, never trusted from this capability's own
    caller-supplied parameters). A plain camera observation that never
    passed LoFTR's geometric verification can never satisfy this."""

    name = "CrashTest"

    async def handle(self, args: dict) -> dict[str, Any]:
        context = args.get("context", {}) or {}
        actor_id = context.get("actor_id", "")
        adapter = context.get("ros_adapter")
        if adapter is None:
            return {"success": False, "error": "no ROS adapter bound to this actor"}

        from src.introspection.otel_bridge import get_bridge
        from src.monkey_brain.kernel.edge.crash_test_config import load_crash_test_config_from_env

        config = load_crash_test_config_from_env()
        bridge = get_bridge()

        # Layer 1: SIMULATION_ONLY + CRASH_TEST_MODE + adapter.is_simulation
        # must ALL be true. No mechanism exists anywhere to bypass this --
        # a real vehicle (a future adapter with is_simulation=False) is
        # refused here regardless of env vars.
        if not (config.enabled and config.simulation_only and getattr(adapter, "is_simulation", False)):
            bridge.emit_counter("crash_test_rejected", actor_id=actor_id, reason="not_armed_for_simulation")
            return {
                "success": False,
                "error": "crash-test not armed: requires CRASH_TEST_MODE=true, SIMULATION_ONLY=true, "
                "and a simulation-backed adapter",
            }

        parameters = args.get("parameters") or {}
        landmark_id = str(parameters.get("landmark_id", "")).strip()
        if not landmark_id:
            bridge.emit_counter("crash_test_rejected", actor_id=actor_id, reason="missing_landmark_id")
            return {"success": False, "error": "landmark_id is required"}

        # Layer 1, continued: the target must already be visually verified
        # in belief -- never trust the parameter alone.
        belief = _find_actor_belief(context, actor_id)
        verified = False
        if belief is not None:
            for fact in belief.facts:
                if (
                    fact.entity == actor_id
                    and fact.attribute == "visual_landmark_match"
                    and isinstance(fact.value, dict)
                    and fact.value.get("landmark_id") == landmark_id
                    and fact.value.get("geometric_verified") is True
                    and fact.confidence >= config.min_landmark_confidence
                ):
                    verified = True
                    break
        if not verified:
            bridge.emit_counter(
                "crash_test_rejected", actor_id=actor_id, landmark_id=landmark_id, reason="landmark_not_verified"
            )
            return {
                "success": False,
                "error": f"landmark {landmark_id!r} is not a geometric_verified visual_landmark_match "
                f"fact (confidence >= {config.min_landmark_confidence}) in this actor's belief state",
            }

        # VISION identified WHAT structure; this ACTION layer resolves the
        # corresponding simulator target -- never the other way around, and
        # never hard-coded inside the perception module.
        target = config.targets.get(landmark_id)
        if target is None:
            bridge.emit_counter(
                "crash_test_rejected", actor_id=actor_id, landmark_id=landmark_id, reason="no_target_mapping"
            )
            return {"success": False, "error": f"no simulator target configured for landmark {landmark_id!r}"}

        bridge.emit_counter("crash_test_target_verified", actor_id=actor_id, landmark_id=landmark_id)
        bridge.emit_counter("crash_test_armed", actor_id=actor_id, landmark_id=landmark_id)
        logger.info(
            "crash-test armed for actor=%s landmark=%s config=%s", actor_id, landmark_id, config.to_dict()
        )

        from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed

        target_x, target_y, target_z = target
        return await run_ros_action_if_governed(
            capability=self.name,
            resource=f"px4:{actor_id}" if actor_id else "px4",
            parameters={
                "landmark_id": landmark_id,
                "target_x": target_x,
                "target_y": target_y,
                "target_z": target_z,
                "max_velocity": config.max_velocity,
                "acceleration": config.acceleration,
                "target_distance": config.target_distance,
                "collision_radius": config.collision_radius,
            },
            adapter=adapter,
            actor_id=actor_id,
        )
