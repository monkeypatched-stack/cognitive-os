"""One mission, flown by the whole fleet in lockstep.

usage: fleet_mission.py "<prompt>" [port:label ...]

Planned once by the LLM, then every drone executes step N together and the
fleet waits for all of them before starting step N+1. That lockstep matters:
run the drones as independent missions and they drift apart in time, so a
"formation" ends up with one vehicle cruising while another is still
climbing. Waiting on the slowest keeps the shape.

Each drone's own spawn point is its PX4 local-frame origin, so the identical
relative plan holds their spawn separation for the whole flight -- no
per-drone offsets needed, and "return home" sends each to its own pad.
"""

import concurrent.futures as futures
import json
import os
import sys

import requests

# mission_chatbot.py is a sibling of this script -- resolve relative to this
# file's own location, not a hardcoded absolute path (was
# "/home/varun/cognitive-os/.claude/worktrees/isaac-gui-lowres/scripts",
# a stale, other-machine path into a duplicate worktree copy that doesn't
# exist on a normal checkout of this repo).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mission_chatbot as mc  # noqa: E402

PROMPT = sys.argv[1]
FLEET = [a.split(":", 1) for a in (sys.argv[2:] or ["9010:drone1", "9011:drone2"])]


def plan_once(prompt):
    convo = [
        {"role": "system", "content": mc._PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    reply = mc._chat(convo, stream=False).json()["choices"][0]["message"]["content"]
    raw = mc._extract_plan(reply)
    if raw is None:
        print("model did not return a flight plan:\n" + (reply or "")[:400])
        raise SystemExit(1)
    steps, notes = mc._validate(
        raw,
        auto_land=True,
        wants_return=bool(mc._RETURN_RE.search(prompt)),
        user_coords=mc._user_coords(prompt),
    )
    if not steps:
        print("plan did not validate: " + "; ".join(notes))
        raise SystemExit(1)
    return steps, notes


def invoke(port, step):
    r = requests.post(
        f"http://127.0.0.1:{port}/invoke",
        json={"capability": step["capability"], "parameters": step["parameters"]},
        timeout=240,
    )
    return r.json()


def _land_all(targets):
    """Land the given drones, so an abort does not strand them airborne."""
    if not targets:
        return
    print("  landing dropped drones so the next mission starts clean:")
    with futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        jobs = {pool.submit(invoke, port, {"capability": "Land", "parameters": {}}): label for port, label in targets}
        for job in futures.as_completed(jobs):
            label = jobs[job]
            try:
                res = job.result()
                print(f"    {label}: {'landed' if res.get('success') else res.get('error')}")
            except Exception as exc:  # noqa: BLE001
                print(f"    {label}: land failed ({exc})")


def main():
    steps, notes = plan_once(PROMPT)

    print(f"FLEET MISSION — {len(FLEET)} drones, planned once, flown in lockstep")
    print(f"  prompt: {PROMPT}")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {mc._describe(s)}")
    if notes:
        print("  notes: " + "; ".join(notes))

    for port, label in FLEET:
        try:
            live = requests.get(f"http://127.0.0.1:{port}/live", timeout=8).json()
        except Exception as exc:  # noqa: BLE001
            live = f"unreachable: {exc}"
        print(f"  {label} (:{port}) -> {live}")

    print()
    failed = set()
    for idx, step in enumerate(steps, 1):
        label_txt = mc._describe(step)
        print(f"step {idx}/{len(steps)}: {label_txt}", flush=True)
        with futures.ThreadPoolExecutor(max_workers=len(FLEET)) as pool:
            jobs = {pool.submit(invoke, port, step): label for port, label in FLEET if label not in failed}
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
            print("\nevery drone failed; aborting the fleet mission")
            _land_all(FLEET)
            return

    flew = [l for _, l in FLEET if l not in failed]
    print(f"\nFLEET MISSION COMPLETE — {len(flew)}/{len(FLEET)} drones: {', '.join(flew)}")
    if failed:
        print(f"  failed: {', '.join(sorted(failed))}")
        # Anything that dropped out mid-mission is still airborne, holding
        # its last setpoint. Left there it poisons the NEXT mission: Takeoff
        # assumes a ground start, so commanding 5m at a vehicle already at
        # 5.8m cannot converge, and killing the bridge instead cuts the
        # setpoint stream and lets PX4's offboard-loss failsafe drift it
        # further. Put them down.
        _land_all([(port, label) for port, label in FLEET if label in failed])


if __name__ == "__main__":
    main()
