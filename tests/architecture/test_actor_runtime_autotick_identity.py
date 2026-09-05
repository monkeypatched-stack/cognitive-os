"""Actor Runtime review, Phase 1 (P0): proves the autonomous-tick identity
gap is closed.

Before this fix: PlanetaryRuntime._auto_tick_loop() -> cycle() ->
GeographicEntityRuntime.tick() -> SocietyRuntime.tick_one_actor() ->
_coordinate_actor() -> ActorRuntime.tick() had NO identity-binding call
anywhere in that chain (confirmed by grepping the whole src/monkey_brain
tree for bind_trusted_auth() -- exactly 5 call sites, all request/message
-triggered, none in this chain). In a real deployment with
COGNITIVEOS_ALLOW_INSECURE_DEV_MODE correctly off, any governed capability
reached from an autonomous tick would be denied at ensure_governed's AUTH
stage -- autonomous (non-request-triggered) action was not actually
possible in production, only degraded-looking because dev environments
run with insecure_dev_mode on.

The fix: ActorRuntime.tick() now binds a per-actor service identity
(the same evidence_for_service() pattern subscribe_actor_inbox already
uses) ONLY when nothing better is already bound -- never overwriting a
real caller/workload identity a request-triggered path already
established.
"""
from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.actor_runtime import ActorRuntime
from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    evidence_for_service,
    get_trusted_auth,
    unauthenticated_evidence,
)


@pytest.fixture(autouse=True)
def _reset_identity():
    bind_trusted_auth(unauthenticated_evidence())
    yield
    bind_trusted_auth(unauthenticated_evidence())


class TestAutoTickBindsAFallbackIdentity:
    @pytest.mark.asyncio
    async def test_tick_binds_a_real_actor_scoped_identity_when_nothing_is_bound(self, monkeypatch):
        runtime = ActorRuntime("actor-autotick-1")
        observed = {}

        async def _capture_tick(prompt_request=None):
            observed["evidence"] = get_trusted_auth()
            return "ok"

        monkeypatch.setattr(runtime._cognitive_os, "tick", _capture_tick)

        assert get_trusted_auth().authenticated is False  # the exact pre-fix condition

        await runtime.tick()

        evidence = observed["evidence"]
        assert evidence.authenticated is True
        assert evidence.token_valid is True
        assert evidence.principal_id == "actor-runtime:actor-autotick-1"
        assert evidence.principal_type == "service"

    @pytest.mark.asyncio
    async def test_two_different_actors_get_two_different_identities(self, monkeypatch):
        """Guards against a copy-paste bug binding a fixed string instead
        of interpolating self.actor_id."""
        seen = []

        async def _capture(prompt_request=None):
            seen.append(get_trusted_auth().principal_id)
            return "ok"

        for actor_id in ("actor-A", "actor-B"):
            runtime = ActorRuntime(actor_id)
            monkeypatch.setattr(runtime._cognitive_os, "tick", _capture)
            bind_trusted_auth(unauthenticated_evidence())
            await runtime.tick()

        assert seen == ["actor-runtime:actor-A", "actor-runtime:actor-B"]


class TestRequestTriggeredIdentityIsNeverOverwritten:
    """A request-triggered tick (api/dependencies.py, subscribe_actor_inbox,
    the /execute proxy) has already bound the REAL caller/workload
    identity before ActorRuntime.tick() runs -- the fallback must never
    clobber it."""

    @pytest.mark.asyncio
    async def test_an_already_authenticated_identity_survives_tick(self, monkeypatch):
        runtime = ActorRuntime("actor-with-real-caller")
        real_identity = TrustedAuthEvidence(
            authenticated=True, token_valid=True, principal_id="spiffe://cognitiveos/human/alice",
            principal_type="human", mfa_status="satisfied",
        )
        bind_trusted_auth(real_identity)

        observed = {}

        async def _capture_tick(prompt_request=None):
            observed["evidence"] = get_trusted_auth()
            return "ok"

        monkeypatch.setattr(runtime._cognitive_os, "tick", _capture_tick)

        await runtime.tick()

        assert observed["evidence"] == real_identity
        assert observed["evidence"].principal_id == "spiffe://cognitiveos/human/alice"


class TestAutoTickCanReachRealGovernedExecution:
    """End-to-end: with insecure_dev_mode explicitly OFF (the real
    production posture) and no identity pre-bound (the real autonomous-tick
    starting condition), a governed capability call reached from
    ActorRuntime.tick() must actually pass AUTH and execute -- not merely
    "not crash." Uses local_policy_decision to bypass the live OPA network
    call (a separate, already-covered concern), while exercising the REAL
    ensure_governed AUTH stage this fix targets."""

    @pytest.mark.asyncio
    async def test_governed_capability_succeeds_from_an_autonomous_tick_with_dev_mode_off(self, monkeypatch):
        from src.monkey_brain.kernel.security_boundary import (
            ensure_governed,
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.approval import reset_approval_store

        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        reset_approval_store()
        reset_governed_pipeline_for_tests()

        runtime = ActorRuntime("actor-real-governed")
        calls = {"invoked": 0}

        async def _capability_effect():
            calls["invoked"] += 1
            return {"success": True}

        async def _autonomous_tick_body(prompt_request=None):
            # Stands in for what a real cognitive tick eventually does:
            # reach ensure_governed for a capability call, with no prior
            # identity bound by any caller (the real auto-tick condition).
            return await ensure_governed(
                "capability.grocery.purchase", "grocery.purchase", _capability_effect,
                extra={"capability": "grocery.purchase", "parameters": {}},
                force_authorize=True,
                local_policy_decision={
                    "allowed": True, "approval_mode": "AUTO_APPROVE", "reason": "test",
                    "policy_rule": "test", "risk_level": "LOW", "source": "edge_local_governance",
                },
            )

        monkeypatch.setattr(runtime._cognitive_os, "tick", _autonomous_tick_body)

        assert get_trusted_auth().authenticated is False  # the real starting condition

        result = await runtime.tick()

        assert calls["invoked"] == 1, "the governed capability must actually execute, not merely avoid crashing"
        assert result == {"success": True}

        reset_approval_store()
        reset_governed_pipeline_for_tests()

    @pytest.mark.asyncio
    async def test_without_the_fix_this_would_be_denied(self, monkeypatch):
        """Negative control: proves ensure_governed's AUTH stage really
        does fail closed on unauthenticated evidence with dev mode off --
        i.e. that the fix above is doing real work, not passing by
        accident (e.g. some other relaxation already covering this case)."""
        from src.monkey_brain.kernel.security_boundary import (
            SecurityBoundaryDenied,
            ensure_governed,
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.approval import reset_approval_store

        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        reset_approval_store()
        reset_governed_pipeline_for_tests()
        bind_trusted_auth(unauthenticated_evidence())  # deliberately skip the fix's own binding

        async def _capability_effect():
            return {"success": True}

        with pytest.raises(SecurityBoundaryDenied):
            await ensure_governed(
                "capability.grocery.purchase", "grocery.purchase", _capability_effect,
                extra={"capability": "grocery.purchase", "parameters": {}},
                force_authorize=True,
                local_policy_decision={
                    "allowed": True, "approval_mode": "AUTO_APPROVE", "reason": "test",
                    "policy_rule": "test", "risk_level": "LOW", "source": "edge_local_governance",
                },
            )

        reset_approval_store()
        reset_governed_pipeline_for_tests()
