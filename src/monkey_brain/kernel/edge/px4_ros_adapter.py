"""PX4 ROS 2 execution adapter.

This module is intentionally optional: importing CognitiveOS does not require
ROS 2.  Construction requires a ROS 2 environment containing ``px4_msgs``.
All calls still enter through ``run_ros_action_if_governed``.

Anomaly fix (mission_bag_0.mcap review): the previous implementation
published a short burst of setpoints only during Takeoff, then one setpoint
per Waypoint call with nothing in between -- PX4 requires OffboardControlMode
+ TrajectorySetpoint to be streamed continuously (PX4 firmware falls back
out of OFFBOARD after ~500ms without a fresh setpoint) both BEFORE the mode
switch is requested and for the ENTIRE time the vehicle is expected to hold
OFFBOARD, including across the arm attempt. The previous adapter also
reported success immediately after publishing a command, never checking
whether PX4 actually accepted it -- vehicle_status showed arming_state stuck
at DISARMED for the whole recorded flight despite every step reporting
success:True. This version starts a background thread that streams the
current target setpoint at 10 Hz from Arm through Land, and confirms the
actual outcome (armed / altitude reached / position reached / disarmed
after landing) via subscriptions to vehicle_status and
vehicle_local_position before returning success -- "success" now means PX4
told us so, not that a ROS message was published.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from src.introspection.otel_bridge import get_bridge

from .ros_integration import RosUnavailableError

if TYPE_CHECKING:
    from .drone_state import DroneState


def _timeout(name: str, default: float) -> float:
    """Step timeout in seconds, overridable as PX4_<NAME>_TIMEOUT_S.

    These are wall-clock deadlines on simulated motion, so they are really a
    statement about how fast the simulator runs, not about the vehicle. A
    heavy Isaac scene, a GPU shared with something else, or simply a longer
    leg all stretch the same flight past a deadline that was fine before,
    and the failure looks like "did not reach waypoint" rather than
    "the sim is slow". Hence generous defaults plus an override.
    """
    raw = os.environ.get(f"PX4_{name}_TIMEOUT_S")
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_STREAM_HZ = 10.0
_PRE_OFFBOARD_STREAM_S = 1.5  # PX4 needs setpoints already streaming before it accepts OFFBOARD


def _tolerance(name: str, default: float) -> float:
    """Arrival tolerance in metres, overridable as PX4_<NAME>_TOLERANCE_M.

    These are judged against PX4's *estimate*, so they are really a budget
    for EKF drift, not for the controller. Measured here: four drones flew
    out to (8,0) inside 0.5m, were commanded home, and all four settled
    ~1.2m from the origin in the same direction -- a systematic bias, not
    four independent failures. Drift accumulates over a flight and shows up
    on the return leg because "home" IS the EKF origin, the one waypoint
    with no slack. The vehicles arrive; the tolerance is simply tighter than
    the drift. Defaults are unchanged so production behaviour is untouched;
    the sim bridge launcher relaxes them explicitly.
    """
    raw = os.environ.get(f"PX4_{name}_TOLERANCE_M")
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_ALTITUDE_TOLERANCE_M = _tolerance("ALTITUDE", 0.35)
_POSITION_TOLERANCE_M = _tolerance("POSITION", 0.5)
_ARM_TIMEOUT_S = _timeout("ARM", 15.0)
_TAKEOFF_TIMEOUT_S = _timeout("TAKEOFF", 60.0)
_WAYPOINT_TIMEOUT_S = _timeout("WAYPOINT", 90.0)
# observed: 9-33s from NAV_LAND to disarm in real SITL; a descent from
# altitude in a slow scene takes considerably longer than that.
_LAND_TIMEOUT_S = _timeout("LAND", 120.0)
# Hard backstop on the crash-test kinematic loop, independent of its own
# computed target_distance/max_velocity duration -- guarantees boundedness
# even if telemetry stalls or a misconfigured profile would otherwise loop
# indefinitely. Generous default; override like every other PX4_*_TIMEOUT_S.
_CRASHTEST_TIMEOUT_S = _timeout("CRASHTEST", 60.0)
_CRASHTEST_CONTROL_HZ = 10.0


def run_crash_test_kinematics(
    *,
    start: tuple[float, float, float],
    target: tuple[float, float, float],
    max_velocity: float,
    acceleration: float,
    target_distance: float,
    collision_radius: float,
    get_position: Any,
    set_target: Any,
    control_hz: float = _CRASHTEST_CONTROL_HZ,
    timeout_s: float = _CRASHTEST_TIMEOUT_S,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
) -> dict[str, Any]:
    """Bounded, deterministic straight-line kinematics for the simulator-
    only crash-test behavior -- a pure function taking `get_position`
    (returns real telemetry position, or None) and `set_target` (advances
    the EXISTING position-setpoint stream, kernel/edge/px4_ros_adapter.py::
    Px4RosExecutionAdapter._set_target) as plain callables, so it needs no
    live ROS 2/rclpy environment to construct or unit-test (rclpy is not
    pip-installable, see this module's own docstring).

    Ramps a scalar speed from 0 up to `max_velocity` at `acceleration`
    m/s^2, advances the streamed setpoint along the straight line from
    `start` toward `target` each control tick, and checks REAL position
    (never the setpoint itself) for distance-to-target each tick. Stops
    the moment real position is within `collision_radius` of `target`
    (collision), once `target_distance` of travel is exhausted without
    impact, or after `timeout_s` regardless (hard backstop independent of
    the computed profile) -- always returns, never loops unboundedly.

    Returns {"collided": bool, "traveled": float}.
    """
    start_x, start_y, start_z = start
    target_x, target_y, target_z = target
    dx, dy, dz = target_x - start_x, target_y - start_y, target_z - start_z
    distance_to_target = (dx * dx + dy * dy + dz * dz) ** 0.5
    if distance_to_target < 1e-6:
        ux, uy, uz = 0.0, 0.0, 0.0
    else:
        ux, uy, uz = dx / distance_to_target, dy / distance_to_target, dz / distance_to_target

    period = 1.0 / control_hz
    speed = 0.0
    traveled = 0.0
    deadline = monotonic() + timeout_s
    collided = False
    while monotonic() < deadline:
        speed = min(max_velocity, speed + acceleration * period)
        traveled = min(target_distance, traveled + speed * period)
        set_target(start_x + ux * traveled, start_y + uy * traveled, start_z + uz * traveled)

        live_pos = get_position()
        if live_pos is not None:
            rdx, rdy, rdz = live_pos[0] - target_x, live_pos[1] - target_y, live_pos[2] - target_z
            if (rdx * rdx + rdy * rdy + rdz * rdz) ** 0.5 <= collision_radius:
                collided = True
                break
        if traveled >= target_distance:
            break
        sleep(period)

    return {"collided": collided, "traveled": traveled}


def _yaw_degrees_from_attitude(msg: Any) -> float | None:
    """Heading in degrees from px4_msgs/VehicleAttitude's quaternion field
    `q` ([w, x, y, z], PX4's documented component order). Standard
    quaternion-to-yaw formula (atan2(2(wz+xy), 1-2(y^2+z^2))) — returns
    None rather than raising if `q` isn't the expected shape, same
    defensive posture as every other new field in latest_state()."""
    try:
        q = msg.q
        w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])
    except Exception:
        return None
    yaw_rad = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return math.degrees(yaw_rad) % 360.0


class Px4RosExecutionAdapter:
    """Actor-bound PX4 offboard adapter using the PX4 ROS 2 topic contract."""

    def __init__(self, *, actor_id: str, namespace: str) -> None:
        if not actor_id or not namespace:
            raise ValueError("actor_id and namespace are required")
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
            from px4_msgs.msg import (
                OffboardControlMode,
                TrajectorySetpoint,
                VehicleCommand,
                VehicleLocalPosition,
                VehicleStatus,
            )
        except ImportError as exc:
            raise RosUnavailableError("Px4RosExecutionAdapter requires ROS 2 rclpy and px4_msgs") from exc

        # Enrichment telemetry (battery/GPS/attitude) — optional, unlike the
        # import above: these aren't needed for flight control itself, only
        # for DroneState's battery/gps_state/heading fields (kernel/edge/
        # drone_state.py). SensorGps + its latitude_deg/longitude_deg/
        # altitude_msl_m fields and the vehicle_gps_position topic name are
        # confirmed from this repo's own
        # deploy/k8s/px4-sim-deployment.yaml (the Foxglove GPS bridge
        # ConfigMap already subscribes to exactly this). BatteryStatus and
        # VehicleAttitude are standard, long-stable PX4 uORB->ROS2
        # messages, but unlike SensorGps there is no other code in this
        # repo already exercising them to confirm their field names against
        # a real running px4_msgs build (none was available while writing
        # this) -- every access below is defensive (getattr with a None
        # default) specifically so a wrong guess here degrades to a missing
        # field, never a crash.
        try:
            from px4_msgs.msg import BatteryStatus, SensorGps, VehicleAttitude
        except ImportError:
            BatteryStatus = None  # noqa: N806
            SensorGps = None  # noqa: N806
            VehicleAttitude = None  # noqa: N806

        self.actor_id = actor_id
        self.namespace = namespace.strip("/")
        self._rclpy = rclpy
        self._OffboardControlMode = OffboardControlMode
        self._TrajectorySetpoint = TrajectorySetpoint
        self._VehicleCommand = VehicleCommand
        self._VehicleStatus = VehicleStatus
        self._context = rclpy.Context()
        rclpy.init(context=self._context)
        self._node = Node(f"cognitiveos_px4_{actor_id.replace('-', '_')}", context=self._context)
        # Actor Cell binding: this node lives on its own, non-default
        # rclpy.Context -- the bare module-level rclpy.spin_once() manages
        # a GLOBAL default executor/context pair and cannot spin a node
        # bound to a different context (confirmed live: TypeError inside
        # rclpy's own GuardCondition construction). Each adapter owns its
        # own SingleThreadedExecutor, bound to the SAME context as its
        # node, so spinning never touches any other actor's context.
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        prefix_in = f"/{self.namespace}/fmu/in"
        prefix_out = f"/{self.namespace}/fmu/out"
        self._commands = self._node.create_publisher(VehicleCommand, f"{prefix_in}/vehicle_command", 10)
        self._offboard = self._node.create_publisher(OffboardControlMode, f"{prefix_in}/offboard_control_mode", 10)
        self._setpoints = self._node.create_publisher(TrajectorySetpoint, f"{prefix_in}/trajectory_setpoint", 10)

        # PX4 publishes its own telemetry BEST_EFFORT/VOLATILE -- a RELIABLE
        # subscription (rclpy's default) is QoS-incompatible and silently
        # receives nothing. Match PX4's profile exactly.
        px4_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self._latest_status: Any = None
        self._latest_local_position: Any = None
        self._latest_battery: Any = None
        self._latest_gps: Any = None
        self._latest_attitude: Any = None
        # Receipt time (wall clock, this process), NOT last-read time --
        # latest_state()'s freshness check needs to know when telemetry
        # actually arrived, which these record; time.time() at the point
        # latest_state() is CALLED would make every call report itself as
        # fresh regardless of whether _on_status/_on_local_position have
        # fired recently, defeating the whole staleness check.
        self._latest_status_at: float = 0.0
        self._latest_local_position_at: float = 0.0
        # Overwritten from vehicle_status as soon as the first one arrives;
        # 1/1 is right for a single vehicle on PX4 instance 0.
        self._target_system = 1
        self._target_component = 1
        # Subscribe to every versioned vehicle_status topic rather than one.
        # PX4 renames these across releases and only publishes the version it
        # was built with: on this v1.17.0-alpha1 tree, vehicle_status_v1 has
        # a publisher and vehicle_status_v4 has none. Pinning v4 alone left
        # _latest_status permanently None, so every Arm failed with
        # "PX4 did not report ARMED ... (last arming_state=None)" while
        # telemetry was in fact flowing the whole time. Subscribing to both
        # costs nothing -- the absent one simply never fires -- and keeps
        # this working across PX4 versions instead of tracking renames.
        for _status_topic in ("vehicle_status_v1", "vehicle_status_v4"):
            self._node.create_subscription(
                VehicleStatus,
                f"{prefix_out}/{_status_topic}",
                self._on_status,
                px4_qos,
            )
        self._node.create_subscription(
            VehicleLocalPosition,
            f"{prefix_out}/vehicle_local_position_v1",
            self._on_local_position,
            px4_qos,
        )
        # Enrichment telemetry — each guarded independently so a missing
        # message TYPE (px4_msgs built without it) or a missing PUBLISHER
        # (PX4 built without that sensor/estimator) degrades that one
        # field, never the adapter as a whole.
        if BatteryStatus is not None:
            self._node.create_subscription(
                BatteryStatus,
                f"{prefix_out}/battery_status",
                self._on_battery,
                px4_qos,
            )
        if SensorGps is not None:
            self._node.create_subscription(
                SensorGps,
                f"{prefix_out}/vehicle_gps_position",
                self._on_gps,
                px4_qos,
            )
        if VehicleAttitude is not None:
            self._node.create_subscription(
                VehicleAttitude,
                f"{prefix_out}/vehicle_attitude",
                self._on_attitude,
                px4_qos,
            )

        # Continuous setpoint streaming: PX4 requires OffboardControlMode +
        # TrajectorySetpoint at >=2Hz (we use 10Hz) both before the OFFBOARD
        # mode switch is requested and without interruption for as long as
        # OFFBOARD is expected to hold, or it silently falls back out of the
        # mode (and refuses to arm into it at all). One background thread
        # owns all spinning and all setpoint publishing for this adapter --
        # invoke() never spins directly, it only reads _latest_* and swaps
        # the target the stream thread is already publishing.
        self._target = (0.0, 0.0, 0.0)
        self._target_lock = threading.Lock()
        self._stream_stop = threading.Event()
        self._publish_setpoints = threading.Event()
        self._publish_setpoints.set()
        self._stream_thread: threading.Thread | None = None

        # Simulator-only crash-test support (kernel/domains/robot.py::
        # CrashTestCapability). is_simulation is hardcoded True: this is the
        # ONLY backend implemented in this codebase today (Gazebo SITL via
        # ROS 2/px4_msgs) -- there is no real-hardware adapter class. A
        # future real-hardware adapter MUST set this to False; both
        # CrashTestCapability and run_ros_action_if_governed (ros_integration.py)
        # refuse to run a crash-test unless this is True, and this is one of
        # several independent gates (see docs on those two call sites), not
        # the only one -- do not rely on this attribute alone.
        self.is_simulation: bool = True
        self._collision_event: dict[str, Any] | None = None
        self._disabled: bool = False

    def _on_status(self, msg: Any) -> None:
        self._latest_status = msg
        self._latest_status_at = time.time()
        # Learn this vehicle's MAVLink ids instead of assuming system 1.
        # rcS sets MAV_SYS_ID = px4_instance + 1, so a second vehicle on
        # instance 2 answers to system 3; commands addressed to system 1
        # are silently ignored by every instance except 0. That failure is
        # invisible -- PX4 raises no preflight complaint because it never
        # accepted a command at all, and Arm just times out with
        # arming_state stuck at DISARMED.
        sys_id = getattr(msg, "system_id", 0) or 0
        comp_id = getattr(msg, "component_id", 0) or 0
        if sys_id:
            self._target_system = int(sys_id)
        if comp_id:
            self._target_component = int(comp_id)

    def _on_local_position(self, msg: Any) -> None:
        self._latest_local_position = msg
        self._latest_local_position_at = time.time()

    def _on_battery(self, msg: Any) -> None:
        self._latest_battery = msg

    def _on_gps(self, msg: Any) -> None:
        self._latest_gps = msg

    def _on_attitude(self, msg: Any) -> None:
        self._latest_attitude = msg

    def _set_target(self, x: float, y: float, z: float) -> None:
        with self._target_lock:
            self._target = (x, y, z)

    def _start_streaming(self) -> None:
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        self._stream_stop.clear()
        self._publish_setpoints.set()
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()

    def _stream_loop(self) -> None:
        # This thread is the ONLY thing that calls spin_once() for this
        # adapter -- it must keep running (spinning) for as long as we need
        # fresh vehicle_status/vehicle_local_position, even once we no
        # longer want it PUBLISHING setpoints (e.g. during Land, where
        # continuing to hold an OFFBOARD position setpoint fights PX4's own
        # AUTO_LAND control law). _publish_setpoints gates the publish side
        # only; spinning (and therefore telemetry) never stops until
        # _stop_streaming() is called.
        period = 1.0 / _STREAM_HZ
        while not self._stream_stop.is_set():
            if self._publish_setpoints.is_set():
                with self._target_lock:
                    x, y, z = self._target
                self._position_setpoint(x, y, z)
            self._executor.spin_once(timeout_sec=0.0)
            self._stream_stop.wait(period)

    def _pause_setpoint_publishing(self) -> None:
        self._publish_setpoints.clear()

    def _stop_streaming(self) -> None:
        self._stream_stop.set()
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=2.0)
        self._stream_thread = None

    def _wait(self, predicate, timeout_s: float, poll_s: float = 0.1) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(poll_s)
        return predicate()

    def _command(self, command: int, *, p1: float = 0.0, p2: float = 0.0) -> None:
        msg = self._VehicleCommand()
        msg.command = command
        msg.param1 = p1
        msg.param2 = p2
        msg.target_system = self._target_system
        msg.target_component = self._target_component
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        msg.timestamp = int(time.time() * 1_000_000)
        self._commands.publish(msg)

    def _position_setpoint(self, x: float, y: float, z: float) -> None:
        mode = self._OffboardControlMode()
        mode.position = True
        mode.velocity = False
        mode.acceleration = False
        mode.attitude = False
        mode.body_rate = False
        mode.timestamp = int(time.time() * 1_000_000)
        point = self._TrajectorySetpoint()
        point.position = [float(x), float(y), float(z)]
        point.yaw = 0.0
        point.timestamp = mode.timestamp
        self._offboard.publish(mode)
        self._setpoints.publish(point)

    def _is_armed(self) -> bool:
        status = self._latest_status
        armed_value = getattr(self._VehicleStatus, "ARMING_STATE_ARMED", 2)
        return status is not None and status.arming_state == armed_value

    def _is_disarmed(self) -> bool:
        status = self._latest_status
        disarmed_value = getattr(self._VehicleStatus, "ARMING_STATE_DISARMED", 1)
        return status is not None and status.arming_state == disarmed_value

    def _altitude_reached(self, target_z: float) -> bool:
        pos = self._latest_local_position
        return pos is not None and abs(pos.z - target_z) <= _ALTITUDE_TOLERANCE_M

    def _position_reached(self, target_x: float, target_y: float, target_z: float) -> bool:
        pos = self._latest_local_position
        if pos is None:
            return False
        dx, dy, dz = pos.x - target_x, pos.y - target_y, pos.z - target_z
        return (dx * dx + dy * dy) ** 0.5 <= _POSITION_TOLERANCE_M and abs(dz) <= _ALTITUDE_TOLERANCE_M

    def latest_state(self) -> DroneState | None:
        """A typed snapshot of this adapter's telemetry. armed/position come
        from vehicle_status/vehicle_local_position, the same subscriptions
        Arm/Takeoff/Waypoint/Land already rely on for their own outcome
        confirmation. battery/gps_state/heading/flight_mode come from the
        enrichment subscriptions above (battery_status/vehicle_gps_position/
        vehicle_attitude/nav_state) — every access is via getattr with a
        None default specifically so an unexpected px4_msgs field shape
        degrades that one field to None rather than raising (see this
        class's __init__ for which of these field names are confirmed
        in-repo vs. standard-PX4-convention-but-unverified-here).

        Returns None until the first vehicle_status message has actually
        arrived (not "no drone," just "no telemetry yet") — matches
        _is_armed()/_is_disarmed()'s own None-until-first-message contract.
        """
        from .drone_state import DroneState

        status = self._latest_status
        if status is None:
            return None
        pos = self._latest_local_position
        armed_value = getattr(self._VehicleStatus, "ARMING_STATE_ARMED", 2)
        # The older of the two receipt times, not the newer -- if position
        # telemetry stalls while status keeps flowing (or vice versa),
        # staleness must reflect the piece that actually went quiet, not
        # be masked by whichever topic happens to still be healthy.
        receipt_times = [self._latest_status_at]
        if pos is not None:
            receipt_times.append(self._latest_local_position_at)

        battery = self._latest_battery
        battery_remaining = getattr(battery, "remaining", None) if battery is not None else None

        gps = self._latest_gps
        gps_fix_type = getattr(gps, "fix_type", None) if gps is not None else None
        gps_state = str(gps_fix_type) if gps_fix_type is not None else None

        nav_state = getattr(status, "nav_state", None)
        flight_mode = str(nav_state) if nav_state is not None else None

        # PX4's own onboard clock (microseconds since boot, per every
        # px4_msgs header) -- NOT wall-clock epoch time. This is the
        # simulation-time signal Section 13 asks to keep separate from
        # `timestamp` (this process's own wall clock, set above from
        # receipt time) and from Trusted Time (kernel/trusted_time.py,
        # a different concern entirely -- see this repo's
        # docs/DRONE_SIMULATION.md).
        px4_timestamp_us = getattr(status, "timestamp", None)
        sim_timestamp = (px4_timestamp_us / 1_000_000.0) if px4_timestamp_us is not None else None

        heading = _yaw_degrees_from_attitude(self._latest_attitude)

        return DroneState(
            actor_id=self.actor_id,
            namespace=self.namespace,
            armed=(status.arming_state == armed_value),
            position_x=pos.x if pos is not None else None,
            position_y=pos.y if pos is not None else None,
            position_z=pos.z if pos is not None else None,
            timestamp=min(receipt_times),
            heading=heading,
            battery=battery_remaining,
            flight_mode=flight_mode,
            gps_state=gps_state,
            sim_timestamp=sim_timestamp,
            collision_event=self._collision_event,
            disabled=self._disabled,
        )

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Execute one small PX4 action; callers must govern this invocation.

        Every branch below confirms the outcome via subscribed telemetry
        before returning success:True -- publishing a command is necessary
        but not sufficient (see module docstring for the anomaly this
        replaced).
        """
        loop = asyncio.get_running_loop()
        result = dict(actor_id=self.actor_id, namespace=self.namespace)

        def publish() -> dict[str, Any]:
            if self._disabled:
                # A crash-test collision already disabled this simulated
                # vehicle -- no further command of ANY kind is accepted,
                # matching "mark the simulated vehicle as crashed/disabled"
                # (crash-test cannot be bypassed by going around it a second
                # time with a different capability either).
                return {**result, "success": False, "error": "vehicle disabled after crash-test collision"}

            if capability == "Arm":
                # Hold the current position (0,0,0 relative to arm point) so
                # a real setpoint stream is already flowing before OFFBOARD
                # is requested -- PX4 rejects the mode switch otherwise.
                self._set_target(0.0, 0.0, 0.0)
                self._start_streaming()
                time.sleep(_PRE_OFFBOARD_STREAM_S)
                self._command(self._VehicleCommand.VEHICLE_CMD_DO_SET_MODE, p1=1.0, p2=6.0)
                self._command(self._VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, p1=1.0)
                armed = self._wait(self._is_armed, _ARM_TIMEOUT_S)
                if not armed:
                    status = self._latest_status
                    self._stop_streaming()
                    return {
                        **result,
                        "success": False,
                        "error": f"PX4 did not report ARMED within {_ARM_TIMEOUT_S}s "
                        f"(last arming_state={getattr(status, 'arming_state', None)})",
                    }
                return {**result, "success": True}

            elif capability == "Takeoff":
                height_m = float(parameters.get("height_m", 2.0))
                target_z = -height_m
                self._set_target(0.0, 0.0, target_z)
                reached = self._wait(lambda: self._altitude_reached(target_z), _TAKEOFF_TIMEOUT_S)
                if not reached:
                    pos = self._latest_local_position
                    return {
                        **result,
                        "success": False,
                        "error": f"did not reach takeoff altitude {height_m}m within {_TAKEOFF_TIMEOUT_S}s "
                        f"(last z={getattr(pos, 'z', None)})",
                    }
                return {**result, "success": True, "altitude_m": height_m}

            elif capability == "Waypoint":
                x = float(parameters.get("x", 0.0))
                y = float(parameters.get("y", 0.0))
                target_z = -float(parameters.get("height_m", 2.0))
                self._set_target(x, y, target_z)
                reached = self._wait(lambda: self._position_reached(x, y, target_z), _WAYPOINT_TIMEOUT_S)
                if not reached:
                    pos = self._latest_local_position
                    return {
                        **result,
                        "success": False,
                        "error": f"did not reach waypoint ({x},{y}) within {_WAYPOINT_TIMEOUT_S}s "
                        f"(last position=({getattr(pos, 'x', None)},{getattr(pos, 'y', None)}))",
                    }
                return {**result, "success": True, "x": x, "y": y}

            elif capability == "Land":
                self._command(self._VehicleCommand.VEHICLE_CMD_NAV_LAND)
                # Stop competing with PX4's own AUTO_LAND control law: once
                # landing is commanded, continuing to publish an OFFBOARD
                # position setpoint only pressures the vehicle to hold that
                # position instead of descending on its own. Spinning must
                # keep running though -- it's this adapter's only source of
                # fresh vehicle_status, and the wait below depends on it.
                self._pause_setpoint_publishing()
                disarmed = self._wait(self._is_disarmed, _LAND_TIMEOUT_S)
                self._stop_streaming()
                if not disarmed:
                    status = self._latest_status
                    return {
                        **result,
                        "success": False,
                        "error": f"PX4 did not disarm after landing within {_LAND_TIMEOUT_S}s "
                        f"(last arming_state={getattr(status, 'arming_state', None)})",
                    }
                return {**result, "success": True}

            elif capability == "CrashTest":
                # SIMULATOR-ONLY. Reached only through CrashTestCapability's
                # own pre-governance checks + run_ros_action_if_governed's
                # kernel-computed simulation_only/crash_test_mode/is_simulation
                # signals + the OPA crash_test_unsafe deny rule -- this branch
                # itself adds no new gate, it trusts the caller was already
                # verified by all of that (same posture Arm/Takeoff/Waypoint/
                # Land already take toward their own governed callers).
                #
                # Bounded, deterministic kinematics reusing the EXISTING
                # _set_target/_stream_loop position-setpoint machinery (no
                # second control system) -- the actual ramp/straight-line/
                # collision-check logic lives in the standalone
                # run_crash_test_kinematics() above (pure function, no
                # rclpy dependency, directly unit-testable).
                target_x = float(parameters.get("target_x", 0.0))
                target_y = float(parameters.get("target_y", 0.0))
                target_z = float(parameters.get("target_z", 0.0))
                max_velocity = max(0.1, float(parameters.get("max_velocity", 3.0)))
                acceleration = max(0.1, float(parameters.get("acceleration", 1.0)))
                target_distance = max(0.1, float(parameters.get("target_distance", 15.0)))
                collision_radius = max(0.1, float(parameters.get("collision_radius", 2.0)))
                landmark_id = str(parameters.get("landmark_id", ""))

                self._start_streaming()
                pos = self._latest_local_position
                start = (pos.x, pos.y, pos.z) if pos is not None else (0.0, 0.0, 0.0)

                def _get_position() -> tuple[float, float, float] | None:
                    live_pos = self._latest_local_position
                    return (live_pos.x, live_pos.y, live_pos.z) if live_pos is not None else None

                # One span for the whole bounded kinematic loop, not one per
                # tick -- matches "do not generate spans for every
                # simulation tick." crash_test_started/completed are this
                # span's own start/end; crash_test_collision is a separate
                # counter fired once, only on actual impact.
                with get_bridge().span(
                    "crash_test_started",
                    layer="action",
                    actor_id=self.actor_id,
                    landmark_id=landmark_id,
                ):
                    outcome = run_crash_test_kinematics(
                        start=start,
                        target=(target_x, target_y, target_z),
                        max_velocity=max_velocity,
                        acceleration=acceleration,
                        target_distance=target_distance,
                        collision_radius=collision_radius,
                        get_position=_get_position,
                        set_target=self._set_target,
                    )
                    collided = outcome["collided"]

                    get_bridge().emit_counter(
                        "crash_test_collision" if collided else "crash_test_completed",
                        actor_id=self.actor_id,
                        landmark_id=landmark_id,
                    )

                if collided:
                    self._collision_event = {
                        "target_landmark": landmark_id,
                        "simulation_only": True,
                        "crash_test": True,
                    }
                    self._disabled = True
                    # Stop competing with the simulated impact -- same
                    # "stop pushing an OFFBOARD setpoint" posture Land
                    # already takes once its own terminal condition fires.
                    self._pause_setpoint_publishing()
                    return {**result, "success": True, "collision": True, "landmark_id": landmark_id}

                return {
                    **result,
                    "success": False,
                    "error": f"crash-test did not reach collision_radius={collision_radius}m of target "
                    f"within target_distance={target_distance}m / timeout={_CRASHTEST_TIMEOUT_S}s",
                }

            else:
                return {**result, "success": False, "error": f"unsupported PX4 capability: {capability}"}

        return await loop.run_in_executor(None, publish)

    def shutdown(self) -> None:
        self._stop_streaming()
        self._executor.shutdown()
        self._node.destroy_node()
        if self._context.ok():
            self._context.try_shutdown()


def build_px4_ros_execution_adapter(*, actor_id: str, namespace: str) -> Px4RosExecutionAdapter:
    """Construct a real PX4 adapter; never silently fall back to a fake one."""
    return Px4RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
