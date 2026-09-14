"""SPIFFE/SPIRE workload identity layer — security invariant and attack tests.

Scope, stated honestly: pyspiffe is not installed in this environment and
no live SPIRE Server/Agent is deployed for these tests to talk to (see the
SPIFFE/SPIRE Implementation Report's Discovery section) -- every test here
exercises the real Python trust-boundary logic (WorkloadIdentity validation,
evidence_from_spiffe binding, the untrusted-signal strip list, the
production-mode guard on the dev env-var fallback, the Rego recipient-
binding rules, and the NATS actor-inbox Communication Boundary) using a
FAKE WorkloadIdentityProvider standing in for a real Workload API call --
not a live mTLS handshake. This mirrors the same, already-established
convention tests/unit/test_communication_verification.py and
tests/security/test_runtime_approval_gate_wiring.py use for the same
reason (no message broker / SPIRE deployment assumed in CI).
"""

from __future__ import annotations

import json

import pytest

from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    evidence_for_service,
    evidence_from_spiffe,
    get_trusted_auth,
    strip_untrusted_security_signals,
    unauthenticated_evidence,
)
from src.monkey_brain.kernel.workload_identity import (
    WorkloadIdentity,
    WorkloadIdentityError,
    agent_spiffe_id,
    configured_trust_domain,
)


def make_identity(spiffe_id: str, source: str = "spire") -> WorkloadIdentity:
    return WorkloadIdentity(spiffe_id=spiffe_id, trust_domain="cognitiveos.local", source=source)


class TestCanonicalIdentityModel:
    """Phase 2: canonical spiffe://<trust-domain>/agent/<id> URIs, a
    configurable (not hard-coded) trust domain."""

    def test_agent_spiffe_id_uses_configured_trust_domain(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "example.test")
        assert agent_spiffe_id("lending-decision") == "spiffe://example.test/agent/lending-decision"

    def test_trust_domain_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("SPIFFE_TRUST_DOMAIN", raising=False)
        assert configured_trust_domain() == "cognitiveos.local"

    def test_dev_and_prod_trust_domains_are_independently_configurable(self, monkeypatch):
        """Section 5: dev and prod trust domains must not silently overlap
        -- proven here by showing the value is entirely operator-controlled
        per-environment, not derived from anything code-level."""
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "cognitiveos-dev.local")
        dev_domain = configured_trust_domain()
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "cognitiveos.production")
        prod_domain = configured_trust_domain()
        assert dev_domain != prod_domain


class TestWorkloadIdentityValidation:
    """SPIFFE_ID_COMES_FROM_VERIFIED_CREDENTIAL, at the type level: a
    WorkloadIdentity cannot be constructed with a malformed/empty id."""

    def test_valid_construction(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/a")
        assert identity.spiffe_id == "spiffe://cognitiveos.local/agent/a"

    def test_rejects_empty_spiffe_id(self):
        with pytest.raises(WorkloadIdentityError):
            WorkloadIdentity(spiffe_id="", trust_domain="cognitiveos.local", source="spire")

    def test_rejects_non_spiffe_uri(self):
        """An agent's own name/agent_id string ("agent-a") must never pass
        as a SPIFFE identity even if handed directly to this constructor."""
        with pytest.raises(WorkloadIdentityError):
            WorkloadIdentity(spiffe_id="agent-a", trust_domain="cognitiveos.local", source="spire")

    def test_rejects_unknown_source(self):
        with pytest.raises(WorkloadIdentityError):
            WorkloadIdentity(
                spiffe_id="spiffe://cognitiveos.local/agent/a",
                trust_domain="cognitiveos.local",
                source="message",
            )

    def test_spire_source_is_cryptographically_verified(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/a", source="spire")
        assert identity.is_cryptographically_verified is True

    def test_env_source_is_not_cryptographically_verified(self):
        """CERTIFICATE_VALIDITY_DOES_NOT_IMPLY_AUTHORIZATION's sibling
        invariant: an env-var identity string proves nothing
        cryptographically, and callers that need real proof must be able
        to tell the difference."""
        identity = make_identity("spiffe://cognitiveos.local/agent/a", source="env")
        assert identity.is_cryptographically_verified is False


class TestEvidenceFromSpiffe:
    """evidence_from_spiffe() is the ONLY sanctioned path from a verified
    WorkloadIdentity into TrustedAuthEvidence -- kernel/trusted_auth.py."""

    def test_binds_spiffe_id_and_verified_flag(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/lending-decision", source="spire")
        evidence = evidence_from_spiffe(identity)
        assert evidence.authenticated is True
        assert evidence.spiffe_id == "spiffe://cognitiveos.local/agent/lending-decision"
        assert evidence.spiffe_verified is True
        assert evidence.principal_id == "spiffe://cognitiveos.local/agent/lending-decision"

    def test_env_sourced_identity_is_marked_unverified(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/a", source="env")
        evidence = evidence_from_spiffe(identity)
        assert evidence.spiffe_verified is False

    def test_to_opa_auth_carries_spiffe_fields(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/a", source="spire")
        evidence = evidence_from_spiffe(identity)
        opa_auth = evidence.to_opa_auth()
        assert opa_auth["spiffe_id"] == "spiffe://cognitiveos.local/agent/a"
        assert opa_auth["spiffe_verified"] is True

    def test_evidence_without_spiffe_has_empty_spiffe_fields(self):
        """Regression guard: every pre-existing evidence constructor
        (evidence_for_service, evidence_from_jwt, unauthenticated_evidence)
        must keep working unchanged -- spiffe_id/spiffe_verified default
        to falsy, never fabricated."""
        evidence = evidence_for_service("actor-runtime:bob")
        assert evidence.spiffe_id == ""
        assert evidence.spiffe_verified is False


class TestMessageSenderCannotAssertIdentity:
    """SPIFFE_ID_DOES_NOT_IMPLY_AUTHORIZATION's sibling: an agent-controlled
    dict can never inject spiffe_id/recipient_spiffe_id into trusted
    context -- MESSAGE_SENDER_CANNOT_ASSERT_IDENTITY."""

    def test_strip_drops_spiffe_claims_from_agent_content(self):
        cleaned = strip_untrusted_security_signals(
            {
                "spiffe_id": "spiffe://cognitiveos.local/agent/root",
                "spiffe_verified": True,
                "sender_spiffe_id": "spiffe://cognitiveos.local/agent/root",
                "recipient_spiffe_id": "spiffe://cognitiveos.local/agent/victim",
                "question": "buy milk",
            }
        )
        assert "spiffe_id" not in cleaned
        assert "spiffe_verified" not in cleaned
        assert "sender_spiffe_id" not in cleaned
        assert "recipient_spiffe_id" not in cleaned
        assert cleaned["question"] == "buy milk"

    def test_build_opa_input_ignores_agent_supplied_spiffe_claims(self):
        from src.monkey_brain.kernel.security_boundary import build_opa_input

        bind_trusted_auth(unauthenticated_evidence())
        opa_input = build_opa_input(
            action="capability.AskActor",
            resource="agent-b",
            extra={
                "spiffe_id": "spiffe://cognitiveos.local/agent/forged",
                "question": "hi",
            },
        )
        assert opa_input["auth"]["spiffe_id"] == ""  # from the REAL (unauthenticated) evidence, not extra
        assert "spiffe_id" not in opa_input["context"]

    def test_build_opa_input_recipient_only_from_explicit_kwarg(self):
        """Only the explicit recipient_spiffe_id keyword argument (a
        trusted caller's own resolved value) can populate it -- never
        agent-supplied `extra` content, even if shaped identically."""
        from src.monkey_brain.kernel.security_boundary import build_opa_input

        bind_trusted_auth(unauthenticated_evidence())
        opa_input = build_opa_input(
            action="capability.AskActor",
            resource="agent-b",
            extra={"recipient_spiffe_id": "spiffe://cognitiveos.local/agent/forged-recipient"},
            recipient_spiffe_id="spiffe://cognitiveos.local/agent/real-recipient",
        )
        assert opa_input["context"]["recipient_spiffe_id"] == "spiffe://cognitiveos.local/agent/real-recipient"
        assert opa_input["recipient_spiffe_id"] == "spiffe://cognitiveos.local/agent/real-recipient"


class TestProductionModeGuardOnDevOverride:
    """The real gap found in discovery: spire_client.fetch_svid()'s
    SPIFFE_ID env override previously had no production-mode guard at
    all -- Non-negotiable #12 (unknown/unauthenticated workload identity
    must not communicate in production) and Phase 26 (never silently fall
    back from SPIFFE to self-asserted identity in production)."""

    @pytest.mark.asyncio
    async def test_env_override_refused_under_production_mode(self, monkeypatch):
        from cerebellum.capabilities.security import spire_client

        monkeypatch.setenv("SPIFFE_ID", "spiffe://cognitiveos.local/agent/dev-only")
        monkeypatch.setenv("COGNITIVEOS_PRODUCTION_MODE", "true")
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setattr(spire_client, "_SOCKET", "")
        monkeypatch.setattr(spire_client, "_STATIC_ID", "spiffe://cognitiveos.local/agent/dev-only")

        result = await spire_client.fetch_svid()
        assert result is None, "production mode must never accept a self-declared SPIFFE_ID env override"

    @pytest.mark.asyncio
    async def test_env_override_refused_without_explicit_insecure_dev_mode(self, monkeypatch):
        """Even outside production mode, the override requires the SAME
        explicit insecure-dev opt-in every other relaxation in this
        codebase requires -- it is not a bare "if unset, allow" default."""
        from cerebellum.capabilities.security import spire_client

        monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setattr(spire_client, "_SOCKET", "")
        monkeypatch.setattr(spire_client, "_STATIC_ID", "spiffe://cognitiveos.local/agent/dev-only")

        result = await spire_client.fetch_svid()
        assert result is None

    @pytest.mark.asyncio
    async def test_env_override_permitted_under_explicit_insecure_dev_mode(self, monkeypatch):
        from cerebellum.capabilities.security import spire_client

        monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setattr(spire_client, "_SOCKET", "")
        monkeypatch.setattr(spire_client, "_STATIC_ID", "spiffe://cognitiveos.local/agent/dev-only")

        result = await spire_client.fetch_svid()
        assert result is not None
        assert result["spiffe_id"] == "spiffe://cognitiveos.local/agent/dev-only"
        assert result["source"] == "env"


class _FakeNatsClient:
    def __init__(self) -> None:
        self.callback = None

    async def subscribe(self, subject, cb):
        self.callback = cb


class _FakeMsg:
    def __init__(self, payload: dict) -> None:
        self.data = json.dumps(payload).encode()
        self.reply = None
        self.responses: list[bytes] = []

    async def respond(self, data: bytes) -> None:
        self.responses.append(data)


class TestCommunicationBoundaryEnforcement:
    """NO_UNAUTHENTICATED_AGENT_COMMUNICATION at the real Communication
    Boundary -- kernel/domains/grocery.py::subscribe_actor_inbox's
    _on_message, the actual receiving side of every AskActor/DelegateTask/
    broadcast message."""

    @pytest.mark.asyncio
    async def test_forged_sender_field_does_not_change_bound_identity(self, monkeypatch):
        """Forged sender attack (Phase 23): the message CLAIMS to be from
        "agent-a" (or any other actor) -- the bound principal must be the
        RESPONDING actor's own identity regardless, never derived from
        message content."""
        from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox
        from src.monkey_brain.kernel import workload_identity as wi_module

        wi_module.reset_workload_identity_provider_for_tests()
        monkeypatch.delenv("COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY", raising=False)
        monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)

        class _FakePR:
            _nats_client = _FakeNatsClient()
            memory_manager = None

        pr = _FakePR()
        await subscribe_actor_inbox(pr, "real-actor-b", "Actor B")

        msg = _FakeMsg(
            {
                "msg_type": "broadcast",
                "message": "hi",
                "from_actor_id": "agent-c-impersonating",
            }
        )
        await pr._nats_client.callback(msg)

        evidence = get_trusted_auth()
        # No real SPIFFE identity available (fake provider returns None by
        # default) and SPIFFE not required here -> falls back to the
        # existing per-actor service evidence, bound to the RESPONDING
        # actor (real-actor-b), never to the forged from_actor_id.
        assert evidence.principal_id == "actor-runtime:real-actor-b"
        assert "agent-c-impersonating" not in evidence.principal_id

    @pytest.mark.asyncio
    async def test_refuses_communication_when_spiffe_required_and_unavailable(self, monkeypatch):
        """SPIRE unavailable -> DENY for a communication path that
        requires SPIFFE (Phase 23's last attack test, Non-negotiable #12):
        never silently fall back to the service-name evidence when SPIFFE
        identity is explicitly required."""
        from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox
        from src.monkey_brain.kernel import workload_identity as wi_module

        wi_module.reset_workload_identity_provider_for_tests()
        monkeypatch.setenv("COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY", "true")
        bind_trusted_auth(unauthenticated_evidence())

        class _FakePR:
            _nats_client = _FakeNatsClient()
            memory_manager = None

        pr = _FakePR()
        await subscribe_actor_inbox(pr, "actor-needs-spiffe", "Actor")

        msg = _FakeMsg({"msg_type": "broadcast", "message": "hi"})
        msg.reply = "reply-subject"  # asker expects a reply, same as a real NATS request/reply call
        await pr._nats_client.callback(msg)

        evidence = get_trusted_auth()
        assert evidence.authenticated is False, "must NOT fall back to a self-asserted service identity"
        assert len(msg.responses) == 1
        body = json.loads(msg.responses[0])
        assert body["success"] is False
        assert "unauthenticated" in body["error"] or "verified" in body["error"]

    @pytest.mark.asyncio
    async def test_uses_real_verified_identity_when_available(self, monkeypatch):
        """The positive path: when a real WorkloadIdentity IS available,
        it is used (and marked verified) instead of the plain service
        fallback."""
        from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox
        from src.monkey_brain.kernel import workload_identity as wi_module

        class _FakeProvider:
            async def get_current_identity(self):
                return WorkloadIdentity(
                    spiffe_id="spiffe://cognitiveos.local/agent/real-actor-b",
                    trust_domain="cognitiveos.local",
                    source="spire",
                )

        monkeypatch.setattr(wi_module, "get_workload_identity_provider", lambda: _FakeProvider())
        monkeypatch.delenv("COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY", raising=False)

        from src.monkey_brain.kernel.domains.grocery import (
            subscribe_actor_inbox as sub2,
        )

        class _FakePR:
            _nats_client = _FakeNatsClient()
            memory_manager = None

        pr = _FakePR()
        await sub2(pr, "real-actor-b", "Actor B")
        msg = _FakeMsg({"msg_type": "broadcast", "message": "hi"})
        await pr._nats_client.callback(msg)

        evidence = get_trusted_auth()
        assert evidence.spiffe_id == "spiffe://cognitiveos.local/agent/real-actor-b"
        assert evidence.spiffe_verified is True
        wi_module.reset_workload_identity_provider_for_tests()


class TestAgentCannotSelfApprove:
    """AGENT_CANNOT_SELF_APPROVE still holds when the principal is a
    SPIFFE-identified agent, not just a plain service-name one --
    kernel/approval.py::prevent_self_approval doesn't care about the
    principal string's shape, but this pins that a SPIFFE URI is handled
    identically to any other principal id."""

    def test_spiffe_principal_cannot_approve_its_own_request(self):
        from src.monkey_brain.kernel.approval import prevent_self_approval

        spiffe_principal = "spiffe://cognitiveos.local/agent/lending-decision"
        is_valid, reason = prevent_self_approval(spiffe_principal, spiffe_principal)
        assert is_valid is False
        assert "self-approval" in reason


class TestSpiffeIdentityDoesNotImplyAuthorization:
    """Phase 9/29: an authentic SPIFFE ID proves WHO, never WHAT. This
    class has no is_allowed()/can_execute() method at all -- proven by
    absence, matching the same pattern used for ApprovalDecision."""

    def test_workload_identity_has_no_authorization_method(self):
        identity = make_identity("spiffe://cognitiveos.local/agent/lending-decision")
        for forbidden in (
            "is_allowed",
            "can_execute",
            "authorize",
            "permissions",
            "capabilities",
            "scopes",
        ):
            assert not hasattr(identity, forbidden), f"WorkloadIdentity must never carry {forbidden!r}"

    def test_provider_has_no_authorization_method(self):
        from src.monkey_brain.kernel.workload_identity import WorkloadIdentityProvider

        provider = WorkloadIdentityProvider()
        for forbidden in ("is_allowed", "can_execute", "authorize"):
            assert not hasattr(provider, forbidden)
