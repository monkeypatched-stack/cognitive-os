"""ROS integration point for robot actors.

Honest scope: this is an INTERFACE, not a ROS binding. No ROS runtime,
message broker, or hardware exists in this environment to bind against,
so this module defines the one contract a real ROS execution layer must
satisfy, and the one function that enforces where governance sits
relative to it — it does not itself talk to ROS.

The intended runtime path (Section 11):

    CognitiveOS Actor
          |
    Local Edge Store
          |
    Local Governance   <-- kernel/edge/local_governance.py
          |
    Local Negotiation  <-- kernel/edge/negotiation.py
          |
    Committed Plan
          |
    ROS execution layer   <-- THIS module's RosExecutionAdapter contract
          |
    Sensors / actuators
          |
    Outcome
          |
    Local state update
          |
    async synchronization with CognitiveOS  <-- kernel/edge/sync.py

The governance boundary is identical to every other capability path in
this codebase: a ROS-backed capability is registered on the SAME
CapabilityBus every other capability uses (kernel/domains/*.py) and
reached through the SAME ActionExecutor -> ensure_governed boundary
(locally via LocalGovernanceEvaluator, or centrally) — never a separate
"robot dispatch" path that skips it. `run_ros_action_if_governed` below
is the one place that enforces this: it NEVER calls the adapter directly;
`invoke()` is passed in as `ensure_governed`'s own `effect` callable, so
governance always runs first, exactly like any other capability.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("agentos.edge.ros_integration")


class RosUnavailableError(RuntimeError):
    """Raised only when a caller explicitly REQUIRES a real ROS adapter
    (a robot deployment's own startup path, require_real=True) and no ROS
    runtime is importable. The normal CognitiveOS runtime never raises
    this -- build_ros_execution_adapter()'s default falls back to
    FakeRosExecutionAdapter instead, exactly so that ROS not being
    installed can never crash a non-robot deployment."""


class RosExecutionAdapter(Protocol):
    """What a real ROS integration must implement. Deliberately minimal
    and transport-agnostic (rclpy topics/services/actions are all valid
    implementations) — this module does not prescribe ROS 1 vs ROS 2,
    or any specific message type."""

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Send a committed action to sensors/actuators and return the
        real outcome. Must not itself perform any authorization check —
        by the time this is called, governance has already run."""
        ...


async def run_ros_action_if_governed(
    *, capability: str, resource: str, parameters: dict[str, Any],
    adapter: RosExecutionAdapter, local_policy_decision: dict[str, Any] | None = None,
    verified_delegation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ONLY sanctioned entry point from a robot actor's committed
    plan into the ROS execution layer. Routes through the exact same
    ensure_governed boundary every other capability call uses
    (kernel/pipeline/action_executor.py's own force_authorize=True
    pattern) — a robot capability is never invoked directly, and this
    function has no other way to reach `adapter.invoke()` than through
    a successful governance decision.
    """
    from src.monkey_brain.kernel.security_boundary import ensure_governed

    async def _invoke() -> dict[str, Any]:
        return await adapter.invoke(capability=capability, parameters=parameters)

    return await ensure_governed(
        f"capability.{capability}", resource, _invoke,
        extra={"capability": capability, "parameters": parameters},
        force_authorize=True,
        local_policy_decision=local_policy_decision,
        verified_delegation=verified_delegation,
    )


class FakeRosExecutionAdapter:
    """In-memory RosExecutionAdapter for normal CI and unit tests -- no
    ROS runtime, no hardware. Records every invocation (for assertions)
    and returns a deterministic, honestly-labeled result; never claims to
    have moved a real actuator."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        call = {"capability": capability, "parameters": dict(parameters)}
        self.calls.append(call)
        return {"success": True, "simulated": True, "capability": capability}


class RclpyRosExecutionAdapter:
    """Production RosExecutionAdapter backed by a real ROS 2 node
    (rclpy). rclpy is imported lazily -- inside __init__, never at module
    level -- so importing this module (or this whole package) never
    requires ROS to be installed; only actually CONSTRUCTING this class
    does.

    Honest scope: this sends a real ROS 2 service call (the most common
    shape for a request/reply "do this discrete action" pattern; a topic-
    or action-based capability can be added the same way without
    changing this class's public contract) and returns its real response.
    It has never been exercised against a real ROS 2 runtime or physical
    hardware in this codebase's test suite -- that requires an actual ROS
    2 installation and a robot (or simulated) execution target, which
    this development/CI environment does not have. See
    tests/unit/test_ros_integration_contract.py's REQUIRES_ROS-gated
    section and docs/DIAGRAMS.md's ROS integration note.
    """

    def __init__(self, *, node_name: str = "cognitiveos_edge_actor", service_prefix: str = "/cognitiveos") -> None:
        try:
            import rclpy  # type: ignore[import-not-found]
            from rclpy.node import Node  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RosUnavailableError(
                "RclpyRosExecutionAdapter requires a ROS 2 installation providing the "
                "'rclpy' package (installed via a ROS 2 distribution, not pip) -- "
                f"import failed: {exc}",
            ) from exc

        self._rclpy = rclpy
        self._service_prefix = service_prefix
        if not rclpy.ok():
            rclpy.init()
        self._node = Node(node_name)
        self._clients: dict[str, Any] = {}

    def _client_for(self, capability: str) -> Any:
        if capability not in self._clients:
            # Deferred import -- already proven available in __init__.
            from std_srvs.srv import Trigger  # type: ignore[import-not-found]

            self._clients[capability] = self._node.create_client(
                Trigger, f"{self._service_prefix}/{capability}",
            )
        return self._clients[capability]

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        from std_srvs.srv import Trigger  # type: ignore[import-not-found]

        client = self._client_for(capability)
        if not client.wait_for_service(timeout_sec=5.0):
            return {"success": False, "error": f"ROS service {self._service_prefix}/{capability} not available"}

        request = Trigger.Request()
        future = client.call_async(request)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._rclpy.spin_until_future_complete, self._node, future)
        response = future.result()
        if response is None:
            return {"success": False, "error": f"ROS service {capability} call timed out or failed"}
        return {"success": bool(response.success), "message": response.message}

    def shutdown(self) -> None:
        self._node.destroy_node()


def build_ros_execution_adapter(*, require_real: bool = False) -> RosExecutionAdapter:
    """Clear, explicit startup behavior (Section 1's own requirement):

    - require_real=False (default -- the normal CognitiveOS runtime):
      NEVER crashes because ROS is not installed. Returns a
      FakeRosExecutionAdapter.
    - require_real=True (a robot/ROS deployment's own startup path,
      opted into explicitly): raises RosUnavailableError with an
      actionable message if rclpy cannot be imported, rather than
      silently degrading to a fake adapter that would make a robot
      deployment believe it is actually moving hardware when it is not.
    """
    if not require_real:
        return FakeRosExecutionAdapter()
    try:
        return RclpyRosExecutionAdapter()
    except RosUnavailableError:
        raise
    except Exception as exc:
        raise RosUnavailableError(f"failed to construct a real ROS execution adapter: {exc}") from exc
