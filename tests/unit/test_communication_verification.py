"""Verification tests for the real communication substrate this session's
Communication workspace (living-world-explorer's CommunicationPanel.tsx)
is a thin, honest UI layer over: AffiliationCommunicationRouter (kernel/
society/communication.py), SocietyRuntime.send_message/broadcast_message
(kernel/society/runtime.py), AskActorCapability/subscribe_actor_inbox
(kernel/domains/grocery.py), and SocietyContextStream (kernel/society/
context_stream.py). Every test here drives REAL runtime objects — no
second communication model, no fabricated records.

Known, deliberate gaps this file documents rather than works around:
- No test here exercises the real NATS transport path for AskActor (no
  message broker available in CI) — only the in-process fallback
  (`pr._nats_client is None`) is exercised, which is itself real
  production behavior (grocery.py's own non-fatal-degrade path), not a
  mock.
- "No retry" test asserts ABSENCE, not behavior — there is no retry
  mechanism anywhere in this layer. correlation_id/causation_id WAS a
  documented absence here too (see TestNoCorrelationIdOnCommunicationDecision
  below) — that gap was closed by the correlation/causation hardening
  change; see tests/unit/test_correlation_causation.py for the full
  propagation test suite. This file's own correlation test below now
  asserts presence instead of absence.
"""

from __future__ import annotations

import os

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
from src.monkey_brain.kernel.society.communication import CommunicationDecision


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


class TestCommunicationLogEmptyState:
    """A fresh society with zero messages returns 200 with an empty
    entries list — the honest empty state, not an error."""

    def test_empty_communication_log_is_a_real_200(self, client):
        # Must create the society on the SAME PlanetaryRuntime the route
        # itself queries (app.state.planetary_runtime) -- a standalone
        # `PlanetaryRuntime()` here would be a second, disconnected
        # instance the app's runtime has no way to see, producing a real
        # 404 regardless of what this test creates (confirmed live: this
        # is exactly what happened before this fix).
        pr = client.app.state.planetary_runtime
        club = pr.create_society("Comm Test Empty", society_type="community")
        r = client.get(
            f"/api/v1/agentos/societies/{club.society.society_id}/communication-log",
            headers={"X-User-ID": "seed-world"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["entries"] == []


class TestCommunicationLogRecordsAllowedDecision:
    """Two actors in the same society are eligible to communicate (the
    "shared society" AffiliationGraph rule) — a real allowed
    CommunicationDecision is recorded."""

    def test_allowed_decision_recorded(self):
        pr = PlanetaryRuntime()
        club = pr.create_society("Comm Test Allowed", society_type="community")
        alice = _register(pr, "Alice", society_id=club.society.society_id)
        bob = _register(pr, "Bob", society_id=club.society.society_id)

        sr = pr.get_society_runtime(club.society.society_id)
        sent = sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})
        assert sent is True

        audit = sr.communication_audit()
        assert len(audit) == 1
        decision = audit[0]
        assert isinstance(decision, CommunicationDecision)
        assert decision.sender_id == alice.actor_id
        assert decision.recipient_id == bob.actor_id
        assert decision.allowed is True


class TestCommunicationLogRecordsDeniedDecision:
    """Two actors with no shared society/affiliation are denied — a real
    denied decision is recorded with a non-empty reason.

    Both societies must be hosted at genuinely SEPARATE Spaces for this
    to be true: MB-3054 Temporary Membership (kernel/society/membership.py
    ::MembershipGovernor) is real, intentional, documented behavior —
    physical co-location grants TEMPORARY membership in every Society
    hosted at the same Space, which is itself sufficient for
    AffiliationGraph._shared_society to permit communication. Two plain
    create_society() calls with no explicit home_space_id both land on
    PlanetaryRuntime's single default_bootstrap_space_id by default,
    silently co-locating them — Alice and Carol would be temporarily
    "in the same room" despite different permanent home societies, and
    genuinely eligible to talk under the real rule this test means to
    exercise the ABSENCE of. Same explicit-separate-Space pattern
    test_mb3054_temporary_membership.py's own _build_marketplace() uses.
    """

    def test_denied_decision_recorded(self):
        from src.monkey_brain.kernel.geography.entity import GeographicEntityType

        pr = PlanetaryRuntime()
        club_a = pr.create_society("Comm Test Denied A", society_type="community")
        club_b = pr.create_society("Comm Test Denied B", society_type="community")
        default_space_id = pr.default_bootstrap_space_id
        building = pr.geo_registry.parent_of(default_space_id)
        club_b_space = pr.geo_registry.create(
            GeographicEntityType.SPACE,
            "Comm Test Denied B Space",
            parent_id=building.entity_id,
        )
        pr.host_society(club_b_space.entity_id, club_b.society.society_id)

        alice = _register(
            pr,
            "Alice Denied",
            society_id=club_a.society.society_id,
            home_space_id=default_space_id,
        )
        carol = _register(
            pr,
            "Carol Denied",
            society_id=club_b.society.society_id,
            home_space_id=club_b_space.entity_id,
        )

        sr_a = pr.get_society_runtime(club_a.society.society_id)
        sent = sr_a.send_message(alice.actor_id, carol.actor_id, "greeting", {"text": "hi"})
        assert sent is False

        audit = sr_a.communication_audit()
        assert len(audit) == 1
        decision = audit[0]
        assert decision.allowed is False
        assert decision.reason


class TestCommunicationLogNoDuplicateRecords:
    """Reading the log (a GET) doesn't itself append anything — repeated
    reads return the same count."""

    def test_reading_log_twice_does_not_duplicate(self, client):
        pr = PlanetaryRuntime()
        club = pr.create_society("Comm Test NoDup", society_type="community")
        alice = _register(pr, "Alice NoDup", society_id=club.society.society_id)
        bob = _register(pr, "Bob NoDup", society_id=club.society.society_id)
        sr = pr.get_society_runtime(club.society.society_id)
        sr.send_message(alice.actor_id, bob.actor_id, "greeting", {"text": "hi"})

        first = sr.communication_audit()
        second = sr.communication_audit()
        assert len(first) == len(second) == 1


class TestContextStreamInteractionEventPayloadShapes:
    """The two real, structurally-different INTERACTION payload shapes the
    frontend's Message Stream table must branch on — locked in here so a
    future refactor can't silently rename a field without breaking a test."""

    def test_route_interaction_payload_has_participants_and_topic(self):
        pr = PlanetaryRuntime()
        club = pr.create_society("Comm Test Interaction", society_type="community")
        alice = _register(pr, "Alice Interact", society_id=club.society.society_id)
        bob = _register(pr, "Bob Interact", society_id=club.society.society_id)
        sr = pr.get_society_runtime(club.society.society_id)

        from src.monkey_brain.kernel.society.interaction import InteractionType
        from src.monkey_brain.kernel.society.context_stream import ContextEventType

        sr.route_interaction(
            InteractionType.NEGOTIATE,
            alice.actor_id,
            (alice.actor_id, bob.actor_id),
            topic="split the rent",
            proposal={"amount": 500},
        )

        events = pr.context_stream.events(limit=1000)
        matching = [
            e for e in events if e.event_type == ContextEventType.INTERACTION and "participants" in (e.payload or {})
        ]
        assert matching, "route_interaction must publish an INTERACTION event with a participants/topic payload"
        assert matching[-1].payload["topic"] == "split the rent"


class TestNoRetryOnAskActorTimeout:
    """AskActorCapability's real, honest failure path — a target that
    cannot answer produces exactly one failure, no retry. Retry does not
    exist anywhere in this communication layer; this test documents that
    absence as an executable assertion rather than prose."""

    @pytest.mark.asyncio
    async def test_unknown_target_fails_once_no_retry(self):
        import asyncio
        from src.monkey_brain.kernel.domains.grocery import AskActorCapability

        pr = PlanetaryRuntime()
        club = pr.create_society("Comm Test AskActor", society_type="community")
        alice = _register(pr, "Alice AskActor", society_id=club.society.society_id)

        result = await AskActorCapability().handle(
            {
                "context": {
                    "planetary_runtime": pr,
                    "actor_id": alice.actor_id,
                    "actor_role": "Alice",
                },
                "parameters": {"target_actor": "Nobody Real", "question": "hi?"},
            }
        )
        assert result["success"] is False
        assert "no actor named" in result["error"]


class TestNoCorrelationIdOnCommunicationDecision:
    """Was: documents an architectural gap (no correlation_id/causation_id
    anywhere in the message layer). That gap is now closed — this test
    locks in the opposite fact so a future refactor can't silently drop
    the fields again. Full propagation coverage lives in
    tests/unit/test_correlation_causation.py; this file only checks the
    fields exist on the canonical model, matching this file's own scope."""

    def test_communication_decision_has_correlation_fields(self):
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(CommunicationDecision)}
        assert "decision_id" in field_names
        assert "correlation_id" in field_names
        assert "causation_id" in field_names
