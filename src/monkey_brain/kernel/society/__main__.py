#!/usr/bin/env python3
"""Society Runtime entry point — run the milk delivery scenario or
a custom society configuration.

Usage:
    python -m src.monkey_brain.kernel.society                    # default milk delivery
    python -m src.monkey_brain.kernel.society --actors 10        # scale test
    python -m src.monkey_brain.kernel.society --json scenario.json  # custom scenario
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from src.monkey_brain.kernel.society.domain import (
    Society,
    ActorIdentity,
    ActorProfile,
    ActorRole,
    ActorCapability,
    ActorRelationship,
    ActorMembership,
    ActorType,
    RelationshipType,
    CapabilityLevel,
)
from src.monkey_brain.kernel.society.world import (
    WorldEntity,
    WorldResource,
    WorldLocation,
    WorldEntityType,
)
from src.monkey_brain.kernel.society.interaction import InteractionType
from src.monkey_brain.kernel.society.learning import SharedExperience
from src.monkey_brain.kernel.society.governance import Permission
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def build_milk_delivery_society() -> tuple[Society, list[ActorProfile], list[WorldEntity]]:
    """Build the milk delivery acceptance-criteria scenario."""
    human = ActorProfile(
        identity=ActorIdentity(name="Customer", actor_type=ActorType.HUMAN, description="Requests milk"),
        capabilities=(ActorCapability(name="place_order", level=CapabilityLevel.COMPETENT),),
        goals=("get_2_liters_of_milk",),
        trust_level=1.0,
        ownership="self",
    )
    retail = ActorProfile(
        identity=ActorIdentity(name="RetailCo", actor_type=ActorType.ENTERPRISE),
        capabilities=(
            ActorCapability(name="inventory_check", level=CapabilityLevel.EXPERT),
            ActorCapability(name="payment_processing", level=CapabilityLevel.MASTER),
        ),
        goals=("fulfill_orders",),
        policies=("check_inventory_before_fulfill",),
        trust_level=0.95,
        ownership="shareholders",
    )
    robot = ActorProfile(
        identity=ActorIdentity(name="RetrievalBot", actor_type=ActorType.ROBOT),
        capabilities=(ActorCapability(name="product_retrieval", level=CapabilityLevel.PROFICIENT),),
        goals=("retrieve_products",),
        trust_level=0.85,
        ownership="RetailCo",
    )
    payment = ActorProfile(
        identity=ActorIdentity(name="PaymentService", actor_type=ActorType.DIGITAL_SERVICE),
        capabilities=(ActorCapability(name="authorize_payment", level=CapabilityLevel.MASTER),),
        goals=("process_payments",),
        policies=("verify_funds", "fraud_check"),
        trust_level=0.99,
        ownership="PaymentCorp",
    )
    delivery = ActorProfile(
        identity=ActorIdentity(name="DeliveryBot", actor_type=ActorType.ROBOT),
        capabilities=(ActorCapability(name="navigation", level=CapabilityLevel.EXPERT),),
        goals=("deliver_packages",),
        trust_level=0.8,
        ownership="RetailCo",
    )

    actors = [human, retail, robot, payment, delivery]

    society = Society(
        society_id="milk_delivery",
        name="Milk Delivery Society",
        description="Coordinates milk delivery from store to customer",
        memberships=(
            ActorMembership(
                actor_id=human.identity.actor_id,
                role=ActorRole(name="customer", permissions=("order", "track")),
            ),
            ActorMembership(
                actor_id=retail.identity.actor_id,
                role=ActorRole(name="retailer", permissions=("inventory", "fulfill", "bill")),
            ),
            ActorMembership(
                actor_id=robot.identity.actor_id,
                role=ActorRole(name="retrieval_robot", permissions=("pick", "stow")),
            ),
            ActorMembership(
                actor_id=payment.identity.actor_id,
                role=ActorRole(name="payment_service", permissions=("charge", "refund")),
            ),
            ActorMembership(
                actor_id=delivery.identity.actor_id,
                role=ActorRole(name="delivery_robot", permissions=("navigate", "deliver")),
            ),
        ),
        relationships=(
            ActorRelationship(
                source_actor_id=human.identity.actor_id,
                target_actor_id=retail.identity.actor_id,
                relationship_type=RelationshipType.CUSTOMER,
            ),
            ActorRelationship(
                source_actor_id=retail.identity.actor_id,
                target_actor_id=robot.identity.actor_id,
                relationship_type=RelationshipType.SUPERIOR,
            ),
            ActorRelationship(
                source_actor_id=retail.identity.actor_id,
                target_actor_id=delivery.identity.actor_id,
                relationship_type=RelationshipType.SUPERIOR,
            ),
            ActorRelationship(
                source_actor_id=retail.identity.actor_id,
                target_actor_id=payment.identity.actor_id,
                relationship_type=RelationshipType.COLLABORATOR,
            ),
        ),
        policies=("no_delivery_after_10pm", "max_order_quantity_10"),
    )

    entities = [
        WorldEntity(
            name="Store A",
            entity_type=WorldEntityType.ENTITY,
            attributes={"milk_stock": 10, "open_hours": "8-22"},
            confidence=0.95,
            provenance="retail_inventory_system",
        ),
        WorldEntity(
            name="Warehouse",
            entity_type=WorldEntityType.ENTITY,
            attributes={"capacity": 1000},
            confidence=1.0,
        ),
        WorldEntity(
            name="Customer Location",
            entity_type=WorldEntityType.LOCATION,
            attributes={"address": "123 Main St"},
            confidence=1.0,
        ),
        WorldEntity(
            name="2L Milk",
            entity_type=WorldEntityType.RESOURCE,
            attributes={"quantity": 2, "unit": "liters", "price": 3.50},
            confidence=0.9,
            provenance="product_catalog",
        ),
    ]

    return society, actors, entities


async def run_scenario(
    society: Society,
    actors: list[ActorProfile],
    entities: list[WorldEntity],
    cycles: int = 3,
    verbose: bool = False,
) -> dict:
    """Run a complete society scenario and return results."""
    start = time.time()

    pr = PlanetaryRuntime(society)

    for profile in actors:
        pr.register_actor(profile)

    for entity in entities:
        pr.add_world_entity(entity)

    pr.world.add_resource(
        WorldResource(
            name="Milk",
            resource_type="dairy",
            quantity=10,
            unit="liters",
            owner_id=actors[1].identity.actor_id if len(actors) > 1 else "",
        )
    )
    pr.world.add_location(WorldLocation(name="Store A", address="456 Oak Ave", latitude=37.7749, longitude=-122.4194))

    for profile in actors:
        pr.governance.grant_permission(
            Permission(actor_id=profile.identity.actor_id, resource="order", action="create")
        )
        pr.governance.grant_permission(
            Permission(actor_id=profile.identity.actor_id, resource="world", action="observe")
        )

    for _ in range(cycles):
        await pr.cycle()

    if len(actors) >= 2 and len(actors) <= 5:
        pr.send_interaction(
            InteractionType.REQUEST,
            actors[0].identity.actor_id,
            (actors[1].identity.actor_id,),
            topic="order",
            proposal={"item": "2L Milk", "quantity": 2},
        )

        if len(actors) >= 3:
            pr.send_interaction(
                InteractionType.DELEGATE,
                actors[1].identity.actor_id,
                (actors[2].identity.actor_id,),
                topic="retrieval",
            )

        if len(actors) >= 4:
            pr.send_interaction(
                InteractionType.REQUEST,
                actors[1].identity.actor_id,
                (actors[3].identity.actor_id,),
                topic="payment",
                proposal={"amount": 7.00},
            )

        if len(actors) >= 5:
            pr.send_interaction(
                InteractionType.COORDINATE,
                actors[1].identity.actor_id,
                (actors[4].identity.actor_id,),
                topic="delivery",
            )

    pr.share_experience(
        SharedExperience(
            actor_id=actors[0].identity.actor_id,
            outcome="success",
            confidence=0.9,
            description="Scenario completed",
            lessons=("lesson_1",),
        )
    )

    trace = pr.trace()
    duration_ms = (time.time() - start) * 1000

    return {
        "society": pr.to_dict(),
        "world": pr.world.to_dict(),
        "trace_narrative": trace.narrative,
        "duration_ms": round(duration_ms, 2),
        "actor_count": len(pr.active_actors()),
        "interaction_count": len(pr._interaction_manager.all_interactions()),
        "context_events": pr.context_stream.event_count,
        "world_version": pr.world.version,
    }


async def main():
    parser = argparse.ArgumentParser(description="Society Runtime")
    parser.add_argument("--actors", type=int, default=0, help="Number of actors (0 = default scenario)")
    parser.add_argument("--cycles", type=int, default=3, help="Number of planetary cycles")
    parser.add_argument("--json", type=str, help="Path to JSON scenario file")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--output", type=str, help="Output file for results JSON")
    args = parser.parse_args()

    if args.json:
        with open(args.json) as f:
            scenario = json.load(f)
        print(f"Loaded scenario from {args.json}")
        print(json.dumps(scenario, indent=2))
        return

    if args.actors > 0:
        actors = []
        for i in range(args.actors):
            actors.append(
                ActorProfile(
                    identity=ActorIdentity(name=f"Actor_{i}", actor_type=ActorType.AI_AGENT),
                    goals=(f"task_{i}",),
                )
            )
        society = Society(
            name=f"Test Society ({args.actors} actors)",
            memberships=tuple(
                ActorMembership(actor_id=a.identity.actor_id, role=ActorRole(name="member")) for a in actors
            ),
        )
        entities = [WorldEntity(name=f"Entity_{i}") for i in range(min(args.actors, 10))]
        print(f"Running scale test: {args.actors} actors, {args.cycles} cycles")
    else:
        society, actors, entities = build_milk_delivery_society()
        print(f"Running milk delivery scenario: {len(actors)} actors, {args.cycles} cycles")

    result = await run_scenario(society, actors, entities, cycles=args.cycles, verbose=args.verbose)

    print(f"\nResults:")
    print(f"  Actors: {result['actor_count']}")
    print(f"  Interactions: {result['interaction_count']}")
    print(f"  Context events: {result['context_events']}")
    print(f"  World version: {result['world_version']}")
    print(f"  Duration: {result['duration_ms']:.2f}ms")

    if args.verbose:
        print(f"\n{result['trace_narrative']}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
