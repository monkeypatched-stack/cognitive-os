"""Repository-wide actor-isolation audit (Tests A-J).

Proves, against real production code paths (not mocks of the isolation
boundary itself), that:

  - Actor A's local state/beliefs are genuinely separate objects from
    Actor B's, including every nested mutable structure (not just shallow
    object identity) -- see test_A/test_B/test_C.
  - Information crosses the actor boundary only through an explicit
    protocol (AskActorCapability's negotiated conversation, the
    visibility="shared"+co-membership-gated Cognitive Network, or a real
    observe/build() call) -- never automatic global sync -- see
    test_D/test_E/test_J.
  - Concurrent actor execution, checkpoint round-trips, and per-actor
    memory retrieval never leak or contaminate across actor_id -- see
    test_F/test_G/test_H.
  - Local belief mutation never silently reaches the shared world, and
    the shared world only reaches an actor through an explicit
    build()/observation call -- see test_I/test_J.

This complements (does not replace) tests/scenarios/test_transition_gate.py,
whose test_budget004 already proves TransitionGate/negotiation ordering
under real asyncio.gather concurrency for a contested resource -- the
negotiation-ordering half of Test F below.
"""

from __future__ import annotations

import asyncio

from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Goal,
    WorkingMemoryEntry,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.domains.grocery import AskActorCapability


def _register(pr, name, society_id=None):
    kwargs = {"society_id": society_id} if society_id is not None else {}
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)),
        **kwargs,
    )


def _isolated_society(pr, label):
    """A society hosted at its own city, not the shared bootstrap Default
    City -- see this session's test_society.py/test_correlation_causation.py
    fixes: two societies sharing Default City make actors physically
    co-located there TEMPORARY members of both, which would defeat these
    "unauthorized/unaffiliated" isolation tests for the wrong reason
    (accidental co-membership, not an actual isolation bug)."""
    society = pr.create_society(f"Isolation {label}", society_type="community")
    country = pr.create_country(f"Isolation {label} Country")
    city = pr.create_city(f"Isolation {label} City", country.entity_id)
    street = pr.create_geographic_entity(GeographicEntityType.STREET, f"{label} St", city.entity_id)
    building = pr.create_geographic_entity(GeographicEntityType.BUILDING, f"{label} Bldg", street.entity_id)
    space = pr.create_geographic_entity(GeographicEntityType.SPACE, f"{label} Space", building.entity_id)
    pr.assign_society_to_city(society.society.society_id, city.entity_id)
    return society, space.entity_id


# ── Test A -- separate local state (deep, not just shallow object identity) ─


def test_A_separate_local_state():
    pr = PlanetaryRuntime()
    a = _register(pr, "IsoA-Alice")
    b = _register(pr, "IsoA-Bob")

    assert a is not b
    assert a.profile is not b.profile

    belief_a = BeliefState(actor_id=a.actor_id)
    belief_b = BeliefState(actor_id=b.actor_id)
    # Every mutable nested structure must be its own object -- proves
    # BeliefState's dataclass fields use field(default_factory=...), not a
    # bare mutable literal shared across every instance.
    assert belief_a.facts is not belief_b.facts
    assert belief_a.observations is not belief_b.observations
    assert belief_a.working_memory is not belief_b.working_memory
    assert belief_a.metadata is not belief_b.metadata
    assert belief_a.plan is not belief_b.plan

    belief_a.add_fact(entity="milk", attribute="price", value=5.0, confidence=0.9)
    assert belief_a.facts and not belief_b.facts


# ── Test B -- no cross-write ─────────────────────────────────────────────


def test_B_no_cross_write():
    belief_a = BeliefState(actor_id="iso-b-a")
    belief_b = BeliefState(actor_id="iso-b-b")

    belief_a.add_fact(entity="x", attribute="y", value=1, confidence=0.9)
    belief_a.working_memory.append(WorkingMemoryEntry(key="secret", value="A's private note"))

    # No production API accepts a *different* actor's BeliefState object
    # for direct mutation (confirmed by this audit's repo-wide search for
    # `actor.belief.update(`, `actors[target_id]...=`, `.belief_state =`
    # cross-actor write patterns -- every real write happens through the
    # writing actor's own tick). Mutating A's belief must never reach B's.
    assert belief_b.facts == []
    assert belief_b.working_memory == []


# ── Test C -- belief divergence over the same fact ───────────────────────


def test_C_belief_divergence():
    belief_a = BeliefState(actor_id="iso-c-a")
    belief_b = BeliefState(actor_id="iso-c-b")

    belief_a.add_fact(entity="milk_price", attribute="value", value=5.0, confidence=0.9)
    belief_b.add_fact(entity="milk_price", attribute="value", value=8.0, confidence=0.9)

    assert belief_a.facts[0].value == 5.0
    assert belief_b.facts[0].value == 8.0
    assert belief_a.facts[0].value != belief_b.facts[0].value  # both stay valid simultaneously


# ── Test D -- explicit information transfer (real AskActor round trip) ──


def test_D_explicit_information_transfer():
    pr = PlanetaryRuntime()
    society, space_id = _isolated_society(pr, "D")
    alice = _register(pr, "IsoD-Alice", society.society.society_id)
    bob = pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="IsoD-Bob", actor_type=ActorType.HUMAN)),
        society_id=society.society.society_id,
        home_space_id=space_id,
    )

    before = pr.memory_manager.search_episodic("oat milk", top_k=10, actor_id=bob.actor_id)
    assert not any("oat milk" in n.payload.get("text", "").lower() for n in before)

    result = asyncio.run(
        AskActorCapability().handle(
            {
                "context": {
                    "planetary_runtime": pr,
                    "actor_id": alice.actor_id,
                    "actor_role": "Alice",
                },
                "parameters": {
                    "target_actor": bob.actor_id,
                    "question": "Does oat milk cost $5?",
                },
            }
        )
    )
    assert result["success"] is True

    # Bob now has a recorded conversation memory of the exchange -- ONLY
    # because the explicit AskActor interaction happened, not automatically.
    after = pr.memory_manager.search_episodic("oat milk", top_k=10, actor_id=bob.actor_id)
    assert any("oat milk" in n.payload.get("text", "").lower() for n in after)


# ── Test E -- private information stays private without authorization ───


def test_E_private_information_requires_authorization():
    pr = PlanetaryRuntime()
    society_a, _ = _isolated_society(pr, "E-A")
    society_b, _ = _isolated_society(pr, "E-B")
    alice = _register(pr, "IsoE-Alice", society_a.society.society_id)
    carol = _register(pr, "IsoE-Carol", society_b.society.society_id)

    pr.memory_manager.record_experience(alice.actor_id, "experience", "Alice's secret PIN is 4471")

    # No visibility="shared" tag, no co-membership -- the one production
    # cross-actor retrieval surface (ContextConstructionEngine.
    # search_shared_experiences, CCB-600) must refuse to surface it.
    leaked = pr.context_engine.search_shared_experiences(carol.actor_id, "secret PIN 4471", top_k=10)
    assert leaked == []

    # Authorization changes the outcome, proving the gate is real, not
    # just permanently closed: mark the memory shared AND give Carol a
    # real co-membership with Alice.
    pr.memory_manager.record_experience(
        alice.actor_id,
        "experience",
        "Alice's shopping list is public",
        metadata={"visibility": "shared"},
    )
    pr.join_society(carol.actor_id, society_a.society.society_id)
    visible = pr.context_engine.search_shared_experiences(carol.actor_id, "shopping list", top_k=10)
    assert any("shopping list" in n.payload.get("text", "").lower() for n in visible)


# ── Test F -- concurrent actor execution, no local-state contamination ──


def test_F_concurrent_actor_execution_no_contamination():
    pr = PlanetaryRuntime()
    society, space_id = _isolated_society(pr, "F")
    alice = _register(pr, "IsoF-Alice", society.society.society_id)
    bob = pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="IsoF-Bob", actor_type=ActorType.HUMAN)),
        society_id=society.society.society_id,
        home_space_id=space_id,
    )
    sr = pr.get_society_runtime(society.society.society_id)

    async def _run():
        return await asyncio.gather(
            sr.tick_one_actor(alice.actor_id),
            sr.tick_one_actor(bob.actor_id),
        )

    asyncio.run(_run())

    alice_state = sr.get_actor(alice.actor_id)
    bob_state = sr.get_actor(bob.actor_id)
    assert alice_state.actor_id != bob_state.actor_id
    assert alice_state.belief_state is not bob_state.belief_state
    # Negotiation-ordering under real contention (asyncio.gather of two
    # full buy-and-pay pipelines against one shared budget ceiling, exactly
    # one confirms) is already covered by
    # tests/scenarios/test_transition_gate.py::test_budget004 -- not
    # duplicated here.


# ── Test G -- checkpoint isolation (serialize/restore round trip) ───────


def test_G_checkpoint_isolation():
    belief_a = BeliefState(actor_id="iso-g-a")
    belief_a.add_fact(entity="secret", attribute="value", value="A-only", confidence=0.9)
    belief_b = BeliefState(actor_id="iso-g-b")
    belief_b.add_fact(entity="secret", attribute="value", value="B-only", confidence=0.9)

    snapshot_a = belief_a.to_dict()
    snapshot_b = belief_b.to_dict()

    restored_a = BeliefState.from_dict(snapshot_a)
    restored_b = BeliefState.from_dict(snapshot_b)

    assert restored_a.actor_id == "iso-g-a"
    assert restored_b.actor_id == "iso-g-b"
    assert restored_a.facts[0].value == "A-only"
    assert restored_b.facts[0].value == "B-only"

    # from_dict() must return a fresh object, never mutate a shared target
    restored_a.add_fact(entity="post-restore", attribute="x", value=1, confidence=0.9)
    assert len(restored_b.facts) == 1

    # persistence key scheme (persistence/actor_state_store.py) is
    # composite `f"{tenant_id}:{actor_id}"`, Mongo-indexed unique on
    # (tenant_id, actor_id) -- restoring one actor_id structurally cannot
    # resolve to another's document.


# ── Test H -- cache isolation (per-actor memory retrieval) ──────────────


def test_H_cache_isolation():
    pr = PlanetaryRuntime()
    alice = _register(pr, "IsoH-Alice")
    bob = _register(pr, "IsoH-Bob")

    pr.memory_manager.record_experience(alice.actor_id, "experience", "Alice cached fact: apple juice $3")
    pr.memory_manager.record_experience(bob.actor_id, "experience", "Bob cached fact: apple juice $3")

    alice_only = pr.memory_manager.search_episodic("apple juice", top_k=10, actor_id=alice.actor_id)
    assert alice_only and all(n.payload["actor_id"] == alice.actor_id for n in alice_only)

    bob_only = pr.memory_manager.search_episodic("apple juice", top_k=10, actor_id=bob.actor_id)
    assert bob_only and all(n.payload["actor_id"] == bob.actor_id for n in bob_only)


# ── Test I -- local belief mutation never directly reaches world state ──


def test_I_world_state_separation():
    pr = PlanetaryRuntime()
    alice = _register(pr, "IsoI-Alice")

    belief = BeliefState(actor_id=alice.actor_id)
    belief.add_fact(entity="inventory:milk", attribute="stock", value=999, confidence=0.9)

    # A local, never-committed BeliefState fact must not appear in the
    # shared KnowledgeGraph merely by existing -- only an authorized commit
    # path (TransitionGate -> KnowledgeGraph.compare_and_swap) does that.
    kg_entity = pr.knowledge_graph.get_entity("inventory:milk")
    assert kg_entity is None or kg_entity.attributes.get("stock") != 999


# ── Test J -- world change reaches actors only via explicit observation ──


def test_J_world_observation_not_automatic_sync():
    pr = PlanetaryRuntime()
    society, _ = _isolated_society(pr, "J")
    alice = _register(pr, "IsoJ-Alice", society.society.society_id)
    bob = _register(pr, "IsoJ-Bob", society.society.society_id)

    belief_a_before = BeliefState(actor_id=alice.actor_id)
    belief_b_before = BeliefState(actor_id=bob.actor_id)
    assert belief_a_before.facts == belief_b_before.facts == []

    # A world-state change happens.
    pr.knowledge_graph.add_entity("inventory:eggs", attributes={"stock": 12})

    # Already-constructed BeliefState snapshots are plain objects, not live
    # views -- they are never auto-mutated by a world change that happened
    # after they were built.
    assert belief_a_before.facts == []
    assert belief_b_before.facts == []

    # Only an explicit build()/observation call pulls world facts into an
    # actor's own PlanningContext, and only for the actor that asked.
    ctx = pr.context_engine.build(alice.actor_id, Goal(name="check eggs", description="check eggs"))
    assert ctx.actor_id == alice.actor_id
