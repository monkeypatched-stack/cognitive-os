"""DELEGATE-001..004 — delegated transaction qualification tests.

kernel/domains/grocery.py::subscribe_actor_inbox's _on_message previously
routed every inbound actor-to-actor message to AnswerQuestionCapability
unconditionally -- there was no way for one actor to ask another to
actually DO something (run a real capability) and get a structured
transaction result back, only to answer a free-text question. This closes
that gap with a real msg_type dispatch plus a new DelegateTaskCapability,
the AskActorCapability's sibling for actions instead of questions.

kernel/domains/grocery.py::_run_delegated_tasks is the one real dispatch
path shared by both the NATS receiving side (_on_message) and
DelegateTaskCapability's own in-process fallback -- it runs the target
actor's real capability chain through the same, single, shared
ActionExecutor (pr._execution_engine) every other real request in this
system already goes through.

Scope, stated honestly (same convention tests/unit/test_communication_
verification.py already established for this exact code): these tests
exercise the real in-process fallback path (pr._nats_client is None), not
a live NATS round-trip -- that file's own docstring notes no message
broker is assumed in CI, so the real NATS transport (_on_message wired to
nc.subscribe/nc.request themselves) is never separately exercised in this
suite either. DELEGATE-004 (below) documents the resulting limit on what
a regression guard can prove here: _on_message's own new dispatch branch
cannot be driven directly without a real NATS subscription callback, so
this only proves the SHARED code the old and new branches both still
resolve to (AnswerQuestionCapability, via AskActorCapability's own
in-process fallback) remains correct -- not a substitute for the fact
that the modification itself was a pure additive `if/else` with the
pre-existing behavior moved verbatim under the `else`.
"""

from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import (
    AskActorCapability,
    DelegateTaskCapability,
    _run_delegated_tasks,
)
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _register(pr, name, society_id=None):
    kwargs = {}
    if society_id is not None:
        kwargs["society_id"] = society_id
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)),
        **kwargs,
    )


def _seed_product(pr, quantity: int = 5):
    kg = pr.knowledge_graph
    store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
    product_id = list_product(kg, store, "merchant_a", "Milk", price=3.99, quantity=quantity)["product_id"]
    return kg, product_id


@pytest.mark.asyncio
async def test_delegate001_real_single_task_delegation():
    """A delegates a real ProductSelection task to B. The structured
    result must reflect B's own real capability outcome (the real
    selected product from B's own KG), not a free-text answer."""
    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 001", society_type="community")
    alice = _register(pr, "Alice Delegate", society_id=club.society.society_id)
    _register(pr, "Bob Delegate", society_id=club.society.society_id)
    _, product_id = _seed_product(pr)

    result = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": "Bob Delegate",
                "tasks": [
                    {
                        "capability": "ProductSelection",
                        "parameters": {"selection": [{"id": product_id, "qty": 1}]},
                    }
                ],
            },
        }
    )

    assert result["success"] is True
    assert result["success_count"] == 1
    assert result["failure_count"] == 0
    assert len(result["actions"]) == 1
    action = result["actions"][0]
    assert action["capability"] == "ProductSelection"
    assert action["success"] is True
    assert action["result"]["selected"][0]["id"] == product_id


@pytest.mark.asyncio
async def test_delegate002_real_multistep_chain_with_real_world_mutation():
    """A delegates a real (ProductSelection -> OrderCreation) chain to B,
    with real depends_on ordering. Both steps' real outcomes must be
    present, and the mutation must be real: a genuine reservation exists
    in B's own KG afterward, not merely reported by the capability."""
    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 002", society_type="community")
    alice = _register(pr, "Alice Delegate2", society_id=club.society.society_id)
    _register(pr, "Bob Delegate2", society_id=club.society.society_id)
    kg, product_id = _seed_product(pr, quantity=5)

    result = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": "Bob Delegate2",
                "tasks": [
                    {
                        "capability": "ProductSelection",
                        "parameters": {"selection": [{"id": product_id, "qty": 1}]},
                    },
                    {
                        "capability": "OrderCreation",
                        "parameters": {},
                        "depends_on": [0],
                    },
                ],
            },
        }
    )

    assert result["success"] is True
    assert result["success_count"] == 2
    assert result["failure_count"] == 0
    order_outcome = result["actions"][1]
    assert order_outcome["capability"] == "OrderCreation"
    assert order_outcome["success"] is True
    order_id = order_outcome["result"]["order_id"]
    assert order_id

    entity = kg.get_entity(product_id)
    active = [r for r in entity.attributes.get("reservations", []) if r.get("until", 0) > time.time()]
    assert len(active) == 1
    assert active[0]["actor_id"] == order_id


@pytest.mark.asyncio
async def test_delegate003_honest_failure_unknown_capability_and_empty_tasks():
    """An unknown capability name and an empty tasks list each produce a
    real, honest structured failure -- never a crash, never a fabricated
    success. Exercises both the sender-side (DelegateTaskCapability's own
    parameter guard) and receiver-side (_run_delegated_tasks's own guard,
    the one subscribe_actor_inbox's _on_message would hit on a malformed
    message) empty-tasks checks."""
    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 003", society_type="community")
    alice = _register(pr, "Alice Delegate3", society_id=club.society.society_id)
    bob = _register(pr, "Bob Delegate3", society_id=club.society.society_id)

    result = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": "Bob Delegate3",
                "tasks": [{"capability": "NotARealCapability", "parameters": {}}],
            },
        }
    )
    assert result["success"] is False
    assert result["failure_count"] == 1
    assert "Capability not found" in result["actions"][0]["error"]

    result2 = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {"target_actor": "Bob Delegate3", "tasks": []},
        }
    )
    assert result2["success"] is False
    assert "requires parameters.target_actor and parameters.tasks" in result2["error"]

    result3 = await _run_delegated_tasks(pr, bob.actor_id, "Bob", [])
    assert result3["success"] is False
    assert "non-empty tasks list" in result3["error"]


@pytest.mark.asyncio
async def test_delegate005_a_genuine_delegated_failure_is_marked_recoverable():
    """Same-tick cross-agent recovery (Qualification Gap Closure, Phase 4
    extension): a real, honest delegated-transaction failure (the exact
    same shape DELEGATE-003 already proves) now also carries the same
    generic {"recoverable": True} signal ProductSelectionCapability's own
    forced/real failures already use -- the SAME generic contract
    ActionExecutor's one-shot recovery hook reacts to, now honored by a
    second, independently-implemented capability, not a special case.
    Retrying (retry_after_failure=True) against the SAME still-broken
    task must be reported exhausted (recoverable=False), never claim a
    fabricated second chance at recovery."""
    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 005", society_type="community")
    alice = _register(pr, "Alice Delegate5", society_id=club.society.society_id)
    bob = _register(pr, "Bob Delegate5", society_id=club.society.society_id)

    bad_tasks = [{"capability": "NotARealCapability", "parameters": {}}]

    first = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {"target_actor": "Bob Delegate5", "tasks": bad_tasks},
        }
    )
    assert first["success"] is False
    assert first["recoverable"] is True

    retried = await DelegateTaskCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": "Bob Delegate5",
                "tasks": bad_tasks,
                "retry_after_failure": True,
                "excluded_ids": [bob.actor_id],
            },
        }
    )
    assert retried["success"] is False
    assert retried["recoverable"] is False


def test_delegate006_alternative_selection_prefers_goal_overlap_and_excludes_correctly():
    """Direct test of _find_alternative_delegate's own real selection
    logic (the delegation-level sibling of ProductSelectionCapability's
    retry_after_failure search): a real candidate whose own stated goals
    genuinely overlap the original target's wins -- and the original
    target itself, plus any explicitly excluded actor, is never eligible
    even when it would otherwise be the only goal-overlapping match.

    Scope, stated honestly: every actor in this runtime is discovered to
    be a real member of a shared universal "Planetary Society" (confirmed
    live), so "which society" is not a meaningful reachability filter in
    this environment and isn't asserted on here -- only the two properties
    _find_alternative_delegate actually guarantees (goal-overlap
    preference, correct exclusion) are checked."""
    import uuid
    from src.monkey_brain.kernel.society.domain import (
        ActorIdentity,
        ActorProfile,
        ActorType,
    )

    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 006", society_type="community")
    unique_goal = f"procurement_{uuid.uuid4().hex}"

    original = pr.register_actor(
        ActorProfile(
            identity=ActorIdentity(name="Bob Delegate6", actor_type=ActorType.HUMAN),
            goals=(unique_goal,),
        ),
        society_id=club.society.society_id,
    )
    matching = pr.register_actor(
        ActorProfile(
            identity=ActorIdentity(name="Erin Delegate6", actor_type=ActorType.HUMAN),
            goals=(unique_goal,),
        ),
        society_id=club.society.society_id,
    )
    alice = _register(pr, "Alice Delegate6", society_id=club.society.society_id)

    # Renamed this session: _find_actor_by_name -> _find_actor_by_id_or_name
    # (grocery.py), matching AskActorCapability's already-correct pattern of
    # trying actor_id before falling back to a name match. Returns a 3-tuple
    # (sr, state, ambiguity_error) -- matches real production usage at this
    # method's own call site (grocery.py's DelegateTaskCapability.handle()).
    _sr, original_state, _ambiguity_error = DelegateTaskCapability._find_actor_by_id_or_name(pr, "Bob Delegate6")

    _sr, found = DelegateTaskCapability._find_alternative_delegate(
        pr,
        original_state,
        {original.actor_id},
        alice.actor_id,
    )
    assert found is not None
    assert found.actor_id == matching.actor_id

    # Explicitly excluding the one real goal-overlapping match too must
    # never fall back to selecting it anyway.
    _sr, excluded_too = DelegateTaskCapability._find_alternative_delegate(
        pr,
        original_state,
        {original.actor_id, matching.actor_id},
        alice.actor_id,
    )
    assert excluded_too is None or excluded_too.actor_id not in {
        original.actor_id,
        matching.actor_id,
    }


@pytest.mark.asyncio
async def test_delegate004_existing_question_payload_shape_still_works(monkeypatch):
    """Regression guard: AskActorCapability's own real question-asking
    behavior (the code path _on_message's unchanged `else` branch still
    calls) is untouched by this phase's addition. The LLM backend itself
    is monkeypatched (same established convention as Phase 2/4's
    ComparatorRuntime swaps) purely to keep this deterministic and
    network-free -- every other real step (target resolution, the real
    communication-permission check, AnswerQuestionCapability's own fact-
    gathering, the in-process fallback branch) runs unmodified."""
    from src.monkey_brain.kernel.execute.provider import (
        model_backend as model_backend_module,
    )

    class _FakeBackend:
        async def complete(self, prompt: str, system: str = "") -> str:
            return "I'm doing my usual work here."

    monkeypatch.setattr(model_backend_module, "get_backend", lambda: _FakeBackend())

    pr = PlanetaryRuntime()
    club = pr.create_society("Delegate Test 004", society_type="community")
    alice = _register(pr, "Alice Delegate4", society_id=club.society.society_id)
    _register(pr, "Bob Delegate4", society_id=club.society.society_id)

    result = await AskActorCapability().handle(
        {
            "context": {
                "planetary_runtime": pr,
                "actor_id": alice.actor_id,
                "actor_role": "Alice",
            },
            "parameters": {
                "target_actor": "Bob Delegate4",
                "question": "what is your role?",
            },
        }
    )
    assert result["success"] is True
    assert result["answer"] == "I'm doing my usual work here."
