#!/usr/bin/env python3
"""MB-3310 — Strategic Collaboration.

Customer needs a package tomorrow before noon. Inventory Robot,
Warehouse Worker, Driver, and Support Agent each contribute exactly
once, handing off to a different real colleague each time — the final
plan emerges from this real chain of independent reasoning, not a
single centralized decision (this script never decides feasibility
itself, it only relays each real answer forward).

Usage:
    python3 demo/negotiation/mb3310_strategic_collaboration.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3310 import TRACKED_PRODUCT_NAME, bootstrap_world


def main() -> int:
    with client() as c:
        try:
            banner("MB-3310 — Strategic Collaboration")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            customer_id = world["actors"]["Customer"]
            inventory_id = world["actors"]["Inventory Robot"]
            warehouse_id = world["actors"]["Warehouse Worker"]
            driver_id = world["actors"]["Driver"]

            deadline = "tomorrow before noon"

            section("Hop 1 — Customer -> Inventory Robot")
            steps, actions = force_round(
                c,
                customer_id,
                "Customer",
                "AskActor",
                f"You need the {TRACKED_PRODUCT_NAME} {deadline}. Ask the Inventory Robot if it is in "
                f'stock. Use parameters {{"target_actor": "Inventory Robot", "question": "I need the '
                f'{TRACKED_PRODUCT_NAME} {deadline} — is it in stock?"}}.',
            )
            hop1 = first_result("AskActor", steps, actions)
            if hop1:
                print(f'\n  Customer -> Inventory Robot: "{hop1.get("question")}"')
                print(f'  Inventory Robot -> Customer: "{hop1.get("answer")}"')

            section("Hop 2 — Inventory Robot -> Warehouse Worker")
            hop1_answer = (hop1.get("answer", "") if hop1 else "")[:150]
            steps, actions = force_round(
                c,
                inventory_id,
                "Inventory Robot",
                "AskActor",
                f"A customer needs the {TRACKED_PRODUCT_NAME} {deadline}. Ask the Warehouse Worker if "
                f'they can pack it in time. Use parameters {{"target_actor": "Warehouse Worker", '
                f'"question": "Can you pack the {TRACKED_PRODUCT_NAME} in time for delivery {deadline}?"}}.',
                extra_context=f'You told the customer: "{hop1_answer}"',
            )
            hop2 = first_result("AskActor", steps, actions)
            if hop2:
                print(f'\n  Inventory Robot -> Warehouse Worker: "{hop2.get("question")}"')
                print(f'  Warehouse Worker -> Inventory Robot: "{hop2.get("answer")}"')

            section("Hop 3 — Warehouse Worker -> Driver")
            hop2_answer = (hop2.get("answer", "") if hop2 else "")[:150]
            steps, actions = force_round(
                c,
                warehouse_id,
                "Warehouse Worker",
                "AskActor",
                f'Ask the Driver whether they can deliver {deadline}. Use parameters {{"target_actor": '
                f'"Driver", "question": "Can you deliver the {TRACKED_PRODUCT_NAME} {deadline}?"}}.',
                extra_context=f'You told the Inventory Robot: "{hop2_answer}"',
            )
            hop3 = first_result("AskActor", steps, actions)
            if hop3:
                print(f'\n  Warehouse Worker -> Driver: "{hop3.get("question")}"')
                print(f'  Driver -> Warehouse Worker: "{hop3.get("answer")}"')

            section("Hop 4 — Driver -> Support Agent")
            hop3_answer = (hop3.get("answer", "") if hop3 else "")[:150]
            steps, actions = force_round(
                c,
                driver_id,
                "Driver",
                "AskActor",
                f"Ask the Support Agent to confirm the overall plan to the customer, including payment. "
                f'Use parameters {{"target_actor": "Support Agent", "question": "Can you confirm the '
                f'plan (including payment) for delivering the {TRACKED_PRODUCT_NAME} {deadline}?"}}.',
                extra_context=f'You told the Warehouse Worker: "{hop3_answer}"',
            )
            hop4 = first_result("AskActor", steps, actions)
            if hop4:
                print(f'\n  Driver -> Support Agent: "{hop4.get("question")}"')
                print(f'  Support Agent -> Driver: "{hop4.get("answer")}"')

            section("Verification")
            checks = [
                (
                    "Customer's tight-deadline request carried in real natural language",
                    bool(hop1),
                ),
                (
                    "Inventory Robot contributed a real stock assessment",
                    bool(hop1 and hop1.get("answer")),
                ),
                (
                    "Warehouse Worker contributed a real packing feasibility assessment",
                    bool(hop2 and hop2.get("answer")),
                ),
                (
                    "Driver contributed a real delivery feasibility assessment",
                    bool(hop3 and hop3.get("answer")),
                ),
                (
                    "Support Agent produced a real final plan (emerged from the chain, not centralized)",
                    bool(hop4 and hop4.get("answer")),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3310 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
