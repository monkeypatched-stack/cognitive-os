"""Unit tests for kernel/edge/camera_state.py -- the explicit
actor_id <-> vehicle <-> ROS2 namespace <-> LiveKit identity mapping
(CognitiveOS Drone Camera Video Telemetry, spec's "MULTI-DRONE IDENTITY"
section: "Do not rely on display names or implicit ordering")."""

from __future__ import annotations

from src.monkey_brain.kernel.edge.camera_state import (
    CameraIdentity,
    camera_identity_for_track,
    get_camera_identity,
    register_camera_identity,
    unregister_camera_identity,
)


def _identity(
    actor_id: str = "drone-a", room: str = "mission-room", track: str = "drone-a-camera-track"
) -> CameraIdentity:
    return CameraIdentity(
        actor_id=actor_id,
        vehicle_id=f"px4/{actor_id}",
        ros_namespace="px4_1",
        livekit_room=room,
        livekit_participant_identity=f"{actor_id}-camera",
        camera_track_name=track,
    )


def test_register_and_get_round_trips_by_actor_id():
    identity = _identity()
    register_camera_identity(identity)
    try:
        assert get_camera_identity("drone-a") == identity
    finally:
        unregister_camera_identity("drone-a")


def test_get_unknown_actor_returns_none():
    assert get_camera_identity("no-such-actor") is None


def test_unregister_removes_entry():
    register_camera_identity(_identity())
    unregister_camera_identity("drone-a")
    assert get_camera_identity("drone-a") is None


def test_unregister_unknown_actor_does_not_raise():
    unregister_camera_identity("never-registered")  # must not raise


def test_multiple_drones_do_not_collide():
    a = _identity(actor_id="drone-a", track="drone-a-camera-track")
    b = _identity(actor_id="drone-b", track="drone-b-camera-track")
    register_camera_identity(a)
    register_camera_identity(b)
    try:
        assert get_camera_identity("drone-a").camera_track_name == "drone-a-camera-track"
        assert get_camera_identity("drone-b").camera_track_name == "drone-b-camera-track"
    finally:
        unregister_camera_identity("drone-a")
        unregister_camera_identity("drone-b")


def test_camera_identity_for_track_resolves_room_and_track_to_actor():
    identity = _identity(actor_id="drone-a", room="mission-room", track="drone-a-camera-track")
    register_camera_identity(identity)
    try:
        found = camera_identity_for_track("mission-room", "drone-a-camera-track")
        assert found is not None
        assert found.actor_id == "drone-a"
    finally:
        unregister_camera_identity("drone-a")


def test_camera_identity_for_track_unknown_pair_returns_none_not_a_guess():
    identity = _identity(actor_id="drone-a", room="mission-room", track="drone-a-camera-track")
    register_camera_identity(identity)
    try:
        # Right room, wrong track name -- must not fall back to "the only
        # drone in this room" or any other implicit-ordering guess.
        assert camera_identity_for_track("mission-room", "some-other-track") is None
        assert camera_identity_for_track("other-room", "drone-a-camera-track") is None
    finally:
        unregister_camera_identity("drone-a")
