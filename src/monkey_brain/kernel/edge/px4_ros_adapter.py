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
import threading
import time
from typing import Any

from .ros_integration import RosUnavailableError

_STREAM_HZ = 10.0
_PRE_OFFBOARD_STREAM_S = 1.5  # PX4 needs setpoints already streaming before it accepts OFFBOARD
_ARM_TIMEOUT_S = 8.0
_ALTITUDE_TOLERANCE_M = 0.35
_POSITION_TOLERANCE_M = 0.5
_TAKEOFF_TIMEOUT_S = 20.0
_WAYPOINT_TIMEOUT_S = 25.0
_LAND_TIMEOUT_S = 45.0  # observed: 9-33s from NAV_LAND to disarm in real SITL, 45s gives margin


class Px4RosExecutionAdapter:
    """Actor-bound PX4 offboard adapter using the PX4 ROS 2 topic contract."""

    def __init__(self, *, actor_id: str, namespace: str) -> None:
        if not actor_id or not namespace:
            raise ValueError("actor_id and namespace are required")
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            from rclpy.qos import (
                DurabilityPolicy,
                HistoryPolicy,
                QoSProfile,
                ReliabilityPolicy,
            )
            from px4_msgs.msg import (
                OffboardControlMode,
                TrajectorySetpoint,
                VehicleCommand,
                VehicleLocalPosition,
                VehicleStatus,
            )
        except ImportError as exc:
            raise RosUnavailableError("Px4RosExecutionAdapter requires ROS 2 rclpy and px4_msgs") from exc

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
        self._node.create_subscription(
            VehicleStatus,
            f"{prefix_out}/vehicle_status_v4",
            self._on_status,
            px4_qos,
        )
        self._node.create_subscription(
            VehicleLocalPosition,
            f"{prefix_out}/vehicle_local_position_v1",
            self._on_local_position,
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

    def _on_status(self, msg: Any) -> None:
        self._latest_status = msg

    def _on_local_position(self, msg: Any) -> None:
        self._latest_local_position = msg

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
        msg.target_system = 1
        msg.target_component = 1
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

            else:
                return {
                    **result,
                    "success": False,
                    "error": f"unsupported PX4 capability: {capability}",
                }

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
