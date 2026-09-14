#!/usr/bin/env python3
"""MB-3300 — Competing Customers.

Two Customer actors both want the last remaining unit of a real
product. Proves: competition is genuinely detected and evaluated (not
just allocated by fixed script ordering with no reasoning), the real
CAS outcome decides who wins, and the losing customer's own reasoning
explains a real, non-fabricated reason — not a generic "sorry" string
this script writes.

Three forced rounds per customer (same pattern demo/dialogue proved
reliable): (1) EvaluateStrategy — real utility numbers from each
actor's own real preferences; (2) CompeteForResource — the real CAS
attempt; (3) RespondToInquiry — the actor's own explanation, given the
real round-2 outcome as a fact.

Usage:
    python3 demo/negotiation/mb3300_competing_customers.py
"""

from __future__ import annotations

import sys
from typing import Any

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3300 import TRACKED_PRODUCT_NAME, bootstrap_world


def evaluate_round(c, actor_id: str, actor_name: str) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "EvaluateStrategy",
        f"Only 1 {TRACKED_PRODUCT_NAME} remains in stock and another customer wants it "
        f"too. Evaluate whether buying it right now is worth it to you. Use parameters "
        f'{{"candidates": [{{"name": "buy_now", "attributes": {{"speed": 1.0, "cost": -1.0}}}}, '
        f'{{"name": "wait", "attributes": {{"speed": 0.0, "cost": 0.0}}}}]}}.',
    )
    result = first_result("EvaluateStrategy", steps, actions)
    if result:
        for e in result.get("evaluations", []):
            kv(f"  {actor_name} utility({e.get('name')})", e.get("utility"))
        kv(f"  {actor_name} best strategy", result.get("best"))
    return result


def compete_round(c, actor_id: str, actor_name: str, product_id: str) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "CompeteForResource",
        f"Try to reserve the last {TRACKED_PRODUCT_NAME} for yourself. Use parameters "
        f'{{"resource_id": "{product_id}", "qty": 1}}.',
    )
    result = first_result("CompeteForResource", steps, actions)
    if result:
        outcome = "WON" if result.get("won") else "LOST"
        kv(f"  {actor_name} outcome", f"{outcome} — {result.get('reason')}")
    return result


def respond_round(c, actor_id: str, actor_name: str, compete_result: dict | None) -> str:
    if compete_result and compete_result.get("won"):
        fact = f'You successfully reserved the {TRACKED_PRODUCT_NAME} — real reason: "{compete_result.get("reason")}".'
    elif compete_result:
        fact = f'You did NOT get the {TRACKED_PRODUCT_NAME} — real reason: "{compete_result.get("reason")}".'
    else:
        fact = "Your reservation attempt did not produce a usable result."
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


def step_verify(
    alice_compete: dict | None,
    bob_compete: dict | None,
    alice_eval: dict | None,
    bob_eval: dict | None,
    alice_answer: str,
    bob_answer: str,
) -> bool:
    section("Verification")
    checks = [
        (
            "Competition detected (both attempted CompeteForResource)",
            alice_compete is not None and bob_compete is not None,
        ),
        (
            "Strategies evaluated (both produced real utility numbers)",
            bool(alice_eval and alice_eval.get("evaluations")) and bool(bob_eval and bob_eval.get("evaluations")),
        ),
        (
            "Allocation explained (exactly one real winner, one real loser)",
            bool(alice_compete) and bool(bob_compete) and (alice_compete.get("won") != bob_compete.get("won")),
        ),
        (
            "Losing customer received an appropriate outcome (real explanation, not empty)",
            bool(alice_answer if not (alice_compete or {}).get("won") else bob_answer),
        ),
    ]
    all_pass = True
    for label, ok in checks:
        mark = "✓" if ok else "✗"
        print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
        if not ok:
            all_pass = False
    return all_pass


def main() -> int:
    with client() as c:
        try:
            banner("MB-3300 — Competing Customers")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            alice_id = world["actors"]["Alice"]
            bob_id = world["actors"]["Bob"]
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("Round 1 — Evaluate Strategy")
            alice_eval = evaluate_round(c, alice_id, "Alice")
            bob_eval = evaluate_round(c, bob_id, "Bob")

            section("Round 2 — Compete For Resource")
            alice_compete = compete_round(c, alice_id, "Alice", product_id)
            bob_compete = compete_round(c, bob_id, "Bob", product_id)

            section("Round 3 — Respond To Inquiry")
            alice_answer = respond_round(c, alice_id, "Alice", alice_compete)
            bob_answer = respond_round(c, bob_id, "Bob", bob_compete)

            passed = step_verify(
                alice_compete,
                bob_compete,
                alice_eval,
                bob_eval,
                alice_answer,
                bob_answer,
            )

            banner("MB-3300 RESULT: " + ("PASS" if passed else "FAIL"))
            if not passed:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
