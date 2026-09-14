#!/usr/bin/env python3
"""MB-3306 — Customer Negotiation.

Customer asks the Merchant for a discount in exchange for accepting
delivery next week. The Merchant reasons, negotiates a real bounded
price (NegotiatePrice — never an LLM-invented number, mathematically
bounded by the real listed price and the real seller floor), replies,
and the agreement is persisted as real world state.

Usage:
    python3 demo/negotiation/mb3306_customer_negotiation.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3306 import (
    TRACKED_PRODUCT_FLOOR,
    TRACKED_PRODUCT_NAME,
    TRACKED_PRODUCT_PRICE,
    bootstrap_world,
)


def main() -> int:
    with client() as c:
        try:
            banner("MB-3306 — Customer Negotiation")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            customer_id = world["actors"]["Customer"]
            merchant_id = world["actors"]["Merchant"]
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("Round 1 — Customer asks for a discount")
            steps, actions = force_round(
                c,
                customer_id,
                "Customer",
                "AskActor",
                f"You want the {TRACKED_PRODUCT_NAME} (listed at ${TRACKED_PRODUCT_PRICE}) but think you "
                f"could get a discount if you accept delivery next week instead of today. Ask the "
                f'Merchant about this. Use parameters {{"target_actor": "Merchant", "question": '
                f'"Can I receive a discount if I accept delivery next week?"}}.',
            )
            ask_result = first_result("AskActor", steps, actions)
            if ask_result:
                print(f'\n  Customer -> Merchant: "{ask_result.get("question")}"')
                print(f'  Merchant -> Customer: "{ask_result.get("answer")}"')

            section("Round 2 — Merchant negotiates a real price")
            steps, actions = force_round(
                c,
                merchant_id,
                "Merchant",
                "NegotiatePrice",
                f"A customer offered to accept delivery next week (instead of today) in exchange for "
                f"a discount on the {TRACKED_PRODUCT_NAME}, implying they would want to pay around $52.00 "
                f'instead of the full listed price. Use parameters {{"listed_price": {TRACKED_PRODUCT_PRICE}, '
                f'"min_seller_price": {TRACKED_PRODUCT_FLOOR}, "buyer_target_price": 52.00}}.',
            )
            deal = first_result("NegotiatePrice", steps, actions)
            if deal:
                kv("  Negotiation agreed", deal.get("agreed"))
                kv("  Negotiated price", deal.get("price"))
                kv("  Rounds", len(deal.get("rounds", [])))

            section("Round 3 — Merchant records the agreement")
            if deal and deal.get("agreed"):
                agreement_fact = f"The real negotiated price is ${deal.get('price')}."
                steps, actions = force_round(
                    c,
                    merchant_id,
                    "Merchant",
                    "RecordAgreement",
                    f'{agreement_fact} Persist this agreement. Use parameters {{"entity_id": '
                    f'"{product_id}", "agreement": {{"with": "Customer", "terms": '
                    f'"${deal.get("price")} for next-week delivery instead of ${TRACKED_PRODUCT_PRICE} today"}}}}.',
                    extra_context=agreement_fact,
                )
                record_result = first_result("RecordAgreement", steps, actions)
                if record_result:
                    kv("  Agreement persisted", record_result.get("success"))
            else:
                record_result = None
                print("  (skipped — no deal was reached)")

            section("Round 4 — Merchant replies to the Customer")
            fact = (
                f"You negotiated a real price of ${deal.get('price')} for next-week delivery and recorded "
                f"the agreement."
                if (deal and deal.get("agreed"))
                else "You were unable to reach a deal within the negotiation bounds."
            )
            steps, actions = force_round(
                c,
                merchant_id,
                "Merchant",
                "RespondToInquiry",
                f"{fact} Reply to the customer with your final answer.",
                extra_context=fact,
            )
            final = first_result("RespondToInquiry", steps, actions)
            final_answer = final.get("answer", "") if final else ""
            if final_answer:
                print(f'\n  Merchant -> Customer: "{final_answer}"')

            section("Verification")
            checks = [
                (
                    "Customer negotiated using natural language (real AskActor exchange)",
                    bool(ask_result and ask_result.get("answer")),
                ),
                (
                    "Merchant reasoned about a real bounded price (NegotiatePrice ran)",
                    bool(deal),
                ),
                (
                    "Agreement stored as persistent world state (RecordAgreement succeeded)",
                    bool(record_result and record_result.get("success")),
                ),
                (
                    "Merchant delivered a real final reply to the customer",
                    bool(final_answer),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3306 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
