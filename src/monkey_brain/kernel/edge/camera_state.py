"""Camera / drone / LiveKit identity mapping (CognitiveOS Drone Camera
Video Telemetry, spec section "MULTI-DRONE IDENTITY").

Same process-local, module-singleton registry SHAPE kernel/edge/
drone_state.py already uses for get_drone_adapter()/register_drone_adapter()
-- not a new registry pattern, a sibling one for a different resource
(camera/LiveKit identity instead of a ROS execution adapter instance).

The four identities a camera observation must NEVER blur together (this
codebase's real, already-deployed convention -- not the spec's own
"drone_01" example naming, which this module's docstrings translate to what
actually exists):

    CognitiveOS actor_id        e.g. "drone-a"   (ActorCell.actor_id)
    PX4/ROS 2 namespace         e.g. "px4_1"      (PX4_NAMESPACE, run_px4_bridge.sh)
    LiveKit participant identity e.g. "drone-a-camera"  (the ROS-bridge-side publisher)
    LiveKit video track name    e.g. "drone-a-camera-track"

Looking one of these up by display name or by "whichever camera connected
last" is exactly what this module exists to prevent -- every lookup is by
the authoritative actor_id.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

_lock = threading.Lock()
_registry: dict[str, "CameraIdentity"] = {}


@dataclass(frozen=True)
class CameraIdentity:
    """The explicit actor_id <-> vehicle <-> ROS2 namespace <-> LiveKit
    mapping for one drone's camera. Construct one per drone at the same
    place PX4_NAMESPACE/ACTOR_ID are already assigned (deploy/k8s/
    px4-sim-deployment.yaml's per-drone envsubst rendering), never inferred
    from an incoming LiveKit event."""

    actor_id: str
    """CognitiveOS ActorCell.actor_id, e.g. "drone-a" -- the SAME id
    run_ros_action_if_governed/ActorCell already use. This is the only key
    this module or any caller may look an identity up by."""
    vehicle_id: str
    """PX4 vehicle identity, e.g. "px4/drone-a" -- human/log-facing, not
    used for routing."""
    ros_namespace: str
    """PX4_NAMESPACE, e.g. "px4_1" -- must match the SAME value the drone's
    Px4RosExecutionAdapter/ros_bridge_server Pod was started with
    (deploy/k8s/px4-sim-deployment.yaml, run_px4_bridge.sh)."""
    livekit_room: str
    """The LiveKit room this drone's camera publishes into."""
    livekit_participant_identity: str
    """The LiveKit participant identity the ROS-camera-bridge publishes
    under, e.g. "drone-a-camera" -- distinct from the human operator's own
    participant identity and from VoiceCommandRuntime's
    "cognitiveos-listener-{session_id}" (kernel/edge/voice_command_runtime.py)."""
    camera_track_name: str
    """The LiveKit video track name, e.g. "drone-a-camera-track" -- how a
    subscriber (browser or LiveKitVideoObservationProvider) identifies
    WHICH published track is this drone's camera when a room has more than
    one drone in it."""


def register_camera_identity(identity: CameraIdentity) -> None:
    with _lock:
        _registry[identity.actor_id] = identity


def get_camera_identity(actor_id: str) -> CameraIdentity | None:
    with _lock:
        return _registry.get(actor_id)


def unregister_camera_identity(actor_id: str) -> None:
    with _lock:
        _registry.pop(actor_id, None)
