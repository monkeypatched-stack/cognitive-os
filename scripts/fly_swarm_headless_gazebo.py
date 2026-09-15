#!/usr/bin/env python3
"""Fly the 3-drone swarm mission against REAL headless Gazebo + PX4 SITL,
one ros_bridge_server per drone (deploy/k8s/px4-sim-deployment.yaml pods,
port-forwarded to 127.0.0.1:9010/9011/9012).

Same lockstep pattern as scripts/fleet_mission.py (wait for all drones
before starting the next step, land any drone that drops out), but with a
FIXED plan instead of scripts/mission_chatbot.py's LLM planning -- no
llama-server dependency. The plan/waypoints match scripts/demo_swarm_mission.py's
A/B/C mission exactly, so this is the same mission flown for real instead of
against the in-process DemoDroneAdapter stub.

Usage:
    python3 scripts/fly_swarm_headless_gazebo.py [port:label ...]

    (defaults to 9010:drone-a 9011:drone-b 9012:drone-c)
"""

import concurrent.futures as futures
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


def _land_all(targets):
    if not targets:
        return
    print("  landing dropped drones so they don't strand airborne:")
    with futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        jobs = {pool.submit(invoke, port, {"capability": "Land", "parameters": {}}): label for port, label in targets}
        for job in futures.as_completed(jobs):
            label = jobs[job]
            try:
                res = job.result()
                print(f"    {label}: {'landed' if res.get('success') else res.get('error')}")
            except Exception as exc:  # noqa: BLE001
                print(f"    {label}: land failed ({exc})")


def main() -> None:
    print(f"SWARM MISSION (real headless Gazebo/PX4) — {len(FLEET)} drones, fixed plan, lockstep")
    for port, label in FLEET:
        try:
            live = requests.get(f"http://127.0.0.1:{port}/live", timeout=8).json()
        except Exception as exc:  # noqa: BLE001
            live = f"unreachable: {exc}"
        print(f"  {label} (:{port}) -> {live}")

    max_steps = max(len(steps_for(label)) for _, label in FLEET)
    failed: set[str] = set()
    print()
    for idx in range(max_steps):
        with futures.ThreadPoolExecutor(max_workers=len(FLEET)) as pool:
            jobs = {}
            for port, label in FLEET:
                if label in failed:
                    continue
                step = steps_for(label)[idx]
                print(f"step {idx + 1}/{max_steps}: {label} -> {step['capability']}", flush=True)
                jobs[pool.submit(invoke, port, step)] = label
            for job in futures.as_completed(jobs):
                label = jobs[job]
                try:
                    res = job.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"    {label}: bridge error {exc}")
                    failed.add(label)
                    continue
                if res.get("success"):
                    extra = {k: v for k, v in res.items() if k not in ("success", "actor_id", "namespace")}
                    print(f"    {label}: ok {json.dumps(extra) if extra else ''}")
                else:
                    print(f"    {label}: FAILED {res.get('error', res)}")
                    failed.add(label)
        if len(failed) == len(FLEET):
            print("\nevery drone failed; aborting")
            return

    flew = [l for _, l in FLEET if l not in failed]
    print(f"\nSWARM MISSION COMPLETE — {len(flew)}/{len(FLEET)} drones: {', '.join(flew)}")
    if failed:
        print(f"  failed: {', '.join(sorted(failed))}")
        _land_all([(port, label) for port, label in FLEET if label in failed])


if __name__ == "__main__":
    main()
