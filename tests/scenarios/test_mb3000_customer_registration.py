"""MB-3000 Customer Registration — new-customer onboarding scenario.

Objective: verify a new customer can join the marketplace.

Scenario: Alice registers as a new customer.

Expected:
    - Customer actor created.
    - Home society created.
    - Home society hosted.
    - Presence initialized.
    - World invariants satisfied.

Runs the real canonical registration workflow (PlanetaryRuntime.
register_actor() — see kernel/society/integration.py's Registration Entry
Points refactor: "the world must expose a single canonical actor
registration workflow... no public API may bypass world validation") on a
fresh PlanetaryRuntime, so "home society created/hosted" reflects the
actual first-boot bootstrap (Default Planet -> ... -> Default Space) a
real marketplace deployment goes through — not a hand-built fixture.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

CUSTOMER_NAME = "Alice"


def test_mb3000_customer_registration():
    marketplace = PlanetaryRuntime()

    profile = ActorProfile(identity=ActorIdentity(name=CUSTOMER_NAME, actor_type=ActorType.HUMAN))
    alice = marketplace.register_actor(profile)

    # Customer actor created.
    assert alice is not None
    assert alice.profile.identity.name == CUSTOMER_NAME
    assert marketplace._home_society_runtime(alice.actor_id) is not None

    # Home society created — Alice's permanent home affiliation is a real,
    # explicitly stored Membership in a real Society, not an implicit
    # attribute.
    home_society_id = marketplace.society.society_id
    assert home_society_id
    assert home_society_id in marketplace.membership_registry.societies_for_actor(alice.actor_id)

    # Home society hosted — associated with at least one Space. This is
    # the world invariant register_actor() must guarantee before it
    # returns (Actor Registration Invariant): the caller supplied no
    # home_space_id, so it was hosted at the configured default bootstrap
    # Space automatically.
    assert marketplace.default_bootstrap_space_id is not None
    hosting_entity = marketplace.geo_registry.entity_for_society(home_society_id)
    assert hosting_entity is not None
    assert marketplace.geo_registry.spaces_for_society(home_society_id) != ()

    # Presence initialized — Alice has exactly one current Space from the
    # moment registration completes, no separate move_actor() call needed.
    presence = marketplace.presence.current(alice.actor_id)
    assert presence is not None
    assert presence.is_open()
    assert presence.space_id == marketplace.default_bootstrap_space_id

    # World invariants satisfied — the core invariant this whole model
    # exists to guarantee: every Society has at least one associated
    # Space. Must not raise.
    marketplace.geo_registry.validate_society_has_space(home_society_id)

    # Effective membership is immediately computable — no further setup
    # required before Alice can act in the marketplace.
    assert home_society_id in marketplace.effective_societies(alice.actor_id)
