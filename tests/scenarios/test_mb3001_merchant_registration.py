"""MB-3001 Merchant Registration — new-merchant onboarding scenario.

Scenario: Merchant registers.

Expected:
    - Merchant actor created.
    - Merchant society exists.
    - Merchant profile created.

Merchants are a distinct participant class from customers (MB-3000):
ActorType.ENTERPRISE, registered into a dedicated "Merchant Society"
rather than the marketplace's default home Society. Uses
PlanetaryRuntime.register_actor(society_id=...) — the same canonical
registration workflow MB-3000 exercises for the default case (kernel/
society/integration.py's Registration Entry Points refactor) — so a
merchant gets the identical world-invariant guarantees a customer does
(Society hosted at a Space, Presence initialized) even though this
scenario doesn't assert those explicitly.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

MERCHANT_NAME = "Bob's Store"


def test_mb3001_merchant_registration():
    marketplace = PlanetaryRuntime()
    merchant_society = marketplace.create_society(name="Merchant Society")

    profile = ActorProfile(identity=ActorIdentity(name=MERCHANT_NAME, actor_type=ActorType.ENTERPRISE))
    bob = marketplace.register_actor(profile, society_id=merchant_society.society.society_id)

    # Merchant actor created.
    assert bob is not None
    assert bob.profile.identity.name == MERCHANT_NAME
    assert bob.profile.identity.actor_type == ActorType.ENTERPRISE
    assert merchant_society.get_actor(bob.actor_id) is not None

    # Merchant society exists — a real, retrievable Society this
    # PlanetaryRuntime manages, distinct from the marketplace's default
    # home Society (MB-3000's Customer registers into that one instead).
    assert marketplace.get_society_runtime(merchant_society.society.society_id) is merchant_society
    assert merchant_society.society.society_id != marketplace.society.society_id
    assert merchant_society.society.society_id in marketplace.membership_registry.societies_for_actor(bob.actor_id)

    # Merchant profile created.
    assert bob.profile is not None
    assert bob.profile.identity.actor_id == bob.actor_id
