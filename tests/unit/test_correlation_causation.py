"""Verification tests for the correlation_id/causation_id hardening change:
CommunicationDecision, the queued-message dict (kernel/society/runtime.py),
BeliefHypothesis, ContextEvent, TimelineEntry, and the negotiation ->
Timeline call site added to TransactionCoordinator.execute() all now carry
correlation_id (the logical end-to-end operation) and causation_id (the
immediate upstream record that produced this one).

Design this file locks in:
- correlation_id resolution order: reuse execution_id inside a cognitive
  tick, transaction_id inside a negotiation, or mint fresh for a bare
  communication check with no upstream operation (CommunicationDecision's
  own default_factory does this automatically).
- causation_id is left "" rather than fabricated wherever no concrete
  upstream id is in scope — several tests below assert that explicitly.
- Interaction/InteractionMessage (kernel/society/interaction.py) is
  UNTOUCHED by this change: interaction_id already plays correlation_id's
  role and in_reply_to already plays causation_id's role. Not re-tested
  here since no field changed.

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/unit/test_correlation_causation.py -v
"""

from __future__ import annotations

import asyncio
import dataclasses
import os

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.society.communication import CommunicationDecision
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.transaction import (
    NegotiationTrace,
    TransactionCoordinator,
)
from src.monkey_brain.kernel.timeline.entry import TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore
from src.monkey_brain.common.correlation import new_correlation_id


def _register(pr, name, society_id=None, home_space_id=None):
    kwargs = {}
    if society_id is not None:
        kwargs["society_id"] = society_id
    if home_space_id is not None:
        kwargs["home_space_id"] = home_space_id
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)),
        **kwargs,
    )


def _same_society_pair(label):
    pr = PlanetaryRuntime()
    club = pr.create_society(f"Correlation Test {label}", society_type="community")
    # create_society() auto-hosts every new society at the same shared
    # bootstrap "Default City" pr's own default society is already hosted
    # at (see add_society's docstring) — without its own city, this club
    # stays a TEMPORARY co-member of pr's default society too (True
    # Multi-Actor Coordination), so resolve_communication() sees TWO
    # shared societies for alice/bob and may return the default society's
    # CommunicationDecision instead of this club's, breaking any assertion
    # that compares a decision/causation_id against sr.communication_audit()
    # specifically. Give the club its own city/space so it's the ONLY
    # society alice/bob share.
    country = pr.create_country(f"Correlation Test {label} Country")
    city = pr.create_city(f"Correlation Test {label} City", country.entity_id)
    street = pr.create_geographic_entity(GeographicEntityType.STREET, f"{label} Street", city.entity_id)
    building = pr.create_geographic_entity(GeographicEntityType.BUILDING, f"{label} Building", street.entity_id)
    space = pr.create_geographic_entity(GeographicEntityType.SPACE, f"{label} Space", building.entity_id)
    pr.assign_society_to_city(club.society.society_id, city.entity_id)
    alice = _register(
        pr,
        f"Alice {label}",
        society_id=club.society.society_id,
        home_space_id=space.entity_id,
    )
    bob = _register(
        pr,
        f"Bob {label}",
        society_id=club.society.society_id,
        home_space_id=space.entity_id,
    )
    sr = pr.get_society_runtime(club.society.society_id)
    return pr, sr, alice, bob


class _ScriptedCoordinatorRealPlanetary(TransactionCoordinator):
    """Same scripting shape as test_transaction_coordinator.py's own
    _ScriptedCoordinator (society/affiliate resolution and the per-round
    LLM decision are scripted so no live LLM is needed), but wired to a
    REAL PlanetaryRuntime so the context_stream/_record_decision
    propagation this file tests is exercised for real, not bypassed. That
    existing file's _ScriptedCoordinator hardcodes planetary=None
    internally, so it can't be reused as-is for this purpose."""

    def __init__(self, planetary, *, candidates, traces):
        super().__init__(planetary=planetary)
        self._candidates = tuple(candidates)
        self._traces = traces

    def _relevant_societies(self, actor_id, objective):
        return set()

    def _eligible_affiliates(self, actor_id, relevant_society_ids):
        return self._candidates

    def _current_trust(self, originating_actor_id, target_actor_id):
        return 1.0

    async def _send_message(self, originating_actor_id, target_actor_id, message):
        scripted = self._traces.get(target_actor_id)
        if isinstance(scripted, list):
            return scripted.pop(0) if scripted else None
        return scripted

    def _strategic_context(self, originating_actor_id, target_actor_id, trace, remaining_candidates):
        return None

    async def _decide_next_action(
        self,
        originating_actor_id,
        objective,
        prior_steps,
        last_target,
        last_trace,
        remaining_candidates,
        strategic_context,
    ):
        return {
            "next_action": "contact_another_affiliate",
            "reason": "default",
            "target_actor_id": None,
            "strategic_context": strategic_context,
        }

    def _update_trust_from_trace(self, *a, **k):
        pass

    def _apply_trust_outcome(self, *a, **k):
        pass

    def _publish_belief_perturbation(self, *a, **k):
        pass


class TestRequestReceivesCorrelationId:
    """Property 1: a communication request receives a correlation_id, even
    from an unchanged call site that passes none explicitly."""

    def test_resolve_self_mints_correlation_id_when_none_supplied(self):
        pr, sr, alice, bob = _same_society_pair("P1")
        sent = sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})
        assert sent is True
        decision = sr.communication_audit()[-1]
        assert isinstance(decision, CommunicationDecision)
        assert decision.correlation_id


class TestChildMessageInheritsCorrelationId:
    """Property 2: a child message inherits the correct correlation_id."""

    def test_queued_message_carries_the_decisions_correlation_id(self):
        pr, sr, alice, bob = _same_society_pair("P2")
        correlation_id = "corr-p2-explicit"
        sent = sr.send_message(
            alice.actor_id,
            bob.actor_id,
            "greeting",
            {"text": "hi"},
            correlation_id=correlation_id,
        )
        assert sent is True
        queued = sr.get_messages_for(bob.actor_id)
        assert len(queued) == 1
        assert queued[0]["correlation_id"] == correlation_id


class TestChildMessageCausationId:
    """Property 3: the child message receives the correct causation_id —
    the decision_id of the CommunicationDecision that authorized it."""

    def test_queued_message_causation_id_is_the_authorizing_decision(self):
        pr, sr, alice, bob = _same_society_pair("P3")
        sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})
        decision = sr.communication_audit()[-1]
        queued = sr.get_messages_for(bob.actor_id)
        assert queued[0]["causation_id"] == decision.decision_id
        assert queued[0]["causation_id"]  # never empty for an authorized send


class TestResponseRetainsCorrelationId:
    """Property 4: a response retains the same correlation_id as the
    request that produced it — proven through AskActorCapability's real
    request/response round trip (in-process fallback leg, no live NATS
    broker needed in CI, matching this repo's existing AskActor test
    convention)."""

    @pytest.mark.asyncio
    async def test_ask_actor_response_carries_the_injected_correlation_id(self):
        from src.monkey_brain.kernel.domains.grocery import AskActorCapability

        pr, sr, alice, bob = _same_society_pair("P4")
        correlation_id = "corr-p4-ask-actor"
        result = await AskActorCapability().handle(
            {
                "context": {
                    "planetary_runtime": pr,
                    "actor_id": alice.actor_id,
                    "actor_role": "Alice",
                    "correlation_id": correlation_id,
                },
                "parameters": {"target_actor": "Bob P4", "question": "got milk?"},
            }
        )
        assert result["success"] is True
        assert result["correlation_id"] == correlation_id


class TestResponseCausationId:
    """Property 5: the response causation_id references the message/event
    that caused it — the resolve_communication decision's decision_id."""

    @pytest.mark.asyncio
    async def test_ask_actor_response_causation_id_matches_the_decision(self):
        from src.monkey_brain.kernel.domains.grocery import AskActorCapability

        pr, sr, alice, bob = _same_society_pair("P5")
        result = await AskActorCapability().handle(
            {
                "context": {
                    "planetary_runtime": pr,
                    "actor_id": alice.actor_id,
                    "actor_role": "Alice",
                },
                "parameters": {"target_actor": "Bob P5", "question": "got milk?"},
            }
        )
        assert result["success"] is True
        decision = sr.communication_audit()[-1]
        assert result["causation_id"] == decision.decision_id


class TestNegotiationPreservesCorrelationChain:
    """Property 6: negotiation messages preserve the correlation chain —
    every ContextEvent a transaction publishes (across every round) shares
    the same transaction_id as correlation_id."""

    def test_all_transaction_context_events_share_correlation_id(self):
        pr = PlanetaryRuntime()
        trace = NegotiationTrace(
            actor_id="bob-p6",
            execution_outcome="goal_achieved",
            explanation="bob achieved it",
        )
        coord = _ScriptedCoordinatorRealPlanetary(pr, candidates=("bob-p6",), traces={"bob-p6": trace})
        result = asyncio.run(coord.execute("alice-p6", "buy milk"))

        events = pr.context_stream.events(limit=1000)
        tx_events = [
            e
            for e in events
            if e.event_type == ContextEventType.INTERACTION
            and (e.payload or {}).get("transaction_id") == result.transaction_id
        ]
        assert tx_events, "transaction must publish at least one ContextEvent"
        assert all(e.correlation_id == result.transaction_id for e in tx_events)
        # The negotiation_trace-flavored event's causation_id points at
        # that round's own NegotiationTrace.trace_id -- not fabricated.
        trace_events = [e for e in tx_events if (e.payload or {}).get("type") == "negotiation_trace"]
        assert trace_events
        assert trace_events[0].causation_id == trace.trace_id


class TestTimelineTracesToOriginatingExecution:
    """Property 7: Timeline events can be traced back to the originating
    execution. Tested two ways: (a) directly against _record_decision, the
    lower-level mechanism, and (b) end-to-end through a real negotiation,
    proving the new TransactionCoordinator -> Timeline call site actually
    lands a traceable DECISION entry (a real gap before this change --
    negotiations wrote zero Timeline entries)."""

    def test_record_decision_writes_correlation_id_directly(self):
        pr = PlanetaryRuntime()
        actor_id = "actor-p7-direct"
        pr._record_decision(actor_id, {"reason": "test"}, execution_id="exec-p7-direct")
        entries = TimelineStore().query(actor_id, TimelineKind.DECISION)
        assert entries
        last = entries[-1]
        assert last.correlation_id == "exec-p7-direct"
        assert last.metadata.get("execution_id") == "exec-p7-direct"

    def test_negotiation_lands_a_traceable_decision_entry(self):
        pr = PlanetaryRuntime()
        trace = NegotiationTrace(
            actor_id="bob-p7",
            execution_outcome="goal_achieved",
            explanation="bob achieved it",
        )
        coord = _ScriptedCoordinatorRealPlanetary(pr, candidates=("bob-p7",), traces={"bob-p7": trace})
        result = asyncio.run(coord.execute("alice-p7", "buy milk"))

        entries = TimelineStore().query("alice-p7", TimelineKind.DECISION)
        matching = [e for e in entries if e.correlation_id == result.transaction_id]
        assert matching, "negotiation must write a Timeline DECISION entry traceable to its transaction_id"
        assert matching[-1].metadata.get("decision_kind") == "negotiation"


class TestIndependentRequestsDoNotShareCorrelationIds:
    """Property 8: independent requests do not share correlation ids."""

    def test_two_unrelated_resolves_get_different_correlation_ids(self):
        pr, sr, alice, bob = _same_society_pair("P8")
        d1 = sr._communication_router.resolve(alice.actor_id, bob.actor_id)
        d2 = sr._communication_router.resolve(alice.actor_id, bob.actor_id)
        assert d1.correlation_id != d2.correlation_id
        # Distinct decisions too -- these are two separate audit events,
        # not the same record read twice.
        assert d1.decision_id != d2.decision_id


class TestRetriesPreserveCorrelationId:
    """Property 9: retries preserve the logical correlation id — the same
    caller-supplied correlation_id across two calls stays identical, while
    each call still gets its own distinct decision_id (a retry is still a
    new, real audit event, not a no-op)."""

    def test_two_resolves_with_same_explicit_correlation_id_match(self):
        pr, sr, alice, bob = _same_society_pair("P9")
        correlation_id = "corr-p9-retry"
        d1 = sr._communication_router.resolve(alice.actor_id, bob.actor_id, correlation_id=correlation_id)
        d2 = sr._communication_router.resolve(alice.actor_id, bob.actor_id, correlation_id=correlation_id)
        assert d1.correlation_id == d2.correlation_id == correlation_id
        assert d1.decision_id != d2.decision_id


class TestDuplicateMessagesDoNotForkCorrelation:
    """Property 10: duplicate messages (identical payload, replayed with
    the same correlation_id) do not accidentally mint a new logical
    correlation — both queued copies carry the same correlation_id even
    though they're two distinct queue entries."""

    def test_replayed_identical_message_keeps_one_correlation_id(self):
        pr, sr, alice, bob = _same_society_pair("P10")
        correlation_id = "corr-p10-duplicate"
        payload = {"text": "hi"}
        sr.send_message(
            alice.actor_id,
            bob.actor_id,
            "greeting",
            payload,
            correlation_id=correlation_id,
        )
        sr.send_message(
            alice.actor_id,
            bob.actor_id,
            "greeting",
            payload,
            correlation_id=correlation_id,
        )
        queued = sr.get_messages_for(bob.actor_id)
        assert len(queued) == 2
        assert {m["correlation_id"] for m in queued} == {correlation_id}


class TestExistingRoutingBehaviorUnchanged:
    """Property 11: existing communication routing behavior is unchanged —
    calling resolve()/send_message() exactly as every pre-existing call
    site does (no correlation_id/causation_id kwargs at all) produces the
    same allowed/reason/routing outcome as before this change, proving
    pure backward compatibility. Mirrors test_communication_verification.py's
    TestCommunicationLogRecordsAllowedDecision/DeniedDecision assertions."""

    def test_same_society_allowed_unchanged(self):
        pr, sr, alice, bob = _same_society_pair("P11Allow")
        sent = sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})
        assert sent is True
        decision = sr.communication_audit()[-1]
        assert decision.allowed is True
        assert decision.sender_id == alice.actor_id
        assert decision.recipient_id == bob.actor_id

    def test_cross_society_no_affiliation_denied_unchanged(self):
        pr = PlanetaryRuntime()
        club_a = pr.create_society("Correlation Test P11Deny A", society_type="community")
        club_b = pr.create_society("Correlation Test P11Deny B", society_type="community")
        # create_society() auto-hosts every new society at the same shared
        # bootstrap "Default City" (see add_society's docstring) — an actor
        # placed there via pr.register_actor's geography placement becomes
        # a TEMPORARY coordination participant of every society hosted at
        # that city (True Multi-Actor Coordination), which would make
        # alice/carol affiliated with BOTH clubs and defeat this "no
        # affiliation" scenario. Rehost club_b at its own city so the two
        # clubs stay genuinely unaffiliated, matching what this test
        # actually asserts. A City with no Space descendant doesn't
        # satisfy register_actor()'s "every Society needs a real Space"
        # invariant, so without one register_actor()'s home_space_id=None
        # fallback (spaces_for_society(club_b) empty) silently re-hosts
        # club_b back at the shared default bootstrap space — build the
        # full Street->Building->Space chain and pass it explicitly so
        # that fallback never triggers.
        country_b = pr.create_country("Correlation Test P11Deny Country B")
        city_b = pr.create_city("Correlation Test P11Deny City B", country_b.entity_id)
        street_b = pr.create_geographic_entity(GeographicEntityType.STREET, "P11Deny Street B", city_b.entity_id)
        building_b = pr.create_geographic_entity(
            GeographicEntityType.BUILDING, "P11Deny Building B", street_b.entity_id
        )
        space_b = pr.create_geographic_entity(GeographicEntityType.SPACE, "P11Deny Space B", building_b.entity_id)
        pr.assign_society_to_city(club_b.society.society_id, city_b.entity_id)
        alice = _register(pr, "Alice P11Deny", society_id=club_a.society.society_id)
        carol = _register(
            pr,
            "Carol P11Deny",
            society_id=club_b.society.society_id,
            home_space_id=space_b.entity_id,
        )
        sr_a = pr.get_society_runtime(club_a.society.society_id)

        sent = sr_a.send_message(alice.actor_id, carol.actor_id, "greeting", {"text": "hi"})
        assert sent is False
        decision = sr_a.communication_audit()[-1]
        assert decision.allowed is False
        assert decision.reason


class TestCausationIdNotFabricated:
    """Explicit negative test backing the "don't fabricate ids" rule: a
    bare resolve() with no upstream cause leaves causation_id empty rather
    than inventing one."""

    def test_bare_resolve_has_empty_causation_id_by_default(self):
        pr, sr, alice, bob = _same_society_pair("P12NoFab")
        decision = sr._communication_router.resolve(alice.actor_id, bob.actor_id)
        assert decision.causation_id == ""
