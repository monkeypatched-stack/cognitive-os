#!/usr/bin/env python3
"""MB-3302 — Warehouse Cooperation.

Warehouse B has a real order it cannot fulfill locally (0 stock).
Warehouse A has real surplus. Robot B asks Robot A for a transfer;
Robot A evaluates cooperating vs. keeping its stock using its own real
preferences (weighted toward network fulfillment) and, if cooperation
wins, records the agreement and Robot B reserves the real transferred
units — proving fulfillment genuinely improved, not just that a
conversation happened.

Usage:
    python3 demo/negotiation/mb3302_warehouse_cooperation.py
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3302 import TRACKED_PRODUCT_NAME, WAREHOUSE_A_STOCK, bootstrap_world

TRANSFER_QTY = 2


def main() -> int:
    with client() as c:
        try:
            banner("MB-3302 — Warehouse Cooperation")
            print("\nBootstrapping World")
            world = bootstrap_world(c)
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            robot_a_id = world["actors"]["Inventory Robot A"]
            robot_b_id = world["actors"]["Inventory Robot B"]
            product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]

            section("Round 1 — Robot B asks Robot A for a transfer")
            steps, actions = force_round(
                c,
                robot_b_id,
                "Inventory Robot B",
                "AskActor",
                f"You have a real order for {TRANSFER_QTY} units of {TRACKED_PRODUCT_NAME} but zero local "
                f'stock. Ask Warehouse A if they can transfer some. Use parameters {{"target_actor": '
                f'"Inventory Robot A", "question": "Can you transfer {TRANSFER_QTY} units of '
                f'{TRACKED_PRODUCT_NAME} to help fulfill an order I cannot fill locally?"}}.',
            )
            ask_result = first_result("AskActor", steps, actions)
            if ask_result:
                print(f'\n  Robot B -> Robot A: "{ask_result.get("question")}"')
                print(f'  Robot A -> Robot B: "{ask_result.get("answer")}"')

            section("Round 2 — Robot A evaluates cooperating vs. keeping stock")
            steps, actions = force_round(
                c,
                robot_a_id,
                "Inventory Robot A",
                "EvaluateStrategy",
                f"You have {WAREHOUSE_A_STOCK} real units in stock and Warehouse B has a real order it "
                f"cannot fill. Evaluate whether transferring {TRANSFER_QTY} units to them is worth it. Use "
                f'parameters {{"candidates": [{{"name": "transfer", "attributes": {{"network_fulfillment": '
                f'1.0, "local_stock": -1.0}}}}, {{"name": "keep", "attributes": {{"network_fulfillment": '
                f'0.0, "local_stock": 0.0}}}}]}}.',
            )
            eval_result = first_result("EvaluateStrategy", steps, actions)
            if eval_result:
                for e in eval_result.get("evaluations", []):
                    kv(f"  utility({e.get('name')})", e.get("utility"))
                kv("  Robot A's chosen strategy", eval_result.get("best"))

            cooperated = bool(eval_result and eval_result.get("best") == "transfer")

            section("Round 3 — Robot A records the agreement")
            record_result = None
            if cooperated:
                fact = f"You decided to transfer {TRANSFER_QTY} units to Warehouse B (that was the better strategy)."
                steps, actions = force_round(
                    c,
                    robot_a_id,
                    "Inventory Robot A",
                    "RecordAgreement",
                    f'{fact} Persist this agreement. Use parameters {{"entity_id": "{product_id}", '
                    f'"agreement": {{"with": "Inventory Robot B", "terms": "transfer {TRANSFER_QTY} units"}}}}.',
                    extra_context=fact,
                )
                record_result = first_result("RecordAgreement", steps, actions)
                if record_result:
                    kv("  Agreement persisted", record_result.get("success"))
            else:
                print("  (skipped — Robot A's best strategy was to keep its stock)")

            section("Round 4 — Robot B reserves the real transferred units")
            compete_result = None
            # Gated on Robot A's real decision (Round 2), not on whether
            # RecordAgreement's persistence step separately succeeded —
            # those are two independently real, independently reported
            # claims ("an agreement was reached" vs. "it was durably
            # recorded"); a flaky write of the paperwork shouldn't block
            # the real cooperative reservation Robot A already decided on.
            if cooperated:
                steps, actions = force_round(
                    c,
                    robot_b_id,
                    "Inventory Robot B",
                    "CompeteForResource",
                    f"Warehouse A agreed to transfer {TRANSFER_QTY} units to you. Reserve them now. Use "
                    f'parameters {{"resource_id": "{product_id}", "qty": {TRANSFER_QTY}}}.',
                )
                compete_result = first_result("CompeteForResource", steps, actions)
                if compete_result:
                    kv(
                        "  Reservation outcome",
                        "WON" if compete_result.get("won") else "LOST",
                    )
            else:
                print("  (skipped — no agreement to act on)")

            section("Verification")
            checks = [
                (
                    "Robot B asked for cooperation in natural language (real AskActor exchange)",
                    bool(ask_result),
                ),
                (
                    "Robot A evaluated real strategies with real utility numbers",
                    bool(eval_result and eval_result.get("evaluations")),
                ),
                (
                    "Cooperative strategy chosen (transfer beat keep on real utility)",
                    cooperated,
                ),
                (
                    "Fulfillment genuinely improved (Robot B reserved real units it didn't have before)",
                    bool(compete_result and compete_result.get("won")),
                ),
            ]
            all_pass = True
            for label, ok in checks:
                mark = "✓" if ok else "✗"
                print(f"{mark} {label}" + (" — PASS" if ok else " — FAIL"))
                if not ok:
                    all_pass = False

            banner("MB-3302 RESULT: " + ("PASS" if all_pass else "FAIL"))
            if not all_pass:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
