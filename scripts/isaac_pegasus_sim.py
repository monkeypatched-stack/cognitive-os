#!/usr/bin/env python
"""Isaac Sim + Pegasus PX4 SITL backend for the cognitive-os drone actor.

Replaces the Gazebo sim (`make px4_sitl gz_x500` against
Tools/simulation/gz/worlds/default.sdf) with Isaac Sim driving PX4 through
Pegasus's MAVLink backend. Nothing downstream of PX4 changes: PX4 still runs
its uXRCE-DDS client, so cognitiveos-xrce (MicroXRCEAgent udp4 -p 8888),
cognitiveos-heartbeat (fake GCS heartbeat to :18570) and
kernel/edge/ros_bridge_server.py on :9002 keep working untouched -- the
swap is only at the physics/rendering layer.

Run with Isaac Sim's bundled interpreter, NOT the system python:

    ~/isaacsim/python.sh scripts/isaac_pegasus_sim.py

Based on PegasusSimulator/examples/1_px4_single_vehicle.py, which is already
validated on this machine's Isaac Sim 5.1 (including its FPV-camera
workaround for the 5.1 SyntheticData TypeError). Differences from it:

* "Flat Plane" instead of "Curved Gridroom" -- missions fly to x/y of +-40m
  and the gridroom is an enclosed space the vehicle would fly into.
* px4_vehicle_model "none_iris" instead of the configs.yaml default
  "gazebo-classic_iris" -- this PX4 is v1.17.0-alpha1, where the airframe
  that expects no built-in simulator is ROMFS/.../airframes/10016_none_iris.
* PX4_UXRCE_DDS_NS is pinned (see below).
"""

import os

# PX4 instance 0 leaves the uXRCE-DDS namespace EMPTY (init.d-posix/rcS only
# adds "-n px4_$px4_instance" when the instance is non-zero), but
# Px4RosExecutionAdapter subscribes to /px4_1/fmu/out/* -- the bridge
# container runs with PX4_NAMESPACE=px4_1. PX4LaunchTool hands its own
# os.environ straight to the px4 child process, so exporting this before
# SimulationApp boots is what keeps the bridge's topics resolvable. Without
# it PX4 publishes on /fmu/out/* and every flight action times out waiting
# for telemetry that is being published one namespace over.
_VEHICLES = max(1, int(os.environ.get("ISAAC_VEHICLES") or 1))

# With a single vehicle, keep PX4 on instance 0 and pin the namespace: that
# is the only combination where the uXRCE-DDS namespace is px4_1 AND the GCS
# port stays 18570, which is what cognitiveos-heartbeat targets.
#
# With several vehicles the pin has to go. PX4LaunchTool hands its PX4 child
# a reference to this very os.environ, and every backend's start() fires on
# the same timeline.play(), so a per-vehicle value assigned here would race:
# all instances would inherit whichever was written last, every drone would
# publish on /px4_1/fmu/out/* and the bridges could not tell them apart.
# Instead the vehicles take instances 1..N and rcS derives px4_1..px4_N
# itself. The cost is that GCS ports shift to 18571..1857N, so the stock
# heartbeat container reaches none of them and each needs its own sender.
if _VEHICLES == 1:
    os.environ.setdefault("PX4_UXRCE_DDS_NS", "px4_1")
else:
    os.environ.pop("PX4_UXRCE_DDS_NS", None)

HEADLESS = os.environ.get("ISAAC_HEADLESS", "").strip().lower() in ("1", "true", "yes")

# Optional render resolution override (ISAAC_WIDTH / ISAAC_HEIGHT). Kit's
# tonemapping and LDR colour buffers scale with the render target, so on a
# machine whose GPU is shared with a resident LLM the default GUI resolution
# can exhaust VRAM outright: observed here as repeated
# carb.graphics-vulkan ERROR_OUT_OF_DEVICE_MEMORY allocating "Tonemapping
# Output", then a Kit crash before PX4 was ever launched. Headless runs fit
# in ~2.1GB and don't need this.
_WIDTH = int(os.environ.get("ISAAC_WIDTH") or 0)
_HEIGHT = int(os.environ.get("ISAAC_HEIGHT") or 0)


def _xyz(value: str) -> list[float]:
    x, y, z = (float(part) for part in value.split(","))
    return [x, y, z]


# Where the GUI's perspective camera sits and what it aims at, as "x,y,z".
# Defaults frame the launch point from behind/above with the whole mission
# envelope in view; override per-run for a closer or wider shot.
_VIEW_EYE = _xyz(os.environ.get("ISAAC_VIEW_EYE") or "30,-30,22")
_VIEW_TARGET = _xyz(os.environ.get("ISAAC_VIEW_TARGET") or "0,0,6")

# Physics floor for scenes that have none. Pegasus's curated environments
# ship collision geometry, but a raw USD from Isaac's asset library often
# does not -- the Rivermark city is purely visual/dsready content. Spawned
# over a scene with no colliders the vehicle simply falls: measured
# z = 7645m and still accelerating at 22 m/s, tumbling, so PX4 reported
# "Attitude failure (roll)" and refused to arm. No spawn position fixes
# that; the scene needs a floor. Defaults to on for raw USD paths, and
# ISAAC_GROUND=0/1 forces it either way.
_GROUND = os.environ.get("ISAAC_GROUND")
_GROUND_Z = float(os.environ.get("ISAAC_GROUND_Z") or 0.0)

# Spawn pose, as "x,y,z". Default sits the vehicle clear of the floor rather
# than intersecting it: the stock 0.07 overlaps a ground plane at z=0, and
# starting a rigid body inside a collider gives it an impulse on the first
# physics step. PX4's estimator sees that as a jolt and reports
# "High Accelerometer Bias" / "Attitude failure (roll)", then denies arming.
_SPAWN = _xyz(os.environ.get("ISAAC_SPAWN") or "0,0,0.25")

# Lateral gap between vehicles, so several drones do not spawn inside one
# another. Each vehicle's own spawn point becomes its PX4 local-frame origin,
# so "home"/(0,0) stays per-drone rather than shared.
_SPACING_M = float(os.environ.get("ISAAC_SPACING") or 3.0)

# Imports to start Isaac Sim from this script.
# carb.settings is imported here rather than inside IsaacPegasusSim: a
# function-local `import carb.settings` makes `carb` a local name for that
# whole method, so any carb.log_warn before it raises UnboundLocalError on
# the paths where that import is skipped (observed with ISAAC_TEX_BUDGET
# unset, which is the default -- it broke every plain run).
import carb
import carb.settings
from isaacsim import SimulationApp

# Must be instantiated immediately after the import, before any omni/pxr
# import, otherwise the simulator crashes -- this is the object that loads
# the extensions and the simulator itself.
_app_config = {"headless": HEADLESS}
if _WIDTH and _HEIGHT:
    _app_config["width"] = _WIDTH
    _app_config["height"] = _HEIGHT
simulation_app = SimulationApp(_app_config)

# -----------------------------------
# The actual script should start here
# -----------------------------------
import omni.timeline
from pxr import UsdGeom, Gf
from omni.isaac.core.world import World
from omni.isaac.core.utils.prims import is_prim_path_valid

from pegasus.simulator.params import ROBOTS, SIMULATION_ENVIRONMENTS
from pegasus.simulator.logic.backends.px4_mavlink_backend import (
    PX4MavlinkBackend,
    PX4MavlinkBackendConfig,
)
from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface

from scipy.spatial.transform import Rotation

DRONE_BODY_PRIM = "/World/quadrotor/body"
FPV_CAMERA_PRIM = f"{DRONE_BODY_PRIM}/fpv_cam"

# v1.17 PX4: the airframe that runs the flight stack with no built-in
# simulator, expecting one to connect over MAVLink (which Pegasus does).
PX4_AIRFRAME = os.environ.get("PX4_VEHICLE_MODEL", "none_iris")


class IsaacPegasusSim:
    """Isaac Sim standalone app hosting one PX4-backed multirotor."""

    def __init__(self):
        self.timeline = omni.timeline.get_timeline_interface()

        self.pg = PegasusInterface()
        self.pg._world = World(**self.pg._world_settings)
        self.world = self.pg.world

        # Flat Plane by default: open terrain, because the drone actor's
        # missions use a launch-relative local frame and range out to tens of
        # metres horizontally. Every other stock environment is an enclosed
        # interior, so ISAAC_ENV is only sensible for short missions -- and
        # a detailed scene costs meaningfully more VRAM than the bare plane.
        env_name = os.environ.get("ISAAC_ENV") or "Flat Plane"
        # A raw USD path or URL passes straight through. Pegasus's
        # SIMULATION_ENVIRONMENTS is a small curated subset, and Isaac's own
        # asset library carries scenes it omits -- notably the Rivermark
        # outdoor town at Isaac/Environments/Outdoor/Rivermark/rivermark.usd,
        # which is the only city-like environment available at all.
        if "://" in env_name or env_name.endswith((".usd", ".usda", ".usdc")):
            env_path = env_name
        elif env_name in SIMULATION_ENVIRONMENTS:
            env_path = SIMULATION_ENVIRONMENTS[env_name]
        else:
            raise SystemExit(
                f"ISAAC_ENV={env_name!r} is neither a USD path nor a known "
                "environment. Available: " + ", ".join(sorted(SIMULATION_ENVIRONMENTS))
            )
        # Optional texture-streaming cap (ISAAC_TEX_BUDGET, a 0-1 fraction of
        # VRAM). Large scenes fail at *load* by allocating more texture memory
        # than the card has left -- resolution barely matters, because the
        # cost is geometry and textures, not render targets. Streaming lets
        # Kit page textures instead of resident-allocating them all. These are
        # renderer-plugin settings rather than anything declared in the .kit
        # files, so a wrong path here is a silent no-op, not an error.
        budget = os.environ.get("ISAAC_TEX_BUDGET")
        if budget:
            s = carb.settings.get_settings()
            s.set("/rtx-transient/resourcemanager/enableTextureStreaming", True)
            s.set("/rtx-transient/resourcemanager/texturestreaming/memoryBudget", float(budget))
            s.set("/rtx/resourcemanager/texturestreaming/memoryBudget", float(budget))
            carb.log_warn(f"[env] texture streaming on, budget={budget}")

        carb.log_warn(f"[env] loading {env_name!r} -> {env_path}")
        self.pg.load_environment(env_path)

        raw_usd = env_path is env_name
        want_ground = raw_usd if _GROUND is None else _GROUND.strip() not in ("0", "false", "no")
        if want_ground:
            # Added before the vehicle spawns so physics has a floor from
            # the first step, rather than the vehicle free-falling while the
            # scene finishes streaming.
            self.world.scene.add_ground_plane(size=4000.0, z_position=_GROUND_Z, prim_path="/World/physicsGround")
            carb.log_warn(f"[env] added physics ground plane at z={_GROUND_Z:g}")
        carb.log_warn(f"[env] spawning vehicle at {_SPAWN}")

        # Instance 0 alone keeps the heartbeat-compatible ports; multiple
        # vehicles use 1..N so rcS names their namespaces for us.
        ids = [0] if _VEHICLES == 1 else list(range(1, _VEHICLES + 1))
        for slot, vehicle_id in enumerate(ids):
            config_multirotor = MultirotorConfig()
            mavlink_config = PX4MavlinkBackendConfig(
                {
                    "vehicle_id": vehicle_id,
                    "px4_autolaunch": True,
                    "px4_dir": self.pg.px4_path,
                    "px4_vehicle_model": PX4_AIRFRAME,
                }
            )
            config_multirotor.backends = [PX4MavlinkBackend(mavlink_config)]

            # The first vehicle keeps the original prim path so the FPV
            # camera attachment below still resolves.
            prim = "/World/quadrotor" if slot == 0 else f"/World/quadrotor_{vehicle_id}"
            pos = [_SPAWN[0], _SPAWN[1] + slot * _SPACING_M, _SPAWN[2]]
            Multirotor(
                prim,
                ROBOTS["Iris"],
                vehicle_id,
                pos,
                Rotation.from_euler("XYZ", [0.0, 0.0, 0.0], degrees=True).as_quat(),
                config=config_multirotor,
            )
            ns = os.environ.get("PX4_UXRCE_DDS_NS") or f"px4_{vehicle_id}"
            carb.log_warn(f"[veh] {prim} instance={vehicle_id} ns={ns} spawn={pos} gcs_port={18570 + vehicle_id}")

        self.world.reset()
        self._attach_fpv_camera()
        self._recenter_viewport()

        self.stop_sim = False

    def _recenter_viewport(self):
        """Frame the launch point in the GUI's perspective camera.

        Isaac's default view sits near the origin, so a mission ranging tens
        of metres horizontally and climbing to 15m leaves the vehicle a
        speck at the edge of frame (or out of it). Pull the camera back and
        up and aim it just above the launch point, which is where every
        mission starts and returns to. Headless has no viewport to move.
        """
        if HEADLESS:
            return
        try:
            from isaacsim.core.utils.viewports import set_camera_view
        except ImportError:  # pre-5.x extension layout
            from omni.isaac.core.utils.viewports import set_camera_view
        set_camera_view(eye=_VIEW_EYE, target=_VIEW_TARGET)
        carb.log_warn(f"[view] perspective camera at {_VIEW_EYE} looking at {_VIEW_TARGET}")

    def _attach_fpv_camera(self):
        """Plain USD camera prim rather than Pegasus's MonocularCamera, which
        trips a SyntheticData TypeError on Isaac Sim 5.1."""
        if not is_prim_path_valid(DRONE_BODY_PRIM):
            carb.log_warn(f"[fpv] {DRONE_BODY_PRIM} not found; FPV camera skipped")
            return
        stage = self.world.stage
        cam_prim = stage.DefinePrim(FPV_CAMERA_PRIM, "Camera")
        cam = UsdGeom.Camera(cam_prim)

        # ~140 deg horizontal FOV: h_fov = 2*atan(h_aperture / (2*focal)).
        cam.CreateFocalLengthAttr(10.0)
        cam.CreateHorizontalApertureAttr(55.0)
        cam.CreateVerticalApertureAttr(31.0)
        cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))

        xform = UsdGeom.Xformable(cam_prim)
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(0.20, 0.0, 0.05))
        qx, qy, qz, qw = Rotation.from_euler("ZYX", [-90.0, 0.0, 90.0], degrees=True).as_quat()
        xform.AddOrientOp().Set(Gf.Quatf(qw, qx, qy, qz))

    def run(self):
        carb.log_warn(
            f"[isaac-pegasus] PX4 airframe={PX4_AIRFRAME} "
            f"uXRCE-DDS ns={os.environ.get('PX4_UXRCE_DDS_NS')} "
            f"px4_dir={self.pg.px4_path}"
        )
        self.timeline.play()

        while simulation_app.is_running() and not self.stop_sim:
            self.world.step(render=True)

        carb.log_warn("[isaac-pegasus] closing.")
        self.timeline.stop()
        simulation_app.close()


def main():
    IsaacPegasusSim().run()


if __name__ == "__main__":
    main()
