"""Nav2 ROS 2 execution adapter — the ground-robot analog of
kernel/edge/px4_ros_adapter.py::Px4RosExecutionAdapter, reached through the
SAME kernel/edge/ros_integration.py::run_ros_action_if_governed entry point
and the same "success means the system told us so" discipline: NavigateToPose
confirms success via Nav2's own `navigate_to_pose` action result (a stock
part of every nav2_bringup launch), never by publish-and-hope.

Simpler than Px4RosExecutionAdapter on purpose: PX4's OFFBOARD mode requires
a continuously-streamed setpoint even while nothing is happening (hence that
adapter's always-on background spin/stream threads). Nav2's action server
already owns its own closed-loop control between goal-accepted and
goal-result -- this adapter only needs to spin while it is actively waiting
on a future, so it does that inline, synchronously, with no background
thread and no risk of two threads spinning the same executor at once.

Honest scope (v1, first ground-robot pass): exactly two capabilities,
NavigateToPose and Stop -- the minimum needed to drive a robot to a pose and
cancel it. No telemetry fusion into belief state yet: latest_state() always
returns None (see its own docstring) -- real follow-up work, not a stub
pretending to be done, matching kernel/edge/drone_state.py's own "field this
repo doesn't populate yet stays None, never fabricated" precedent. Never
exercised against a real ROS 2 runtime in this codebase's test suite (same
honest caveat kernel/edge/px4_ros_adapter.py's own docstring states) -- that
requires an actual ROS 2 installation and a Nav2-backed simulator or robot,
which this development/CI environment does not have.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
from typing import Any

from .ros_integration import RosUnavailableError


def _timeout(name: str, default: float) -> float:
    """Step timeout in seconds, overridable as NAV2_<NAME>_TIMEOUT_S --
    same override convention as px4_ros_adapter.py's own _timeout()."""
    raw = os.environ.get(f"NAV2_{name}_TIMEOUT_S")
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


_SERVER_WAIT_TIMEOUT_S = _timeout("SERVER_WAIT", 30.0)
_NAVIGATE_TIMEOUT_S = _timeout("NAVIGATE", 120.0)
_CANCEL_TIMEOUT_S = _timeout("CANCEL", 10.0)

_MAX_DISTANCE_M = 100.0  # sanity bound, not a certified operating envelope -- this is a sim demo


class Nav2RosExecutionAdapter:
    """Actor-bound adapter over Nav2's stock `navigate_to_pose` action
    (nav2_msgs/action/NavigateToPose)."""

    def __init__(self, *, actor_id: str, namespace: str = "") -> None:
        if not actor_id:
            raise ValueError("actor_id is required")
        try:
            import rclpy
            from rclpy.action import ActionClient
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
            from nav2_msgs.action import NavigateToPose
        except ImportError as exc:
            raise RosUnavailableError("Nav2RosExecutionAdapter requires ROS 2 rclpy and nav2_msgs") from exc

        self.actor_id = actor_id
        self.namespace = namespace.strip("/")
        self._rclpy = rclpy
        self._NavigateToPose = NavigateToPose
        # Deliberately the DEFAULT global context/executor here, NOT a
        # per-adapter rclpy.Context() the way px4_ros_adapter.py's
        # Px4RosExecutionAdapter uses -- that isolation exists there because
        # a single process can host multiple co-resident drone actors. This
        # container (docker/Dockerfile.ros-bridge-nav2, kernel/edge/
        # nav2_bridge_server.py) always constructs exactly one adapter per
        # process, so there is nothing to isolate FROM, and the default
        # context lets rclpy.spin_until_future_complete() below use its own
        # executor= argument without also having to carry a custom context
        # through every call (confirmed live: rclpy's free-function
        # spin_until_future_complete() defaults to the GLOBAL executor
        # when executor=None, which cannot add a node from a non-default
        # context -- the exact TypeError px4_ros_adapter.py's own __init__
        # comment already documents hitting for that reason).
        if not rclpy.ok():
            rclpy.init()
        node_name = f"cognitiveos_nav2_{actor_id.replace('-', '_')}"
        self._node = Node(node_name)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        action_name = f"/{self.namespace}/navigate_to_pose" if self.namespace else "/navigate_to_pose"
        # No context= kwarg here -- unlike Node(), ActionClient.__init__()
        # doesn't accept one (confirmed live: TypeError). It derives its
        # context from the node passed in.
        self._client = ActionClient(self._node, NavigateToPose, action_name)

        # Same posture as Px4RosExecutionAdapter.is_simulation: hardcoded
        # True because this is the only backend implemented today (Nav2 +
        # Gazebo SITL). A future real-hardware adapter must set this False.
        self.is_simulation: bool = True

        self._goal_lock = threading.Lock()
        self._current_goal_handle: Any = None

    def latest_state(self) -> None:
        """Telemetry fusion (position/nav-status into belief state, the
        ground-robot analog of DroneState) is deliberately not implemented
        in this first pass -- see module docstring. Always None, matching
        kernel/edge/drone_state.py's own "None means not populated, never
        fabricated" contract, so kernel/pipeline/observations.py's
        is_fresh(None) check safely skips it every tick rather than trying
        to read drone-shaped attributes off a differently-shaped object."""
        return None

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        result = dict(actor_id=self.actor_id, namespace=self.namespace)

        if capability == "NavigateToPose":
            x = float(parameters.get("x", 0.0))
            y = float(parameters.get("y", 0.0))
            yaw_deg = float(parameters.get("yaw_deg", 0.0))
            return await loop.run_in_executor(None, self._navigate, x, y, yaw_deg, result)
        if capability == "Stop":
            return await loop.run_in_executor(None, self._stop, result)
        return {**result, "success": False, "error": f"unsupported Nav2 capability: {capability}"}

    def _navigate(self, x: float, y: float, yaw_deg: float, result: dict[str, Any]) -> dict[str, Any]:
        if not self._client.wait_for_server(timeout_sec=_SERVER_WAIT_TIMEOUT_S):
            return {
                **result,
                "success": False,
                "error": f"navigate_to_pose action server not available within {_SERVER_WAIT_TIMEOUT_S}s",
            }

        goal = self._NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        yaw_rad = math.radians(yaw_deg)
        goal.pose.pose.orientation.z = math.sin(yaw_rad / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw_rad / 2.0)

        send_future = self._client.send_goal_async(goal)
        self._rclpy.spin_until_future_complete(
            self._node, send_future, executor=self._executor, timeout_sec=_NAVIGATE_TIMEOUT_S
        )
        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            return {**result, "success": False, "error": "NavigateToPose goal was rejected"}

        with self._goal_lock:
            self._current_goal_handle = goal_handle

        result_future = goal_handle.get_result_async()
        self._rclpy.spin_until_future_complete(
            self._node, result_future, executor=self._executor, timeout_sec=_NAVIGATE_TIMEOUT_S
        )

        with self._goal_lock:
            self._current_goal_handle = None

        wrapped = result_future.result()
        if wrapped is None:
            return {
                **result,
                "success": False,
                "error": f"NavigateToPose did not complete within {_NAVIGATE_TIMEOUT_S}s",
            }

        from action_msgs.msg import GoalStatus

        succeeded = wrapped.status == GoalStatus.STATUS_SUCCEEDED
        return {
            **result,
            "success": succeeded,
            "x": x,
            "y": y,
            "yaw_deg": yaw_deg,
            "status": wrapped.status,
            **({} if succeeded else {"error": f"Nav2 did not report SUCCEEDED (status={wrapped.status})"}),
        }

    def _stop(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._goal_lock:
            handle = self._current_goal_handle
        if handle is None:
            return {**result, "success": True, "note": "no active navigation goal"}
        cancel_future = handle.cancel_goal_async()
        self._rclpy.spin_until_future_complete(
            self._node, cancel_future, executor=self._executor, timeout_sec=_CANCEL_TIMEOUT_S
        )
        return {**result, "success": True}

    def shutdown(self) -> None:
        self._executor.shutdown()
        self._node.destroy_node()
        # Never rclpy.shutdown() here -- this is the shared DEFAULT global
        # context (see __init__'s own comment on why this adapter, unlike
        # Px4RosExecutionAdapter, doesn't own a private one), so tearing it
        # down here would be a process-wide action from a single object's
        # shutdown(), not scoped to just this adapter.


def build_nav2_ros_execution_adapter(*, actor_id: str, namespace: str = "") -> Nav2RosExecutionAdapter:
    """Construct a real Nav2 adapter; never silently fall back to a fake one."""
    return Nav2RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
