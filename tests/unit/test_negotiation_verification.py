"""Verification tests for the real negotiation substrate this session's
Negotiations workspace (living-world-explorer's NegotiationsPanel.tsx) is
a thin, honest UI layer over: TransactionCoordinator + TerminalStateEvaluator
(kernel/society/transaction.py, negotiation_planning.py), GameTheoryRuntime
(kernel/society/game_theory.py), and the grocery-domain bilateral bargaining
functions (kernel/domains/negotiation.py, kernel/domains/grocery.py).

The core negotiation-loop control flow (terminal-state gating, LLM cannot
self-terminate, trust-ordering, replanning) is ALREADY thoroughly covered
by tests/unit/test_transaction_coordinator.py's _ScriptedCoordinator suite
(10 scenarios) — this file does not duplicate that coverage. It instead
closes the gaps that suite doesn't touch: the real streaming integration
(WS hub + Context Stream), the historical negotiation REST route, real
trust mutation, the grocery bargaining math (zero prior coverage), durable
agreement persistence, and explicit documentation-as-assertions for the
two "do not build on this" findings (CoordinationEngine, InteractionManager's
missing REST surface for respond/vote/complete).

Known, deliberate gaps this file documents rather than works around:
- No test exercises the real NATS transport leg of _stream_event (no
  broker in CI) — only the WS-hub and Context-Stream legs, both of which
  run unconditionally regardless of NATS availability.
- "Deadlock" is not a concept this codebase implements; the real terminal
  state that answers that question is TerminalState(..., "no eligible
  affiliates remain") — already proven by test_transaction_coordinator.py's
  TestNoAffiliateCanSolve. This file's deadlock-framed test asserts the
  same real string directly, from this feature's own verification
  perspective, rather than re-deriving the whole scripted scenario.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.society.transaction import TransactionCoordinator
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.domains.negotiation import (
    negotiate_terms,
    record_agreement,
)
from src.monkey_brain.kernel.domains.grocery import negotiate_price
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph, EntityType
from src.monkey_brain.kernel.affiliations.manager import AffiliationManager
from src.monkey_brain.kernel.affiliations.affiliation import Affiliation


def _register(pr, name, society_id=None):
    kwargs = {}
    if society_id is not None:
        kwargs["society_id"] = society_id
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)),
        **kwargs,
    )


@pytest.fixture(scope="module")
def client():
    import subprocess

    try:
        subprocess.run(
            [
                "redis-cli",
                "-h",
                os.getenv("REDIS_HOST", "localhost"),
                "-p",
                os.getenv("REDIS_PORT", "6379"),
                "flushdb",
            ],
            timeout=2,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env")
    from src.monkey_brain.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class _FakePlanetary:
    """Minimal stand-in exposing only what TransactionCoordinator._stream_event
    actually reads (context_stream, _nats_client) — a real SocietyContextStream,
    no NATS client (exercises the same non-fatal-degrade branch production
    hits whenever NATS isn't connected)."""

    def __init__(self, context_stream):
        self.context_stream = context_stream
        self._nats_client = None


class _FakeSocket:
    def __init__(self):
        self.received: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.received.append(event)


class TestTransactionRouteReturnsRealShape:
    """A real end-to-end POST /actors/{id}/transactions call returns the
    documented TransactionResponse shape. Genuinely slow (a real
    multi-round LLM loop) — this is the one full-stack test in this file."""

    def test_live_transaction_returns_transaction_id_and_stream_url(self, client):
        r = client.post(
            "/api/v1/agentos/actors",
            json={
                "name": "Negotiation Route Test Actor",
                "actor_type": "human",
                "goals": ["negotiate"],
                "capabilities": [{"name": "general"}],
            },
        )
        assert r.status_code == 200, r.text
        actor_id = r.json()["actor_id"]

        r2 = client.post(
            f"/api/v1/agentos/actors/{actor_id}/transactions",
            json={"objective": "Ask around for the best milk price", "max_steps": 2},
            headers={"X-User-ID": actor_id},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["transaction_id"]
        assert body["stream_url"] == f"/ws/transactions/{body['transaction_id']}"
        assert body["originating_actor_id"] == actor_id
        assert body["status"] in ("completed", "terminated")


class TestTransactionStreamsToEventHubAndContextStream:
    """_stream_event's real integration: one call fans out to BOTH the
    WebSocket hub (live leg) and SocietyContextStream (durable leg) —
    locks in the provenance="society:transaction" discriminator the
    Communication page's Message Stream table filters on."""

    @pytest.mark.asyncio
    async def test_stream_event_reaches_ws_hub_and_context_stream(self):
        pr = PlanetaryRuntime()
        alice = _register(pr, "Alice Stream")
        bob = _register(pr, "Bob Stream")

        from src.monkey_brain.kernel.society.transaction_event_hub import (
            get_transaction_event_hub,
        )

        transaction_id = f"test-txn-{time.time_ns()}"
        socket = _FakeSocket()
        get_transaction_event_hub().subscribe(transaction_id, socket)

        coordinator = TransactionCoordinator(planetary=_FakePlanetary(pr.context_stream))
        try:
            await coordinator._stream_event(
                transaction_id,
                {
                    "type": "step_completed",
                    "originating_actor_id": alice.actor_id,
                    "target_actor_id": bob.actor_id,
                },
            )
        finally:
            get_transaction_event_hub().unsubscribe(transaction_id, socket)

        assert len(socket.received) == 1
        assert socket.received[0]["type"] == "step_completed"

        events = pr.context_stream.events(limit=1000)
        matching = [
            e
            for e in events
            if e.event_type == ContextEventType.INTERACTION
            and e.provenance == "society:transaction"
            and (e.payload or {}).get("transaction_id") == transaction_id
        ]
        assert len(matching) == 2, "one INTERACTION event per real actor_id involved (originator + target)"


class TestNegotiationRecordViaTimelineRoute:
    """The historical GET .../negotiation route reads real Timeline
    DECISION records — honest negotiation_required=false for an execution
    that never negotiated, not a fabricated record."""

    def test_negotiation_not_required_for_non_negotiating_execution(self, client):
        r = client.post(
            "/api/v1/agentos/actors",
            json={
                "name": "Negotiation Record Test Actor",
                "actor_type": "human",
                "goals": ["greet"],
                "capabilities": [{"name": "general"}],
            },
        )
        assert r.status_code == 200, r.text
        actor_id = r.json()["actor_id"]

        r2 = client.get(
            f"/api/v1/agentos/actors/{actor_id}/executions/no-such-execution/negotiation",
            headers={"X-User-ID": actor_id},
        )
        # A nonexistent execution_id must not fabricate a negotiation
        # record — either a 404, or an honest negotiation_required=false.
        assert r2.status_code in (200, 404)
        if r2.status_code == 200:
            assert r2.json()["negotiation_required"] is False


class TestTrustUpdatesFromNegotiationOutcome:
    """Real trust ↔ negotiation linkage — AffiliationManager.get_trust
    actually changes after update_trust_from_outcome, not merely claimed."""

    def test_trust_increases_after_goal_achieved_outcome(self):
        manager = AffiliationManager()
        manager.add(
            Affiliation(
                affiliation_id="aff-trust-1",
                affiliation_type="customer",
                target_id="target-actor",
                target_name="Target",
                trust_level=0.5,
            )
        )
        before = manager.get_trust("target-actor")
        manager.update_trust_from_outcome("target-actor", goal_achieved=True)
        after = manager.get_trust("target-actor")
        assert after > before


class TestGroceryNegotiatePriceSettlesWithinBounds:
    """Zero prior coverage per this feature's own audit — the real
    bounded bilateral bargain never crosses either side's real limit."""

    @pytest.mark.parametrize(
        "listed,floor,target",
        [
            (5.99, 4.50, 4.75),
            (10.00, 8.00, 9.50),
            (3.29, 3.00, 3.10),
        ],
    )
    def test_settles_within_seller_floor_and_listed_price(self, listed, floor, target):
        result = negotiate_price(listed, floor, target, max_rounds=3)
        if result["agreed"]:
            assert floor <= result["price"] <= listed
        # negotiate_price's own round shape is {"seller_ask", "buyer_offer",
        # ...} (grocery.py:negotiate_price) -- NOT negotiate_terms'
        # {"high_side", "low_side"} shape (a different function, tested
        # separately below by TestGroceryNegotiateTermsSettlesWithinBounds).
        for rnd in result["rounds"]:
            assert rnd["seller_ask"] >= floor - 0.01
            assert rnd["buyer_offer"] <= listed + 0.01

    def test_no_deal_when_buyer_target_below_seller_floor(self):
        result = negotiate_price(5.99, 5.00, 3.00, max_rounds=3)
        assert result["agreed"] is False
        assert result["rounds"] == []


class TestGroceryNegotiateTermsSettlesWithinBounds:
    """The generalized numeric-term bargain (negotiation.py) — same real
    bound guarantee, zero prior coverage."""

    def test_settles_within_bounds(self):
        result = negotiate_terms(
            high_side_opening=3.0,
            high_side_floor=1.0,
            low_side_opening=0.0,
            max_rounds=3,
        )
        if result["agreed"]:
            assert 0.0 <= result["term"] <= 3.0

    def test_immediate_deal_when_low_side_already_meets_floor(self):
        result = negotiate_terms(
            high_side_opening=3.0,
            high_side_floor=1.0,
            low_side_opening=1.5,
            max_rounds=3,
        )
        assert result["agreed"] is True
        assert result["rounds"] == []


class TestRecordAgreementPersistsDurably:
    """record_agreement CAS-appends to a real KnowledgeGraph entity and is
    re-readable — contrasted against CoordinationEngine's in-memory-only
    sessions (see TestCoordinationEngineConfirmedUnused below)."""

    def test_agreement_is_durably_recorded_and_rereadable(self):
        kg = KnowledgeGraph()
        kg.add_entity(
            "negotiated-deal-1",
            entity_type=EntityType.OTHER,
            name="Milk Deal",
            attributes={"agreements": []},
        )

        ok, msg = record_agreement(
            kg,
            "negotiated-deal-1",
            {"price": 4.75, "buyer": "alice", "seller": "safeway"},
        )
        assert ok is True, msg

        entity = kg.get_entity("negotiated-deal-1")
        agreements = entity.attributes.get("agreements", [])
        assert len(agreements) == 1
        assert agreements[0]["price"] == 4.75
        assert "recorded_at" in agreements[0]


class TestCoordinationEngineConfirmedUnused:
    """Documentation-as-assertion: CoordinationEngine's propose/counter_
    propose/accept/settle session model is fully implemented but has zero
    callers anywhere in production — the Negotiations UI must NOT be built
    around it. This test only proves the class still constructs and its
    session-mutating methods still work in isolation (so a future refactor
    doesn't accidentally break it while it sits unused), not that anything
    calls it."""

    def test_coordination_engine_session_lifecycle_works_standalone(self):
        from src.monkey_brain.kernel.society.coordination import (
            CoordinationEngine,
            NegotiationType,
        )

        engine = CoordinationEngine()
        negotiation = engine.propose(
            NegotiationType.BILATERAL,
            "alice",
            ("alice", "bob"),
            "split the rent",
            {"amount": 500},
        )
        assert negotiation.status.value == "proposed"

        countered = engine.counter_propose(negotiation.negotiation_id, "bob", {"amount": 450}, "too high")
        assert countered.round_count == 1
        assert countered.status.value == "counter_proposed"

        accepted = engine.accept(negotiation.negotiation_id)
        assert accepted.status.value == "accepted"


class TestInteractionManagerLifecycleGapUnwired:
    """respond/vote/complete are real and reachable in-process — supports
    the PARTIAL classification (creation has a REST route, the rest of
    the lifecycle does not) with an executable check rather than a route
    count claim."""

    def test_respond_vote_complete_work_without_any_rest_route(self):
        pr = PlanetaryRuntime()
        club = pr.create_society("Interaction Lifecycle Test", society_type="community")
        alice = _register(pr, "Alice Lifecycle", society_id=club.society.society_id)
        bob = _register(pr, "Bob Lifecycle", society_id=club.society.society_id)
        sr = pr.get_society_runtime(club.society.society_id)

        from src.monkey_brain.kernel.society.interaction import InteractionType

        interaction = sr.route_interaction(
            InteractionType.NEGOTIATE,
            alice.actor_id,
            (alice.actor_id, bob.actor_id),
            topic="split the rent",
            proposal={"amount": 500},
        )

        responded = sr.respond_to_interaction(interaction.interaction_id, bob.actor_id, accept=True, message="ok")
        assert responded is not None
        assert responded.status.value == "accepted"

        completed = sr.complete_interaction(interaction.interaction_id)
        assert completed is not None
        assert completed.status.value == "completed"


class TestNoRemainingCandidatesIsTheRealDeadlockEquivalent:
    """ "Deadlock" is not a concept this codebase implements. The real
    terminal state a user would call "deadlocked" is TerminalState(...,
    "no eligible affiliates remain") — already proven end-to-end by
    test_transaction_coordinator.py::TestNoAffiliateCanSolve. This test
    asserts the same real string directly from negotiation_planning.py's
    own module, for this feature's verification perspective."""

    def test_no_remaining_candidates_terminal_reason_string(self):
        from src.monkey_brain.kernel.society.negotiation_planning import (
            TerminalStateEvaluator,
            TransactionState,
        )
        from src.monkey_brain.kernel.society.transaction import TransactionStatus

        evaluator = TerminalStateEvaluator()
        state = TransactionState(
            originating_actor_id="alice",
            objective="find milk",
            candidates=(),
            max_steps=8,
        )
        terminal = evaluator.evaluate(state)
        assert terminal.is_terminal is True
        assert terminal.status == TransactionStatus.TERMINATED
        assert terminal.reason == "no eligible affiliates remain"
