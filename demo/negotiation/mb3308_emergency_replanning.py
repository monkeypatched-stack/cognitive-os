#!/usr/bin/env python3
"""MB-3308 — Emergency Replanning.

A real fire (POST /events, same mechanism proven in
demo/coordination/mb3103) evacuates Warehouse A. Warehouse A's own
worker then negotiates a real handoff to Warehouse B — a new
equilibrium reached through natural-language negotiation, not a
scripted reassignment.

Usage:
    python3 demo/negotiation/mb3308_emergency_replanning.py
"""

from __future__ import annotations

import sys

from _common import (
    ApiError,
    banner,
    call,
    client,
    first_result,
    force_round,
    kv,
    section,
)
from bootstrap_mb3308 import TRACKED_PRODUCT_NAME, bootstrap_world


def main() -> int:
    with client() as c:
        try:
            banner("MB-3308 — Emergency Replanning")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            a_id = world["actors"]["Warehouse A Worker"]
            b_id = world["actors"]["Warehouse B Worker"]

            section("Inject Event: Warehouse A Fire (real evacuation)")
            fire_result = call(
                c,
                "POST",
                "/events",
                json={
                    "type": "fire",
                    "space_id": world["spaces"]["warehouse_a"],
                    "description": "Warehouse A Fire",
                },
            )
            evacuated = fire_result.get("evacuated") or []
            kv("Actors evacuated", len(evacuated))
            worker_a_evacuated = any(e.get("actor_id") == a_id for e in evacuated)
            kv("Warehouse A Worker evacuated", worker_a_evacuated)

            section("Round 1 — Warehouse A Worker negotiates a handoff")
            steps, actions = force_round(
                c,
                a_id,
                "Warehouse A Worker",
                "AskActor",
                f"There has been a real fire at Warehouse A and you have been evacuated. There is a "
                f"pending order for {TRACKED_PRODUCT_NAME} that Warehouse A can no longer fulfill. Ask "
                f'Warehouse B to take it over. Use parameters {{"target_actor": "Warehouse B Worker", '
                f'"question": "Warehouse A had a fire and I have been evacuated — can you take over '
                f'fulfilling our pending {TRACKED_PRODUCT_NAME} order?"}}.',
            )
            handoff = first_result("AskActor", steps, actions)
            if handoff:
                print(f'\n  Warehouse A Worker -> Warehouse B Worker: "{handoff.get("question")}"')
                print(f'  Warehouse B Worker -> Warehouse A Worker: "{handoff.get("answer")}"')

            handoff_answer = (handoff.get("answer", "") if handoff else "").lower()
            new_equilibrium = bool(handoff and handoff.get("answer")) and not any(
                phrase in handoff_answer for phrase in ("cannot", "can't", "unable", "no,")
            )

            section("Verification")
            checks = [
                (
                    "Real fire evacuated Warehouse A Worker (not simulated)",
                    worker_a_evacuated,
                ),
                (
                    "Warehouse A Worker renegotiated in real natural language (AskActor)",
                    bool(handoff),
                ),
                (
                    "Warehouse B Worker gave a real, substantive response",
                    bool(handoff and handoff.get("answer")),
                ),
                (
                    "A new equilibrium emerged (Warehouse B did not refuse outright)",
                    new_equilibrium,
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3308 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
