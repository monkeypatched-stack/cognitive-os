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
import os
import re
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
    or any specific message type.

    Actor Cell binding (docs/ACTOR_CELL_ARCHITECTURE.md Section I/3): an
    adapter instance is constructed bound to one actor_id (see
    build_ros_execution_adapter below) and exposes it as `.actor_id` so
    run_ros_action_if_governed can assert the calling actor matches before
    ever reaching `invoke()` — today's one-actor-per-process deployment
    convention makes this redundant in practice, but nothing previously
    enforced it, so a future wiring mistake handing actor A's plan
    adapter B's instance would previously have gone undetected."""

    actor_id: str

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Send a committed action to sensors/actuators and return the
        real outcome. Must not itself perform any authorization check —
        by the time this is called, governance has already run."""
        ...


async def _invoke_idempotent(
    idempotency_key: str,
    capability: str,
    parameters: dict[str, Any],
    adapter: "RosExecutionAdapter",
) -> dict[str, Any]:
    """Physical-effect-level idempotency: a caller-supplied key that, if
    reused, replays the cached PHYSICAL result instead of calling
    adapter.invoke() again — closes the gap
    tests/validation/test_v13_ros_governance.py documents and deliberately
    leaves open (an identical command sent twice moved the vehicle twice).

    Deliberately does NOT skip governance — this wraps only the effect
    (adapter.invoke()), called from inside run_ros_action_if_governed's own
    `_invoke` closure, which still runs through ensure_governed on every
    call. Deduplication belongs at the effect boundary, not the
    authorization boundary: a retried command should still be re-authorized
    (permissions/policy may have changed since the first attempt), it just
    must not move the vehicle a second time if the first attempt already
    did.

    Reuses api/idempotency.py's IdempotencyStore/request_fingerprint
    directly (not the `@idempotent` HTTP route decorator, which is shaped
    around FastAPI's Request/Depends — this call site has neither).
    """
    from src.monkey_brain.api.idempotency import get_idempotency_store, request_fingerprint

    scoped_key = f"ros_action:{capability}:{idempotency_key}"
    request_hash = request_fingerprint("ROS", capability, parameters)
    store = get_idempotency_store()
    claimed, existing = store.reserve(scoped_key, request_hash)
    if not claimed:
        if existing is not None and existing.state == "completed":
            if existing.request_hash == request_hash:
                return existing.response_body or {}
            return {
                "success": False,
                "error": f"idempotency key {idempotency_key!r} was already used for a different {capability} request",
            }
        return {
            "success": False,
            "error": f"a request with idempotency key {idempotency_key!r} is already being processed",
        }
    try:
        result = await adapter.invoke(capability=capability, parameters=parameters)
    except BaseException:
        store.release(scoped_key)
        raise
    if isinstance(result, dict):
        store.complete(scoped_key, request_hash, result)
    else:
        store.release(scoped_key)
    return result


async def run_ros_action_if_governed(
    *,
    capability: str,
    resource: str,
    parameters: dict[str, Any],
    adapter: RosExecutionAdapter,
    actor_id: str = "",
    local_policy_decision: dict[str, Any] | None = None,
    verified_delegation: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """The ONLY sanctioned entry point from a robot actor's committed
    plan into the ROS execution layer. Routes through the exact same
    ensure_governed boundary every other capability call uses
    (kernel/pipeline/action_executor.py's own force_authorize=True
    pattern) — a robot capability is never invoked directly, and this
    function has no other way to reach `adapter.invoke()` than through
    a successful governance decision.

    actor_id: the actor this call is being made ON BEHALF OF. When both
    this and the adapter's own bound `.actor_id` are non-empty and they
    differ, this raises RosUnavailableError (fail closed) BEFORE
    governance even runs — Actor Cell ROS isolation (docs/
    ACTOR_CELL_ARCHITECTURE.md:139): actor A's plan must never reach
    actor B's ROS adapter. Omitting actor_id (the default, "") preserves
    prior behavior exactly for any existing caller/adapter that doesn't
    bind one yet.

    idempotency_key: optional, caller-supplied (e.g. a plan step id) —
    when given, a retried call with the same key replays the cached
    physical result instead of re-invoking the adapter (see
    _invoke_idempotent above). Omitting it (the default) preserves prior
    behavior exactly — every existing caller that doesn't pass one keeps
    executing every call, same as before this parameter existed.
    """
    bound_actor_id = getattr(adapter, "actor_id", "") or ""
    if actor_id and bound_actor_id and actor_id != bound_actor_id:
        raise RosUnavailableError(
            f"ROS adapter is bound to actor_id={bound_actor_id!r}, refusing to invoke it "
            f"on behalf of actor_id={actor_id!r} -- Actor Cell ROS isolation would be violated",
        )

    from src.monkey_brain.kernel.security_boundary import ensure_governed

    async def _invoke() -> dict[str, Any]:
        if idempotency_key:
            return await _invoke_idempotent(idempotency_key, capability, parameters, adapter)
        return await adapter.invoke(capability=capability, parameters=parameters)

    extra: dict[str, Any] = {
        "capability": capability,
        "parameters": parameters,
        "actor_id": actor_id,
    }
    if capability == "CrashTest":
        # Kernel-computed governance signals, deliberately NOT sourced from
        # the capability's own call (build_opa_input's own docstring:
        # "OPA input from kernel evidence only. Agent extra cannot set
        # auth."). This function is the ONE chokepoint every PX4 capability
        # passes through, so it is trusted kernel plumbing, not "agent
        # extra" -- it reads os.environ and the adapter's own hardcoded
        # is_simulation attribute itself, here, overriding anything a
        # caller might have tried to pass for these specific keys, rather
        # than re-trusting a claim the capability layer already made. Only
        # computed for capability=="CrashTest" -- zero overhead on the
        # Arm/Takeoff/Waypoint/Land hot path. opa/policies/
        # agentos_governance.rego's crash_test_unsafe rule reads these
        # under input.context.signals.*, the same sub-key
        # GovernanceEngine.evaluate() already uses for bias-audit signals.
        extra["signals"] = {
            "simulation_only": os.environ.get("SIMULATION_ONLY", "").strip().lower() == "true",
            "crash_test_mode": os.environ.get("CRASH_TEST_MODE", "").strip().lower() == "true",
            "is_simulation": bool(getattr(adapter, "is_simulation", False)),
        }

    return await ensure_governed(
        f"capability.{capability}",
        resource,
        _invoke,
        extra=extra,
        force_authorize=True,
        local_policy_decision=local_policy_decision,
        verified_delegation=verified_delegation,
    )


class FakeRosExecutionAdapter:
    """In-memory RosExecutionAdapter for normal CI and unit tests -- no
    ROS runtime, no hardware. Records every invocation (for assertions)
    and returns a deterministic, honestly-labeled result; never claims to
    have moved a real actuator."""

    def __init__(self, *, actor_id: str = "") -> None:
        self.actor_id = actor_id
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

    def __init__(
        self,
        *,
        actor_id: str = "",
        node_name: str | None = None,
        service_prefix: str | None = None,
    ) -> None:
        try:
            import rclpy  # type: ignore[import-not-found]
            from rclpy.node import Node  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RosUnavailableError(
                "RclpyRosExecutionAdapter requires a ROS 2 installation providing the "
                "'rclpy' package (installed via a ROS 2 distribution, not pip) -- "
                f"import failed: {exc}",
            ) from exc

        self.actor_id = actor_id
        # Actor Cell binding (docs/ACTOR_CELL_ARCHITECTURE.md Section I/3):
        # namespace the ROS node/service prefix by actor_id by default, so
        # two Cells never collide on the same ROS namespace even if they
        # ever ran co-resident -- an explicit node_name/service_prefix
        # still overrides this (e.g. a deployment with its own naming
        # scheme), preserving prior behavior for any existing caller.
        default_prefix = f"/cognitiveos/{actor_id}" if actor_id else "/cognitiveos"
        # ROS 2 node names must match ^[a-zA-Z][a-zA-Z0-9_]*$ -- unlike the
        # topic-shaped service_prefix above, a raw actor_id (e.g.
        # "actor-A", commonly hyphenated) is not a valid node name, so it's
        # sanitized here before being embedded.
        default_node_name = (
            f"cognitiveos_edge_actor_{re.sub(r'[^a-zA-Z0-9_]', '_', actor_id)}"
            if actor_id
            else "cognitiveos_edge_actor"
        )
        node_name = node_name if node_name is not None else default_node_name
        service_prefix = service_prefix if service_prefix is not None else default_prefix

        # Swarm-readiness audit finding (docs/ACTOR_CELL_ARCHITECTURE.md /
        # this session's audit): the prior implementation called the
        # process-global `rclpy.init()` once (guarded by `rclpy.ok()`) and
        # every adapter's Node implicitly used that one shared default
        # Context -- real namespace isolation (service_prefix/node_name)
        # but NOT runtime isolation: two co-resident adapters shared one
        # rclpy Context with no per-actor executor. Each adapter now gets
        # its own Context, so two Actor Cells co-resident in one process
        # never share ROS runtime state, only the one global `rclpy`
        # import (unavoidable -- it's a single C extension module, not
        # per-actor state) -- symmetric with shutdown() below, which tears
        # down only this adapter's own context, never any other actor's.
        self._rclpy = rclpy
        self._service_prefix = service_prefix
        self._context = rclpy.Context()
        rclpy.init(context=self._context)
        self._node = Node(node_name, context=self._context)
        self._clients: dict[str, Any] = {}

    def _client_for(self, capability: str) -> Any:
        if capability not in self._clients:
            # Deferred import -- already proven available in __init__.
            from std_srvs.srv import Trigger  # type: ignore[import-not-found]

            self._clients[capability] = self._node.create_client(
                Trigger,
                f"{self._service_prefix}/{capability}",
            )
        return self._clients[capability]

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        from std_srvs.srv import Trigger  # type: ignore[import-not-found]

        client = self._client_for(capability)
        if not client.wait_for_service(timeout_sec=5.0):
            return {
                "success": False,
                "error": f"ROS service {self._service_prefix}/{capability} not available",
            }

        request = Trigger.Request()
        future = client.call_async(request)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._rclpy.spin_until_future_complete, self._node, future)
        response = future.result()
        if response is None:
            return {
                "success": False,
                "error": f"ROS service {capability} call timed out or failed",
            }
        return {"success": bool(response.success), "message": response.message}

    def shutdown(self) -> None:
        self._node.destroy_node()
        # Tear down only THIS adapter's own Context (per-actor, from
        # __init__ above) -- never the process-global `rclpy.shutdown()`,
        # which would also tear down every other co-resident actor's ROS
        # runtime.
        if self._context.ok():
            self._context.try_shutdown()


class RemoteRosExecutionAdapter:
    """RosExecutionAdapter that delegates the real work to a separate
    `ros-bridge` sidecar container over plain HTTP, instead of importing
    rclpy in-process.

    Why this is safe from a governance standpoint: `invoke()` is only ever
    reached through `run_ros_action_if_governed` above, which documents
    (and enforces, via `ensure_governed`) that authorization has ALREADY
    happened before `invoke()` is called. Moving where invoke()'s actual
    execution runs — in-process vs. one HTTP hop to a co-located sidecar —
    does not move where that authorization decision is made; it still
    happens entirely inside this actor's own process, same as every other
    adapter in this file.

    Why HTTP instead of rclpy directly: this lets the CognitiveOS actor
    container stay the plain, unmodified image every non-robot actor
    already uses (no ROS 2/rclpy/px4_msgs baked in), with the actual ROS
    bridge (kernel/edge/ros_bridge_server.py, wrapping
    kernel/edge/px4_ros_adapter.py::Px4RosExecutionAdapter unchanged)
    running in its own container inside the simulator's own Pod
    (deploy/k8s/px4-sim-deployment.yaml) — same-Pod localhost networking
    between the bridge and PX4/xrce-agent there, exactly as before,
    avoiding cross-Pod ROS 2 DDS discovery entirely (unicast HTTP has no
    multicast-discovery dependency, unlike raw rclpy).
    """

    def __init__(self, *, actor_id: str = "", base_url: str) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.actor_id = actor_id
        self._base_url = base_url.rstrip("/")
        # latest_state()'s own cache -- see that method and start() below.
        self._cached_state: Any = None
        self._poll_task: Any = None

    def start(self) -> None:
        """Launch the background telemetry poller on the CURRENT running
        event loop -- same shape as kernel/edge/livekit_adapter.py::
        LiveKitVoiceObservationProvider.start(), and for the same reason:
        latest_state() below must stay synchronous and non-blocking to
        satisfy kernel/pipeline/observations.py::WorldPollingProvider's
        contract (Px4RosExecutionAdapter.latest_state() is a plain
        attribute read, called from inside a tick), so the actual GET
        /state HTTP call happens here, in a background task, not inline.
        actor_runtime.py calls this right after register_drone_adapter()
        registers this same object -- never raises; a failure to start
        just means latest_state() stays None (no telemetry), matching
        every other "no adapter registered" outcome observations.py
        already handles.
        """
        if self._poll_task is not None:
            return
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "RemoteRosExecutionAdapter.start() called outside a running event loop for actor %s — telemetry polling will not run",
                self.actor_id,
            )
            return
        self._poll_task = loop.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        import asyncio

        import httpx

        try:
            while True:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(f"{self._base_url}/state")
                        resp.raise_for_status()
                        raw = resp.json().get("state")
                    self._cached_state = self._state_from_json(raw) if raw is not None else None
                except Exception:
                    # Transient poll failure -- never surfaced as a crash,
                    # same "next poll tries again" posture as
                    # LiveKitVoiceObservationProvider._poll_once(). The
                    # last-known cached state simply goes stale and
                    # is_fresh() (drone_state.py) naturally stops emitting
                    # it once it's more than 5s old.
                    logger.info("RemoteRosExecutionAdapter: telemetry poll failed", exc_info=True)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _state_from_json(raw: dict[str, Any]) -> Any:
        from .drone_state import DroneState

        # ros-bridge's own GET /state (ros_bridge_server.py) serializes a
        # real DroneState via dataclasses.asdict() -- reconstructing by
        # keyword here, not positionally, so an added/reordered field on
        # either side can never silently swap two values.
        return DroneState(**raw)

    def latest_state(self) -> Any:
        """Duck-types Px4RosExecutionAdapter.latest_state() (kernel/edge/
        drone_state.py's DroneState | None contract) so this SAME adapter
        object -- already registered via register_drone_adapter() for its
        .invoke() capability -- also satisfies WorldPollingProvider's
        telemetry read, closing the gap start()'s own docstring
        describes. Returns whatever the last successful poll cached;
        never blocks, never raises."""
        return self._cached_state

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        import httpx

        # Generous timeout: must cover Px4RosExecutionAdapter's own longest
        # real wait with margin, not just typical request latency. Confirmed
        # live this margin was wrong: px4_ros_adapter.py's own defaults are
        # _ARM_TIMEOUT_S=15, _TAKEOFF_TIMEOUT_S=60, _WAYPOINT_TIMEOUT_S=90,
        # _LAND_TIMEOUT_S=120 (the "_LAND_TIMEOUT_S=45" this comment used to
        # cite was stale) -- a client timeout equal to or below any of
        # those turns a clean, informative server-side "did not reach
        # altitude/waypoint/disarm within Ns" JSON response into an opaque
        # httpx.ReadTimeout with an EMPTY message ("ros-bridge call failed:
        # ") on the actor side, racing against the server's own wait
        # instead of comfortably outlasting it. 150.0 clears the largest
        # (120.0) with real margin.
        try:
            async with httpx.AsyncClient(timeout=150.0) as client:
                response = await client.post(
                    f"{self._base_url}/invoke",
                    json={"capability": capability, "parameters": parameters},
                )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"ros-bridge call failed: {exc}"}


def build_ros_execution_adapter(*, actor_id: str = "", require_real: bool = False) -> RosExecutionAdapter:
    """Clear, explicit startup behavior (Section 1's own requirement):

    - require_real=False (default -- the normal CognitiveOS runtime):
      NEVER crashes because ROS is not installed. Returns a
      FakeRosExecutionAdapter.
    - require_real=True (a robot/ROS deployment's own startup path,
      opted into explicitly): raises RosUnavailableError with an
      actionable message if rclpy cannot be imported, rather than
      silently degrading to a fake adapter that would make a robot
      deployment believe it is actually moving hardware when it is not.

    actor_id (Actor Cell binding, docs/ACTOR_CELL_ARCHITECTURE.md Section
    I/3): binds the returned adapter to exactly one actor, so
    run_ros_action_if_governed can refuse to invoke it on behalf of any
    other actor_id. Optional/defaulted ("") -- omitting it preserves prior
    behavior exactly (an unbound adapter, as every existing caller
    constructs today; there are no production callers of this function
    yet, per docs/ACTOR_CELL_ARCHITECTURE.md's own finding, so this only
    affects future wiring, not any live call site).

    ROS_ADAPTER_KIND (env var, checked only when require_real=True):
    "px4" (default "rclpy") selects the real, PX4-specific
    kernel/edge/px4_ros_adapter.py::Px4RosExecutionAdapter (Arm/Takeoff/
    Waypoint/Land against real PX4 topics, telemetry-confirmed) instead
    of the generic RclpyRosExecutionAdapter (topic-agnostic, no PX4
    knowledge) -- actor_runtime.py's own node_class=robot boot path
    previously only ever built the generic adapter (and even then only
    with require_real defaulted False, i.e. the FAKE one), so a drone
    actor's standard boot never got the real, already-proven-live PX4
    adapter this session's prompt-driven mission had to construct by
    hand instead. PX4_NAMESPACE (required when ROS_ADAPTER_KIND=px4)
    matches PX4_UXRCE_DDS_NS on the px4-sitl sidecar in the same Pod
    (e.g. "px4_1") -- see deploy/k8s/drone-actor-deployment.yaml.
    """
    if not require_real:
        return FakeRosExecutionAdapter(actor_id=actor_id)
    adapter_kind = os.getenv("ROS_ADAPTER_KIND", "rclpy").strip().lower()
    try:
        if adapter_kind == "remote_http":
            base_url = os.getenv("ROS_BRIDGE_URL", "").strip()
            if not base_url:
                raise RosUnavailableError(
                    "ROS_ADAPTER_KIND=remote_http requires ROS_BRIDGE_URL to be set "
                    "(the ros-bridge sidecar's own Service URL, e.g. "
                    "http://px4-sim-<actor>.monkeybrain.svc.cluster.local:9000)"
                )
            return RemoteRosExecutionAdapter(actor_id=actor_id, base_url=base_url)
        if adapter_kind == "px4":
            namespace = os.getenv("PX4_NAMESPACE", "").strip()
            if not namespace:
                raise RosUnavailableError(
                    "ROS_ADAPTER_KIND=px4 requires PX4_NAMESPACE to be set "
                    "(must match the px4-sitl sidecar's own PX4_UXRCE_DDS_NS)"
                )
            from src.monkey_brain.kernel.edge.px4_ros_adapter import (
                Px4RosExecutionAdapter,
            )

            return Px4RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
        return RclpyRosExecutionAdapter(actor_id=actor_id)
    except RosUnavailableError:
        raise
    except Exception as exc:
        raise RosUnavailableError(f"failed to construct a real ROS execution adapter: {exc}") from exc
