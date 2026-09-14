#!/usr/bin/env python3
"""Natural Language Actor-to-Actor Communication.

Proves CognitiveOS actors can hold a real conversation, not just
execute routed workflows: every question below is answered by
POST /actors/{id}/ask, which runs AnswerQuestionCapability — real KG
facts + a real LLM call (get_backend().complete()), never a template,
never a scripted string. Nothing in this script hardcodes a reply.

Where one actor's answer needs to inform another actor's turn (the
whole point of a conversation), THIS SCRIPT does the relay — it reads
actor A's real natural-language answer and passes it, as plain text,
inside actor B's next question. There is no hidden inter-actor
channel: an external client could reproduce this exact flow with five
curl calls.

Five demonstrations, run against one shared, evolving world:
  1. Customer <-> Warehouse Worker      (pre-order: stock/timing)
  2. Warehouse Worker <-> Driver        (post-order: delivery)
  3. Driver <-> Warehouse Worker        (negotiation)
  4. Customer -> Support Agent -> Warehouse Worker -> Support Agent -> Customer
  5. Customer -> Warehouse -> Inventory -> Driver -> Support -> Customer
     (multi-actor collaboration chain)

Usage:
    python3 demo/conversation/run_conversation.py
"""

from __future__ import annotations

import datetime
import sys
import time
from typing import Any

from bootstrap import ApiError, TRACKED_PRODUCT_NAME, _call, _client, bootstrap_world

WIDTH = 56


def banner(title: str) -> None:
    print("\n" + "=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def section(title: str) -> None:
    print("\n" + "-" * WIDTH)
    print(title)
    print("-" * WIDTH)


def _ts(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def ask(
    client,
    world: dict[str, Any],
    to_name: str,
    question: str,
    from_name: str | None = None,
) -> dict[str, Any]:
    """The only function in this script that talks to an actor.
    Everything else is either real world-state mutation (bootstrap.py
    calls, order/shipment lifecycle) or relaying a PREVIOUS real answer
    into the next question — never a scripted reply."""
    to_actor_id = world["actors"][to_name]
    from_actor_id = world["actors"].get(from_name) if from_name else None
    result = _call(
        client,
        "POST",
        f"/actors/{to_actor_id}/ask",
        json={
            "question": question,
            "from_actor_id": from_actor_id,
            "from_actor_name": from_name,
        },
    )

    society_name = result.get("society_name", "?")
    print(f"\n{from_name or '(orchestrator)'}")
    print(f"    ↓")
    print(f"{society_name}")
    print(f"    ↓")
    print(f"{to_name}")
    print(f"  Sender ....... {from_name or '(orchestrator)'}")
    print(f"  Recipient .... {to_name}")
    print(f"  Society ...... {society_name}")
    print(f"  Timestamp .... {_ts(result.get('timestamp', time.time()))}")
    print(f'\n  "{question}"')
    print(f'\n  -> {to_name}: "{result.get("answer", "")}"')
    return result


# ── World progression between conversation turns (real API calls) ───────


def place_real_order(client, world: dict[str, Any]) -> str:
    product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]
    order = _call(
        client,
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
    return order.get("order_id", "")


def pack_and_ship(client, world: dict[str, Any], order_id: str) -> str:
    product_id = world["commerce"]["products"][TRACKED_PRODUCT_NAME]
    shipment = _call(
        client,
        "POST",
        "/shipments",
        json={
            "order_id": order_id,
            "packages": [{"box": 1, "items": [product_id]}],
            "rider_id": world["rider_id"],
        },
    )
    return shipment.get("shipment_id", "")


# ── Demonstrations ────────────────────────────────────────────────────


def demo_1_customer_warehouse(client, world: dict[str, Any]) -> None:
    banner("Demonstration 1 — Customer and Warehouse")
    ask(
        client,
        world,
        "Warehouse Worker",
        "I would like to buy a wireless gaming mouse under $100. Can it arrive tomorrow?",
        from_name="Customer",
    )


def demo_2_warehouse_driver(client, world: dict[str, Any], order_id: str) -> str:
    banner("Demonstration 2 — Warehouse and Driver")
    result = ask(
        client,
        world,
        "Driver",
        f"Order {order_id} has been packed. Can you deliver it tomorrow?",
        from_name="Warehouse Worker",
    )
    return result.get("answer", "")


def demo_3_negotiation(client, world: dict[str, Any]) -> None:
    banner("Demonstration 3 — Negotiation")
    ask(
        client,
        world,
        "Warehouse Worker",
        "Traffic is unusually heavy today. Can packing be delayed by two hours?",
        from_name="Driver",
    )


def demo_4_information_request(client, world: dict[str, Any], order_id: str) -> None:
    banner("Demonstration 4 — Information Request")
    ask(client, world, "Support Agent", "Where is my package?", from_name="Customer")

    warehouse_reply = ask(
        client,
        world,
        "Warehouse Worker",
        f"Has Order {order_id} been dispatched?",
        from_name="Support Agent",
    ).get("answer", "")

    ask(
        client,
        world,
        "Support Agent",
        f'The warehouse just told you: "{warehouse_reply}" '
        f"Relay this to the customer who asked where their package is, in your own words.",
        from_name="(orchestrator, relaying Warehouse Worker's real answer)",
    )


def demo_5_collaboration(client, world: dict[str, Any], order_id: str) -> None:
    banner("Demonstration 5 — Multi-Actor Collaboration")
    print(f'\nCustomer asks: "Can order {order_id} arrive tomorrow?"')

    warehouse_reply = ask(
        client,
        world,
        "Warehouse Worker",
        f"The customer is asking whether order {order_id} can arrive tomorrow. Can you confirm it's ready to go?",
        from_name="Customer",
    ).get("answer", "")

    inventory_reply = ask(
        client,
        world,
        "Inventory Robot",
        f'Warehouse update: "{warehouse_reply}" Can you confirm the stock situation for this order?',
        from_name="Warehouse Worker",
    ).get("answer", "")

    driver_reply = ask(
        client,
        world,
        "Driver",
        f'Inventory update: "{inventory_reply}" Given that, can you deliver order {order_id} tomorrow?',
        from_name="Inventory Robot",
    ).get("answer", "")

    support_reply = ask(
        client,
        world,
        "Support Agent",
        f'Driver update: "{driver_reply}" '
        f"Please confirm to the customer whether order {order_id} will arrive tomorrow.",
        from_name="Driver",
    ).get("answer", "")

    section("Final Answer to Customer (synthesized across 4 independent actors)")
    print(f'\nSupport Agent -> Customer: "{support_reply}"')


def main() -> int:
    with _client() as client:
        try:
            banner("Natural Language Actor Communication")
            print("\nBootstrapping World")
            world = bootstrap_world(client)
            print("✓ Geography Created")
            print("✓ Societies Created (Customer, Warehouse, Inventory, Logistics, Support)")
            print("✓ Actors Created (Customer, Warehouse Worker, Inventory Robot, Driver, Support Agent)")
            print(f"✓ Product Loaded ({TRACKED_PRODUCT_NAME}, quantity=3, $59.99)")
            print(f"✓ World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
            if not world["verification"].get("ok"):
                raise ApiError(f"World validation failed: {world['verification']}")

            demo_1_customer_warehouse(client, world)

            section("World Update: real order placed, packed, and shipped (real API calls)")
            order_id = place_real_order(client, world)
            shipment_id = pack_and_ship(client, world, order_id)
            print(f"Order ........ {order_id}")
            print(f"Shipment ..... {shipment_id} (status: created — packed, not yet dispatched)")

            demo_2_warehouse_driver(client, world, order_id)
            demo_3_negotiation(client, world)
            demo_4_information_request(client, world, order_id)
            demo_5_collaboration(client, world, order_id)

            banner("Natural Language Actor Communication — COMPLETE")
        except ApiError as exc:
            print(f"\nDemo failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
