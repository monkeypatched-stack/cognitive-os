# PX4 SITL + ROS 2 on macOS

This setup runs the Linux-only ROS 2/PX4 integration inside Docker Desktop;
macOS runs only Docker and CognitiveOS development tools. It targets Apple
Silicon with Linux ARM64 images.

## Prerequisites

Start Docker Desktop and verify the daemon is reachable:

```bash
docker info
```

If this fails with a `docker.sock` permission error, Docker Desktop is not
running yet.

Recommended Docker Desktop settings:

- Linux containers
- at least 8 CPU cores
- at least 12 GB memory
- at least 40 GB disk
- host networking disabled initially

PX4 publishes an ARM64 SITL image. The official image is useful for validating
PX4/Gazebo first:

```bash
docker pull px4io/px4-sitl-gazebo:latest
docker run --rm -it \
  -p 14550:14550/udp \
  px4io/px4-sitl-gazebo:latest
```

## ROS 2 container

Use Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic, matching the current
PX4 ROS 2 documentation. The container must contain:

- `rclpy`
- `px4_msgs` built from the same PX4 release used by SITL
- `ros_gz_bridge` for Gazebo clock integration when needed
- `MicroXRCEAgent`
- the CognitiveOS checkout and its Python dependencies

The PX4 uXRCE-DDS agent uses UDP port 8888. Keep all simulator and ROS
containers on one Docker network. Do not use `localhost` between containers.

## Three-vehicle topology

Use PX4's multi-vehicle launcher inside the Linux simulation environment:

```bash
./Tools/simulation/sitl_multiple_run.sh -n 3
```

The current PX4 multi-vehicle setup assigns distinct instance namespaces such
as `px4_1`, `px4_2`, and `px4_3`. Verify before running CognitiveOS:

```bash
ros2 topic list | sort | grep px4_
ros2 topic echo /px4_1/fmu/out/vehicle_status
ros2 topic echo /px4_2/fmu/out/vehicle_status
ros2 topic echo /px4_3/fmu/out/vehicle_status
```

The three Actor Cells must bind explicitly to those namespaces:

```text
Actor A -> /px4_1
Actor B -> /px4_2
Actor C -> /px4_3
```

Do not use the current generic `RclpyRosExecutionAdapter` as a PX4 flight
adapter. It currently calls `std_srvs/Trigger`; PX4 control requires a
PX4-specific adapter using `px4_msgs` topics/services and the same
`run_ros_action_if_governed` entry point.

## Validation order

1. Start one PX4 instance and verify telemetry.
2. Verify one arm/takeoff/land command from a ROS 2 node.
3. Start three instances and verify namespace isolation.
4. Add the PX4-specific CognitiveOS adapter.
5. Run the three Actor Cell mission test.
6. Kill PX4/Actor B and verify A and C continue.

The host-side CognitiveOS test remains useful on macOS:

```bash
pytest -q tests/scenarios/test_three_drone_mission.py
```

That test uses the fake adapter intentionally and must not be reported as real
PX4 validation.
