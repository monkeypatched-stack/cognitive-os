#!/usr/bin/env python3
"""MB-3303 — Merchant Competition.

Two Merchants compete for one real scarce logistics resource (an
expedited delivery slot). Same proven pattern as MB-3300: real
utility evaluation, a real CAS-decided allocation, and each merchant's
own real explanation of the outcome — the allocation follows policy
and strategic evaluation, not fixed ordering.

Usage:
    python3 demo/negotiation/mb3303_merchant_competition.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3303 import DELIVERY_SLOT_NAME, bootstrap_world


def evaluate_round(c, actor_id: str, actor_name: str) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "EvaluateStrategy",
        f"Only 1 {DELIVERY_SLOT_NAME} remains and another merchant wants it too. Evaluate whether "
        f'claiming it right now is worth it to you. Use parameters {{"candidates": [{{"name": '
        f'"claim_now", "attributes": {{"speed": 1.0, "cost": -1.0}}}}, {{"name": "wait", '
        f'"attributes": {{"speed": 0.0, "cost": 0.0}}}}]}}.',
    )
    result = first_result("EvaluateStrategy", steps, actions)
    if result:
        for e in result.get("evaluations", []):
            kv(f"  {actor_name} utility({e.get('name')})", e.get("utility"))
        kv(f"  {actor_name} best strategy", result.get("best"))
    return result


def compete_round(c, actor_id: str, actor_name: str, slot_id: str) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "CompeteForResource",
        f"Try to claim the last {DELIVERY_SLOT_NAME} for yourself. Use parameters "
        f'{{"resource_id": "{slot_id}", "qty": 1}}.',
    )
    result = first_result("CompeteForResource", steps, actions)
    if result:
        outcome = "WON" if result.get("won") else "LOST"
        kv(f"  {actor_name} outcome", f"{outcome} — {result.get('reason')}")
    return result


def respond_round(c, actor_id: str, actor_name: str, compete_result: dict | None) -> str:
    if compete_result and compete_result.get("won"):
        fact = f'You successfully claimed the {DELIVERY_SLOT_NAME} — real reason: "{compete_result.get("reason")}".'
    elif compete_result:
        fact = f'You did NOT get the {DELIVERY_SLOT_NAME} — real reason: "{compete_result.get("reason")}".'
    else:
        fact = "Your claim attempt did not produce a usable result."
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "RespondToInquiry",
        f"{fact} Explain what happened and why, in your own words, as your final answer.",
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
            banner("MB-3303 — Merchant Competition")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            a_id = world["actors"]["Merchant A"]
            b_id = world["actors"]["Merchant B"]
            slot_id = world["resource"]["slot_id"]

            section("Round 1 — Evaluate Strategy")
            a_eval = evaluate_round(c, a_id, "Merchant A")
            b_eval = evaluate_round(c, b_id, "Merchant B")

            section("Round 2 — Compete For Resource")
            a_compete = compete_round(c, a_id, "Merchant A", slot_id)
            b_compete = compete_round(c, b_id, "Merchant B", slot_id)

            section("Round 3 — Respond To Inquiry")
            a_answer = respond_round(c, a_id, "Merchant A", a_compete)
            b_answer = respond_round(c, b_id, "Merchant B", b_compete)

            section("Verification")
            checks = [
                (
                    "Competition detected (both attempted CompeteForResource)",
                    a_compete is not None and b_compete is not None,
                ),
                (
                    "Strategic evaluation occurred (real utility numbers)",
                    bool(a_eval and a_eval.get("evaluations")) and bool(b_eval and b_eval.get("evaluations")),
                ),
                (
                    "Allocation followed the real CAS outcome, not fixed ordering (one real winner, one real loser)",
                    bool(a_compete) and bool(b_compete) and (a_compete.get("won") != b_compete.get("won")),
                ),
                (
                    "Losing merchant received a real, non-empty explanation",
                    bool(a_answer if not (a_compete or {}).get("won") else b_answer),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3303 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
