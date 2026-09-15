#!/usr/bin/env python3
"""Fly the same 3-drone mission as scripts/fly_swarm_headless_gazebo.py against
REAL headless Gazebo + PX4 SITL, but ONE DRONE AT A TIME instead of lockstep --
each drone's full Arm/Takeoff/Waypoint/Land mission runs to completion before
the next drone's mission starts, rather than all three stepping together.

Same bridges (deploy/k8s/px4-sim-deployment.yaml pods, port-forwarded to
127.0.0.1:9010/9011/9012) and the same fixed plan/waypoints as
scripts/demo_swarm_mission.py's A/B/C mission -- only the dispatch order
differs.

Usage:
    python3 scripts/fly_drones_sequentially.py [port:label ...]

    (defaults to 9010:drone-a 9011:drone-b 9012:drone-c)
"""

import json
import sys

import requests

FLEET = [a.split(":", 1) for a in (sys.argv[1:] or ["9010:drone-a", "9011:drone-b", "9012:drone-c"])]

# Same waypoints as scripts/demo_swarm_mission.py's A/B/C mission.
WAYPOINTS = {"drone-a": (8.0, 0.0), "drone-b": (28.0, 0.0), "drone-c": (8.0, 20.0)}


def steps_for(label: str) -> list[dict]:
    x, y = WAYPOINTS[label]
    return [
        {"capability": "Arm", "parameters": {}},
        {"capability": "Takeoff", "parameters": {"height_m": 5.0}},
        {"capability": "Waypoint", "parameters": {"x": x, "y": y, "height_m": 5.0}},
        {"capability": "Land", "parameters": {}},
    ]


def invoke(port: str, step: dict) -> dict:
    r = requests.post(f"http://127.0.0.1:{port}/invoke", json=step, timeout=240)
    return r.json()


def fly_one(port: str, label: str) -> bool:
    try:
        live = requests.get(f"http://127.0.0.1:{port}/live", timeout=8).json()
    except Exception as exc:  # noqa: BLE001
        live = f"unreachable: {exc}"
    print(f"{label} (:{port}) -> {live}")

    for step in steps_for(label):
        print(f"  {step['capability']}...", end=" ", flush=True)
        try:
            res = invoke(port, step)
        except Exception as exc:  # noqa: BLE001
            print(f"bridge error {exc}")
            print(f"  landing {label} so it doesn't strand airborne:")
            try:
                land = invoke(port, {"capability": "Land", "parameters": {}})
                print(f"    {'landed' if land.get('success') else land.get('error')}")
            except Exception as exc2:  # noqa: BLE001
                print(f"    land failed ({exc2})")
            return False
        if res.get("success"):
            extra = {k: v for k, v in res.items() if k not in ("success", "actor_id", "namespace")}
            print(f"ok {json.dumps(extra) if extra else ''}")
        else:
            print(f"FAILED {res.get('error', res)}")
            print(f"  landing {label} so it doesn't strand airborne:")
            try:
                land = invoke(port, {"capability": "Land", "parameters": {}})
                print(f"    {'landed' if land.get('success') else land.get('error')}")
            except Exception as exc2:  # noqa: BLE001
                print(f"    land failed ({exc2})")
            return False
    return True


def main() -> None:
    print(f"SEQUENTIAL MISSION (real headless Gazebo/PX4) — {len(FLEET)} drones, one at a time\n")
    results = {}
    for port, label in FLEET:
        print(f"--- {label} ---")
        results[label] = fly_one(port, label)
        print()

    flew = [label for label, ok in results.items() if ok]
    failed = [label for label, ok in results.items() if not ok]
    print(f"SEQUENTIAL MISSION COMPLETE — {len(flew)}/{len(FLEET)} drones: {', '.join(flew)}")
    if failed:
        print(f"  failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
