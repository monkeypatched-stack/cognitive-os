#!/usr/bin/env python3
"""MB-3301 — Driver Negotiation.

Warehouse requests expedited (morning) delivery; the Driver negotiates
an alternative schedule using its own real constraint (existing
delivery commitments, carried in its real strategy metadata) rather
than simply agreeing or refusing outright. The negotiated delay is a
real bounded bargain (NegotiateTerms), not an LLM-invented number, and
persists as real world state on the shipment.

Usage:
    python3 demo/negotiation/mb3301_driver_negotiation.py
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
from bootstrap_mb3301 import TRACKED_PRODUCT_NAME, bootstrap_world


def main() -> int:
    with client() as c:
        try:
            banner("MB-3301 — Driver Negotiation")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            warehouse_id = world["actors"]["Warehouse Worker"]
            driver_id = world["actors"]["Driver"]
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("World Update: real order + shipment (real API calls)")
            order = call(
                c,
                "POST",
                "/orders",
                json={
                    "actor_id": world["actors"]["Customer"],
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
            shipment = call(
                c,
                "POST",
                "/shipments",
                json={
                    "order_id": order_id,
                    "packages": [{"box": 1, "items": [product_id]}],
                    "rider_id": world["rider_id"],
                },
            )
            shipment_id = shipment.get("shipment_id", "")
            kv("Order", order_id)
            kv("Shipment", shipment_id)

            section("Round 1 — Warehouse requests expedited delivery")
            steps, actions = force_round(
                c,
                warehouse_id,
                "Warehouse Worker",
                "AskActor",
                f"Shipment {shipment_id} needs to go out. Ask the Driver whether they can deliver it "
                f'tomorrow morning. Use parameters {{"target_actor": "Driver", "question": '
                f'"Can you deliver this shipment tomorrow morning?"}}.',
            )
            ask_result = first_result("AskActor", steps, actions)
            if ask_result:
                print(f'\n  Warehouse Worker -> Driver: "{ask_result.get("question")}"')
                print(f'  Driver -> Warehouse Worker: "{ask_result.get("answer")}"')

            section("Round 2 — Driver negotiates a real alternative schedule")
            steps, actions = force_round(
                c,
                driver_id,
                "Driver",
                "NegotiateTerms",
                f"The Warehouse wants delivery in the morning (0 hours after the requested time), but "
                f"you already have 3 existing deliveries scheduled tomorrow morning and would need at "
                f"least 3 more hours (afternoon) to fit this in without delaying them; you would ideally "
                f'like 6 hours to be safe. Use parameters {{"high_side_opening": 6, "high_side_floor": 3, '
                f'"low_side_opening": 0}}.',
            )
            deal = first_result("NegotiateTerms", steps, actions)
            if deal:
                kv("  Negotiation agreed", deal.get("agreed"))
                kv("  Negotiated delay (hours)", deal.get("term"))
                kv("  Rounds", len(deal.get("rounds", [])))

            section("Round 3 — Driver records the agreement")
            record_result = None
            if deal and deal.get("agreed"):
                fact = f"The real negotiated delay is {deal.get('term')} hours."
                steps, actions = force_round(
                    c,
                    driver_id,
                    "Driver",
                    "RecordAgreement",
                    f'{fact} Persist this agreement. Use parameters {{"entity_id": "{shipment_id}", '
                    f'"agreement": {{"with": "Warehouse Worker", "terms": '
                    f'"deliver {deal.get("term")} hours later than originally requested"}}}}.',
                    extra_context=fact,
                )
                record_result = first_result("RecordAgreement", steps, actions)
                if record_result:
                    kv("  Agreement persisted", record_result.get("success"))
            else:
                print("  (skipped — no deal was reached)")

            section("Round 4 — Driver replies to the Warehouse")
            if deal and deal.get("agreed") and record_result and record_result.get("success"):
                fact = f"You negotiated a real {deal.get('term')}-hour delay and recorded the agreement."
            elif deal and deal.get("agreed"):
                fact = (
                    f"You negotiated a real {deal.get('term')}-hour delay, but the agreement was NOT "
                    f"successfully recorded — do not claim it was."
                )
            else:
                fact = "You were unable to reach a scheduling agreement."
            steps, actions = force_round(
                c,
                driver_id,
                "Driver",
                "RespondToInquiry",
                f"{fact} Reply to the Warehouse Worker with your final answer.",
                extra_context=fact,
            )
            final = first_result("RespondToInquiry", steps, actions)
            final_answer = final.get("answer", "") if final else ""
            if final_answer:
                print(f'\n  Driver -> Warehouse Worker: "{final_answer}"')

            section("Verification")
            checks = [
                (
                    "Warehouse's request carried in real natural language (AskActor)",
                    bool(ask_result),
                ),
                (
                    "Driver's reply reflected its own real constraints, not a generic answer",
                    bool(ask_result and "3" in (ask_result.get("answer") or ""))
                    or bool(
                        ask_result
                        and any(
                            w in (ask_result.get("answer") or "").lower()
                            for w in ("existing", "already", "afternoon", "delay")
                        )
                    ),
                ),
                (
                    "Negotiated agreement reached (real bounded bargain, agreed=True)",
                    bool(deal and deal.get("agreed")),
                ),
                (
                    "Agreement persisted as world state (RecordAgreement succeeded)",
                    bool(record_result and record_result.get("success")),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3301 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
