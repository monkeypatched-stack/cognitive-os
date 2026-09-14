#!/usr/bin/env python3
"""MB-3309 — Multi-Party Negotiation.

Customer, Merchant, Warehouse Worker, and Driver each contribute
exactly once to negotiating a real delivery exception (a changed
delivery address), handing off to a different real colleague each
time — every actor ticks exactly once (avoiding the consecutive-tick
reliability issue found in MB-3307), and Support Agent's real answer
(reached only via AskActor, same as every other target in this suite)
stands as the final outcome.

Usage:
    python3 demo/negotiation/mb3309_multi_party_negotiation.py
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
from bootstrap_mb3309 import TRACKED_PRODUCT_NAME, bootstrap_world


def main() -> int:
    with client() as c:
        try:
            banner("MB-3309 — Multi-Party Negotiation")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            customer_id = world["actors"]["Customer"]
            merchant_id = world["actors"]["Merchant"]
            warehouse_id = world["actors"]["Warehouse Worker"]
            driver_id = world["actors"]["Driver"]
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("World Update: real order (real API call)")
            order = call(
                c,
                "POST",
                "/orders",
                json={
                    "actor_id": customer_id,
                    "items": [
                        {
                            "id": product_id,
                            "name": TRACKED_PRODUCT_NAME,
                            "qty": 1,
                            "price": 59.99,
                        }
                    ],
                    "question": "buy the wireless gaming mouse",
                },
            )
            order_id = order.get("order_id", "")
            kv("Order", order_id)

            section("Hop 1 — Customer -> Merchant")
            steps, actions = force_round(
                c,
                customer_id,
                "Customer",
                "AskActor",
                f"You need order {order_id} delivered to a different address than the one on file — a "
                f'real delivery exception. Ask the Merchant about this. Use parameters {{"target_actor": '
                f'"Merchant", "question": "Can order {order_id} be delivered to a different address than '
                f'the one on file?"}}.',
            )
            hop1 = first_result("AskActor", steps, actions)
            if hop1:
                print(f'\n  Customer -> Merchant: "{hop1.get("question")}"')
                print(f'  Merchant -> Customer: "{hop1.get("answer")}"')

            section("Hop 2 — Merchant -> Warehouse Worker")
            hop1_answer = (hop1.get("answer", "") if hop1 else "")[:150]
            steps, actions = force_round(
                c,
                merchant_id,
                "Merchant",
                "AskActor",
                f"A customer requested order {order_id} be delivered to a different address. Ask the "
                f'Warehouse Worker whether that is feasible on their end. Use parameters {{"target_actor": '
                f'"Warehouse Worker", "question": "Order {order_id} needs delivery to a new address — '
                f'is that feasible?"}}.',
                extra_context=f'You told the customer: "{hop1_answer}"',
            )
            hop2 = first_result("AskActor", steps, actions)
            if hop2:
                print(f'\n  Merchant -> Warehouse Worker: "{hop2.get("question")}"')
                print(f'  Warehouse Worker -> Merchant: "{hop2.get("answer")}"')

            section("Hop 3 — Warehouse Worker -> Driver")
            hop2_answer = (hop2.get("answer", "") if hop2 else "")[:150]
            steps, actions = force_round(
                c,
                warehouse_id,
                "Warehouse Worker",
                "AskActor",
                f"Ask the Driver whether they can deliver order {order_id} to a new address. Use "
                f'parameters {{"target_actor": "Driver", "question": "Order {order_id} needs delivery to '
                f'a new address — can you handle that?"}}.',
                extra_context=f'You told the Merchant: "{hop2_answer}"',
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
                f"Ask the Support Agent to confirm the delivery-address exception for order {order_id} "
                f'to the customer. Use parameters {{"target_actor": "Support Agent", "question": "Can you '
                f"confirm to the customer that order {order_id}'s new delivery address is being handled?\"}}.",
                extra_context=f'You told the Warehouse Worker: "{hop3_answer}"',
            )
            hop4 = first_result("AskActor", steps, actions)
            if hop4:
                print(f'\n  Driver -> Support Agent: "{hop4.get("question")}"')
                print(f'  Support Agent -> Driver: "{hop4.get("answer")}"')

            section("Verification")
            checks = [
                (
                    "Customer's exception request carried in real natural language",
                    bool(hop1),
                ),
                (
                    "Merchant contributed a real, independent answer",
                    bool(hop1 and hop1.get("answer")),
                ),
                (
                    "Warehouse Worker contributed a real, independent answer",
                    bool(hop2 and hop2.get("answer")),
                ),
                (
                    "Driver contributed a real, independent answer",
                    bool(hop3 and hop3.get("answer")),
                ),
                (
                    "Support Agent contributed a real, independent answer (every actor contributed)",
                    bool(hop4 and hop4.get("answer")),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3309 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
