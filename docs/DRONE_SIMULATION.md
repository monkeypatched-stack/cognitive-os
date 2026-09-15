# Drone Simulation — ROS 2 / PX4 / Gazebo / Isaac Sim

## Architecture

```
CognitiveOS
     |
  Actor Cell            kernel/society/actor_cell.py
     |
   Planner               kernel/pipeline/belief_runtime.py
     |
  Governance             kernel/governance.py -> OPA (agentos_governance.rego)
     |
   Executor               ActionExecutor -> ensure_governed
     |
 Drone Adapter           kernel/edge/px4_ros_adapter.py (Px4RosExecutionAdapter)
     |
   ROS 2                  kernel/edge/ros_integration.py (run_ros_action_if_governed)
     |
    PX4                   real offboard control (Arm/Takeoff/Waypoint/Land)
   /    \
Gazebo   Isaac Sim         interchangeable — PX4 doesn't know or care which
```

The CognitiveOS Actor never touches a simulator API directly. It calls a
governed capability (`Arm`/`Takeoff`/`Waypoint`/`Land`,
`kernel/domains/robot.py`); the capability calls
`run_ros_action_if_governed` (`kernel/edge/ros_integration.py`), the sole
legal entry point into the drone adapter — every call is authorized and
audited before `adapter.invoke()` ever runs (see
`tests/unit/test_edge_ros_integration.py`,
`tests/validation/test_v13_ros_governance.py`). The adapter speaks PX4's own
ROS 2 topic contract; what's on the other end of that connection — Gazebo,
Isaac Sim, or real hardware — is invisible above the adapter.

## Role of each layer

- **ROS 2** — the common middleware boundary. `Px4RosExecutionAdapter`
  publishes `OffboardControlMode`/`TrajectorySetpoint`/`VehicleCommand` and
  subscribes to `vehicle_status_v1`/`vehicle_status_v4`/
  `vehicle_local_position_v1` under a per-vehicle namespace
  (`PX4_NAMESPACE`, e.g. `px4_1`).
- **PX4** — the flight-control authority. Owns arming, mode switching,
  attitude/position control, and (for `Land`) its own `AUTO_LAND` control
  law. CognitiveOS never streams motor commands — it streams a position
  setpoint and PX4 decides how to fly there.
- **Gazebo** — the default simulation backend. No new launch code needed
  here: `px4io/px4-sitl-gazebo` (re-tagged `px4-sitl-flat` in
  `deploy/k8s/px4-sim-deployment.yaml`) already bundles PX4 SITL + Gazebo.
  See `docs/PX4_MAC_DOCKER.md` for the manual dev-setup walkthrough.
- **Isaac Sim** — the higher-fidelity backend, via NVIDIA's Pegasus
  Simulator extension. `scripts/isaac_pegasus_sim.py` launches Isaac Sim
  itself and spawns one or more `Multirotor` vehicles, each with a
  `PX4MavlinkBackend` that autolaunches a real PX4 SITL instance
  (`none_iris` airframe — no built-in sim, expects a MAVLink-connected
  external one). PX4's own uXRCE-DDS output is unchanged by this — the
  same `ros_bridge_server.py`/`Px4RosExecutionAdapter` stack that talks to
  a Gazebo-backed PX4 talks to an Isaac-backed one, with no code
  differences on the CognitiveOS side.

## Observation pipeline

```
Gazebo / Isaac Sim -> PX4 -> ROS 2 -> Px4RosExecutionAdapter.latest_state()
                                             |
                              kernel/edge/drone_state.py's
                              actor_id -> adapter registry
                                             |
                    WorldPollingProvider.observe() (kernel/pipeline/observations.py)
                                             |
                           ObservationSet -> BeliefFusion -> Belief
```

`Px4RosExecutionAdapter.latest_state()` returns a typed `DroneState`
(`kernel/edge/drone_state.py`) built from telemetry the adapter already
subscribes to for its own Arm/Takeoff/Waypoint/Land confirmation logic —
armed state and local position. `actor_runtime.py`'s `ActorRuntime.start()`
registers this Pod's adapter (`register_drone_adapter`) right after it binds
the adapter to the actor's `ActorCell`; `WorldPollingProvider.observe()`
looks the adapter up by `actor_id` every tick and folds fresh telemetry into
observations with `provenance.source == "px4_ros"`.

**Stale telemetry is never reported as current.** `is_fresh()` rejects
anything older than `STALE_TELEMETRY_SECONDS` (5s) — a disconnected
simulator or dead ROS link produces *no* drone observations that tick, not
fabricated ones. A `None`/missing/exception-raising adapter degrades the
same way (logged at debug level, never a crash).

**Known, honest gap:** `DroneState.battery`/`.heading`/`.flight_mode`/
`.gps_state` are always `None` today. The adapter doesn't subscribe to
`battery_status`/`vehicle_gps_position`/`vehicle_attitude` yet — verify the
real px4_msgs field names on a machine with ROS 2 + px4_msgs installed
before wiring these up; guessing them here (no such environment was
available while writing this) risks shipping silently-wrong telemetry.

## Execution pipeline

`kernel/domains/robot.py`'s `ArmCapability`/`TakeoffCapability`/
`WaypointCapability`/`LandCapability` validate parameters (altitude/
horizontal bounds), then call `run_ros_action_if_governed(capability=...,
resource=f"px4:{actor_id}", adapter=context["ros_adapter"], ...)`.
`context["ros_adapter"]` resolves to this actor's own `ActorCell.ros_adapter`
— never a shared or cross-actor adapter (`ActorCell.__post_init__` raises if
one is ever bound to the wrong actor_id).

`kernel/edge/drone_state.py`'s `DroneCommand`/`DroneCommandResult` are a
typed view over these same capabilities' `parameters`/return dict — not a
second action vocabulary. `DroneCommand(DroneCommandType.WAYPOINT, x=..., y=...,
height_m=...).to_capability_parameters()` produces exactly what
`WaypointCapability._validate()` expects.

## Simulator selection

Deliberately **not** a CognitiveOS config value — nothing in the kernel
behaves differently based on which simulator is running (that's the point
of the adapter boundary above). Selection happens entirely at launch time:

- **Gazebo**: run PX4 SITL with the Gazebo-bundled image
  (`deploy/k8s/px4-sim-deployment.yaml`'s `px4-sitl` container, or manually
  per `docs/PX4_MAC_DOCKER.md`), then `scripts/run_px4_bridge.sh <instance>`
  per vehicle.
- **Isaac Sim**: run `scripts/isaac_pegasus_sim.py` under Isaac's own Python
  interpreter (`~/isaacsim/python.sh scripts/isaac_pegasus_sim.py`, set
  `ISAAC_VEHICLES` for multi-vehicle), then `scripts/run_px4_bridge.sh
  <instance>` per vehicle exactly as with Gazebo — the bridge script and
  everything downstream of it is identical either way.

## Multi-drone configuration

`PX4_NAMESPACE` (`px4_1`, `px4_2`, ...) is the canonical per-vehicle ROS
namespace, threaded through `Px4RosExecutionAdapter`, `ros_bridge_server.py`,
and `run_px4_bridge.sh`. `ACTOR_ID` is the parallel CognitiveOS-side identity
— one Pod hosts exactly one actor and, for a robot deployment, exactly one
bound adapter (`deploy/k8s/drone-actor-deployment.yaml` + the `px4-sim`
sidecar Pod, per-`ACTOR_ID` templates rendered via `envsubst`).
`scripts/fleet_mission.py` demonstrates a real multi-drone pattern (lockstep
dispatch, fail-safe landing on partial failure) — note it drives drones via
raw HTTP to each one's bridge `/invoke`, not through CognitiveOS governance;
see `tests/scenarios/test_three_drone_mission.py` for the actual
CognitiveOS-native multi-actor pattern (three isolated, governed
`ActorCell`s, each with its own `FakeRosExecutionAdapter`).

## Simulation time vs. trusted time

Not yet separated in this codebase. `DroneState.sim_timestamp` exists as a
field for this but is currently unpopulated — PX4's own message timestamps
would need to be threaded through instead of the wall-clock receipt time
`latest_state()` uses today. Do not conflate this with Trusted Time (a
separate, already-built HMAC-signed-timestamp service — see
`kernel/trusted_time.py`, `domains/.../services/trusted_time/` — used for
signing security-critical artifacts, not simulation physics).

## Safety and governance

ARM/TAKEOFF/LAND/WAYPOINT all go through
`identity -> authorization -> policy -> approval (if required) -> execution
-> PX4`, same as every other governed capability — this is not weakened for
simulated vehicles. `run_ros_action_if_governed` is a structurally-enforced
chokepoint: `tests/architecture/test_architecture_invariants.py` and
`tests/unit/test_ros_integration_contract.py` both assert no code path
calls `adapter.invoke()` directly.

**Known gap, not yet closed**: no idempotency/replay protection exists at
this layer — `tests/validation/test_v13_ros_governance.py` proves an
identical command sent twice executes twice (the vehicle moves twice). This
matters more once retry-after-reconnect logic exists for a flaky sim link;
until then, a caller that retries a drone command is responsible for not
retrying one that already succeeded.

## Gazebo launch

See `docs/PX4_MAC_DOCKER.md` for the full walkthrough (Docker Desktop on
Apple Silicon, 3-vehicle topology, validation steps). Short version:

```bash
docker run --rm -it px4io/px4-sitl-gazebo:latest   # PX4 SITL + Gazebo
scripts/run_px4_bridge.sh 1                         # bridge for px4_1
```

## Isaac Sim launch

```bash
export ISAAC_VEHICLES=2   # optional, multi-vehicle
~/isaacsim/python.sh scripts/isaac_pegasus_sim.py
scripts/run_px4_bridge.sh 1
scripts/run_px4_bridge.sh 2
```

Requires `PX4_MSGS_WS` set to your own px4_msgs colcon workspace (see
`run_px4_bridge.sh`'s own error message — this is inherently
machine-specific, there's no correct universal default).

## Troubleshooting

- **Arm times out, `arming_state` stuck at DISARMED**: usually a
  `vehicle_status` QoS mismatch (PX4 publishes `BEST_EFFORT`/`VOLATILE`;
  a `RELIABLE` subscriber silently receives nothing) or a topic-version
  mismatch across PX4 releases — `Px4RosExecutionAdapter` already subscribes
  to both `vehicle_status_v1` and `vehicle_status_v4` to cover this; if a
  future PX4 release renames the topic again, add the new name the same way.
- **Commands silently ignored on vehicle 2+**: PX4 sets
  `MAV_SYS_ID = px4_instance + 1`; the adapter learns `target_system` from
  incoming `vehicle_status` rather than assuming `1` — if telemetry isn't
  flowing yet when a command is sent, it still targets system 1 and a
  non-zero instance ignores it. Wait for `latest_state()` to return non-None
  before issuing the first command to a non-zero instance.
- **Waypoint/Land never confirms on a slow (e.g. heavily-loaded Isaac) scene**:
  override `PX4_<STEP>_TIMEOUT_S` (`PX4_TAKEOFF_TIMEOUT_S`,
  `PX4_WAYPOINT_TIMEOUT_S`, `PX4_LAND_TIMEOUT_S`) and
  `PX4_POSITION_TOLERANCE_M`/`PX4_ALTITUDE_TOLERANCE_M` — see
  `px4_ros_adapter.py`'s own `_timeout`/`_tolerance` docstrings for measured
  real-world drift this accounts for.
