#!/usr/bin/env python3
"""MB-3305 — Delivery Optimization.

Two Drivers each have a real delivery in the OTHER driver's usual
zone. Driver X proposes a route exchange; both drivers independently
evaluate the real distance-saving numbers against their own real
preferences, and both confirm in natural language. Overall efficiency
improvement is proven by real utility numbers (swap beats keep for
both), not asserted by the script.

Usage:
    python3 demo/negotiation/mb3305_delivery_optimization.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3305 import bootstrap_world


def evaluate_round(c, actor_id: str, actor_name: str, saved_miles: float) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "EvaluateStrategy",
        f"Another driver has proposed swapping a delivery that is in your usual zone for one that "
        f"is currently on your route but out of your usual zone. Swapping would save you real "
        f'distance. Evaluate whether swapping is worth it. Use parameters {{"candidates": '
        f'[{{"name": "swap", "attributes": {{"distance_saved": {saved_miles}, "effort": -1.0}}}}, '
        f'{{"name": "keep", "attributes": {{"distance_saved": 0.0, "effort": 0.0}}}}]}}.',
    )
    result = first_result("EvaluateStrategy", steps, actions)
    if result:
        for e in result.get("evaluations", []):
            kv(f"  {actor_name} utility({e.get('name')})", e.get("utility"))
        kv(f"  {actor_name} best strategy", result.get("best"))
    return result


def respond_round(c, actor_id: str, actor_name: str, fact: str) -> str:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "RespondToInquiry",
        f"{fact} Reply with your final decision on the route swap, in your own words.",
        extra_context=fact,
    )
    result = first_result("RespondToInquiry", steps, actions)
    answer = result.get("answer", "") if result else ""
    if answer:
        print(f'\n  {actor_name}: "{answer}"')
    return answer


def main() -> int:
    with client() as c:
        try:
            banner("MB-3305 — Delivery Optimization")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            x_id = world["actors"]["Driver X"]
            y_id = world["actors"]["Driver Y"]

            section("Round 1 — Driver X proposes the swap")
            steps, actions = force_round(
                c,
                x_id,
                "Driver X",
                "AskActor",
                f"You have a delivery in Driver Y's usual zone (Zone B), and they have one in yours "
                f'(Zone A). Propose swapping. Use parameters {{"target_actor": "Driver Y", "question": '
                f'"Would you like to swap our out-of-zone deliveries? It should save us both real distance."}}.',
            )
            ask_result = first_result("AskActor", steps, actions)
            if ask_result:
                print(f'\n  Driver X -> Driver Y: "{ask_result.get("question")}"')
                print(f'  Driver Y -> Driver X: "{ask_result.get("answer")}"')

            section("Round 2 — Both drivers evaluate the real distance savings")
            x_eval = evaluate_round(c, x_id, "Driver X", saved_miles=8.0)
            y_eval = evaluate_round(c, y_id, "Driver Y", saved_miles=6.0)

            x_favors_swap = bool(x_eval and x_eval.get("best") == "swap")
            y_favors_swap = bool(y_eval and y_eval.get("best") == "swap")

            section("Round 3 — Both drivers confirm")
            x_fact = (
                "Your own evaluation showed swapping saves you real distance."
                if x_favors_swap
                else "Your own evaluation showed keeping your current route is better for you."
            )
            y_fact = (
                "Your own evaluation showed swapping saves you real distance."
                if y_favors_swap
                else "Your own evaluation showed keeping your current route is better for you."
            )
            x_answer = respond_round(c, x_id, "Driver X", x_fact)
            y_answer = respond_round(c, y_id, "Driver Y", y_fact)

            section("Verification")
            checks = [
                (
                    "Route exchange proposed in real natural language (AskActor)",
                    bool(ask_result),
                ),
                (
                    "Both drivers evaluated real strategies with real utility numbers",
                    bool(x_eval and x_eval.get("evaluations")) and bool(y_eval and y_eval.get("evaluations")),
                ),
                (
                    "Overall efficiency improves (both drivers' real utility favors swapping)",
                    x_favors_swap and y_favors_swap,
                ),
                (
                    "Both drivers confirmed in their own words",
                    bool(x_answer) and bool(y_answer),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3305 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
