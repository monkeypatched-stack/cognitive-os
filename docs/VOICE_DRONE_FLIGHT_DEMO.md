# Voice → Simulated Drone Flight + Live Camera — Demo Runbook

Reproduces: a spoken command flies a simulated drone through the real
CognitiveOS voice → goal → plan → governance → ROS2/PX4 path, while the
drone's camera streams independently through LiveKit into the Next.js
workspace at `/drone-flight`.

**This is a runbook for a human operator with a real cluster, LiveKit
server, and microphone/browser.** Nothing in this repo's automated test
suite spins those up — `tests/scenarios/test_voice_to_drone_flight_e2e.py`
verifies the CognitiveOS wiring against fakes (see that file's own
docstring); this document is how you verify the same chain live.

## 1. Prerequisites

- A Kubernetes cluster reachable by `kubectl` (see `docs/DRONE_SIMULATION.md`
  for the general PX4/ROS2/Gazebo prerequisites — px4-sitl-flat image,
  `PX4_MSGS_WS`, etc.).
- A LiveKit server reachable from both the CognitiveOS backend and the
  browser (`LIVEKIT_URL` / `NEXT_PUBLIC_LIVEKIT_URL`).
- **`openai-whisper` installed** (`uv sync --extra livekit`, or `pip install
  openai-whisper resampy`) — this was previously undeclared/not installed;
  without it, `LiveKitVoiceObservationProvider._transcribe_sync` will raise
  on every audio window (caught, logged as `voice.transcription.failed`,
  never crashes, but no transcript is ever produced). `resampy` is optional
  (degrades to source sample rate if absent).
- A browser with microphone access, on the same machine or network as the
  Next.js dev server.

## 2. Simulator + PX4 + ROS2 startup

Same as `docs/DRONE_SIMULATION.md`, with the camera-equipped airframe and
`DRONE_CAMERA_ENABLED` opt-in from that doc's own "Camera + visual landmark
matching" section:

```bash
# Shared Gazebo world
kubectl apply -f deploy/k8s/gazebo-shared-deployment.yaml

# One drone, camera-equipped
ACTOR_ID=drone-a PX4_NAMESPACE=px4_1 PX4_INSTANCE=1 PX4_UXRCE_DDS_PORT=8888 \
PX4_SIM_MODEL=gz_x500_mono_cam \
  envsubst '${ACTOR_ID} ${PX4_NAMESPACE} ${PX4_INSTANCE} ${PX4_UXRCE_DDS_PORT} ${PX4_SIM_MODEL}' \
  < deploy/k8s/px4-sim-deployment.yaml | kubectl apply -f -

# Drone actor Pod, camera identity + ROS2->LiveKit bridge opted in
export DRONE_CAMERA_ENABLED=true
export PX4_NAMESPACE=px4_1
export LIVEKIT_URL="wss://your-livekit-host"
export LIVEKIT_MISSION_ROOM="mission-room"
kubectl apply -f deploy/k8s/drone-actor-deployment.yaml
```

Verify telemetry is flowing before continuing (same check `docs/PX4_MAC_DOCKER.md`
uses): `ros2 topic echo /px4_1/fmu/out/vehicle_status`.

## 3. CognitiveOS startup

Start the CognitiveOS API (`monkeybrain` entry point / your usual dev
command) with `LIVEKIT_URL` set, pointed at the same PlanetaryRuntime the
actor Pod above registered into.

## 4. LiveKit startup

Point `LIVEKIT_URL` (backend) and `NEXT_PUBLIC_LIVEKIT_URL` (frontend) at
your running LiveKit server (self-hosted `livekit-server` or LiveKit
Cloud). No CognitiveOS code starts a LiveKit server itself — it only
connects to one, matching the "reuse the existing LiveKit
room/session/token architecture, never a second LiveKit service" rule.

## 5. Next.js workspace startup

```bash
cd apps/workspace
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1/agentos \
NEXT_PUBLIC_LIVEKIT_URL=wss://your-livekit-host \
  npm run dev
```

Sign in, then navigate to **Drone Flight** in the nav bar (`/drone-flight`).

## 6. Connect the drone camera

On the `/drone-flight` page: pick the actor (`drone-a`), click **Connect
camera**. This calls `POST /video/sessions` (resolves the room/track
server-side from `kernel/edge/camera_state.py`'s registered
`CameraIdentity` — never a client-supplied room), connects a
subscribe-only LiveKit `Room`, and attaches the drone's camera track to
the on-page `<video>` element the moment `RoomEvent.TrackSubscribed` fires
for `camera_track_name`. "Camera: LIVE" appears once the track is
attached and `GET /video/sessions/{id}` reports `stream_available: true`.

## 7. Issue the voice command

Set **Room** to the same value as `LIVEKIT_MISSION_ROOM` above
(`mission-room` by default — voice sessions don't auto-resolve a room the
way video sessions do, see `api/routes/voice.py`), click **Connect
voice**, **Unmute**, then speak:

> "Take drone one to waypoint Alpha."

This flows: LiveKit audio → `LiveKitVoiceObservationProvider` (4-second
window batches, real Whisper) → `voice_transcript` Observation →
`VoiceCommandRuntime._handle_transcript()` → `voice_intent.
interpret_voice_transcript()` classifies it `actionable` and appends the
known test waypoint "Alpha"'s coordinates (`x=8.0, y=0.0` —
`kernel/edge/voice_intent.py`'s own small, explicitly-labeled test/demo
registry, NOT a real navigation system) → `ActorRuntime.add_goal(...)` →
`SocietyRuntime.tick_one_actor()` → the real LLM planner selects
`Waypoint`, backfills those exact coordinates from the literal `x=/y=`
text (`kernel/pipeline/llm_planner.py::_backfill_px4_parameters`) → real
governance (`ensure_governed` → OPA) → `run_ros_action_if_governed` →
`Px4RosExecutionAdapter.invoke(capability="Waypoint", ...)` → the
simulated drone flies.

## 8. Expected UI

```
DRONE 1

Connection: Connected
Flight state: NAVIGATING → ARRIVED
Camera: LIVE

Voice command: "Take drone one to waypoint Alpha."
Transcript: Take drone one to waypoint Alpha.
```

`Flight state` is derived ONLY from `VoiceSession.status` transitions
`VoiceCommandRuntime` already exposes (`planning` → `NAVIGATING`,
`planning` → `listening` → `ARRIVED`) — `WaypointCapability`'s governed
call blocks until PX4 confirms arrival before returning success, so a
`planning`→`listening` transition means the flight step genuinely
completed, not a guess.

## 9. Expected drone behavior

The simulated vehicle arms (if not already), transitions to OFFBOARD, and
flies to local NED `(8.0, 0.0)` relative to its own EKF origin, holding
its current altitude. Confirm via `ros2 topic echo
/px4_1/fmu/out/vehicle_local_position` or the Foxglove bridge already
documented in `docs/DRONE_SIMULATION.md`.

## 10. Simulation safety

`Px4RosExecutionAdapter.is_simulation` is hardcoded `True` — this is the
only backend implemented in this codebase (Gazebo SITL). There is no
production code path in this repo that can address real hardware. Before
running any flight command against a deployment you don't fully control,
confirm the target is actually the SITL stack above, not something else
entirely — this demo never attempts to detect that automatically beyond
what `Px4RosExecutionAdapter` already is.

## 11. Troubleshooting

- **No transcript ever appears**: `openai-whisper` not installed (see
  Prerequisites) — check backend logs for `voice.transcription.failed` or
  a `ModuleNotFoundError: No module named 'whisper'`.
- **"waypoint Alpha" doesn't move the drone**: confirm the spoken sentence
  actually classified as `actionable` (`GET /voice/sessions/{id}` ->
  `last_transcript`/`clarification_reason`) — a mis-transcribed word can
  make `voice_intent.py`'s ambiguity gate flag it `ambiguous` instead
  (e.g. Whisper mishears "Alpha" as something without a recognized
  `waypoint <name>` shape). Named waypoints outside `alpha`/`bravo`/
  `charlie` pass through unedited and the planner has no deterministic
  coordinate to use — see `kernel/edge/voice_intent.py`'s own
  `_TEST_WAYPOINT_COORDINATES`.
- **Camera never goes LIVE**: confirm `DRONE_CAMERA_ENABLED=true` and
  `PX4_SIM_MODEL=gz_x500_mono_cam` were both set for this drone (see
  `docs/DRONE_SIMULATION.md`'s own camera section) — a non-camera airframe
  publishes no image topic at all, and the bridge container harmlessly
  waits forever.
- **PX4/ROS2 unreachable**: `run_ros_action_if_governed` still runs
  governance, then `adapter.invoke()` fails — the API reports a failed
  flight command, never a fake success. Voice/goal state (transcript, last
  goal text) is unaffected; only the flight step itself fails, matching
  `tests/scenarios/test_voice_to_drone_flight_e2e.py::test_adapter_failure_reports_failure_not_fake_success`.
- **Governance denies the command**: expected if OPA/charter data denies
  `capability.Waypoint` for this runtime — this is not a bug to work
  around; check `opa/policies/agentos_governance.rego`'s data-driven rules
  for this deployment.
