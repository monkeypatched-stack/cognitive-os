#!/usr/bin/env python3
"""MB-3304 — Inventory Allocation.

Three Customers, one real unit of scarce inventory. Priority emerges
from real strategic reasoning, not fixed request ordering: each
customer evaluates real utility numbers from its own real preferences
first, and only the ones whose own strategy favors acting now actually
compete for the resource — the patient customer's own real utility
self-deprioritizes it, it doesn't get pushed aside by the script.

Usage:
    python3 demo/negotiation/mb3304_inventory_allocation.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3304 import TRACKED_PRODUCT_NAME, bootstrap_world

CUSTOMERS = ("Customer 1", "Customer 2", "Customer 3")


def evaluate_round(c, actor_id: str, actor_name: str) -> dict | None:
    steps, actions = force_round(
        c,
        actor_id,
        actor_name,
        "EvaluateStrategy",
        f"Only 1 {TRACKED_PRODUCT_NAME} remains and other customers have pending orders for it too. "
        f'Evaluate whether buying it right now is worth it to you. Use parameters {{"candidates": '
        f'[{{"name": "buy_now", "attributes": {{"speed": 1.0, "cost": -1.0}}}}, {{"name": "wait", '
        f'"attributes": {{"speed": 0.0, "cost": 0.0}}}}]}}.',
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


def respond_round(c, actor_id: str, actor_name: str, fact: str) -> str:
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
            banner("MB-3304 — Inventory Allocation")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            actor_ids = {name: world["actors"][name] for name in CUSTOMERS}
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("Round 1 — Evaluate Strategy (all three)")
            evals = {name: evaluate_round(c, actor_ids[name], name) for name in CUSTOMERS}

            wants_to_compete = [name for name in CUSTOMERS if evals[name] and evals[name].get("best") == "buy_now"]
            self_deprioritized = [name for name in CUSTOMERS if name not in wants_to_compete]

            section("Round 2 — Compete For Resource (only those whose strategy favors acting now)")
            competes = {}
            for name in wants_to_compete:
                competes[name] = compete_round(c, actor_ids[name], name, product_id)
            if not wants_to_compete:
                print("  (no customer's strategy favored acting now)")

            section("Round 3 — Respond To Inquiry (all three)")
            answers = {}
            for name in CUSTOMERS:
                if name in competes and competes[name]:
                    r = competes[name]
                    fact = (
                        f'You successfully reserved the {TRACKED_PRODUCT_NAME} — real reason: "{r.get("reason")}".'
                        if r.get("won")
                        else f'You did NOT get the {TRACKED_PRODUCT_NAME} — real reason: "{r.get("reason")}".'
                    )
                else:
                    fact = (
                        f"You evaluated your options and decided waiting was better for you than "
                        f"competing for the {TRACKED_PRODUCT_NAME} right now."
                    )
                answers[name] = respond_round(c, actor_ids[name], name, fact)

            section("Verification")
            winners = [name for name, r in competes.items() if r and r.get("won")]
            checks = [
                (
                    "All three customers evaluated real strategies with real utility numbers",
                    all(evals[name] and evals[name].get("evaluations") for name in CUSTOMERS),
                ),
                (
                    "Priority emerged from real strategy, not fixed ordering (at least one customer self-deprioritized)",
                    len(self_deprioritized) >= 1,
                ),
                ("Exactly one customer won the real scarce unit", len(winners) == 1),
                (
                    "Every customer received a real, non-empty explanation",
                    all(answers.get(name) for name in CUSTOMERS),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3304 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
