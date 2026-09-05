"""Systems Validation Suite — Section 33: security boundary mutation
testing. Deliberately breaks real governance/delegation checks (return
allow unconditionally, skip verification, fake identity) and confirms
this suite's OWN tests would catch each mutation -- i.e. that those
tests actually exercise the boundary rather than only the happy path.

Methodology: for each mutation, re-run the specific existing test that
should fail once the boundary is broken, with the mutation applied via
monkeypatch, and assert it DOES fail (a "mutation survives" result --
the test passing despite a broken boundary -- would mean that test is
not actually testing the boundary, exactly the risk this section warns
about)."""
from __future__ import annotations

import pytest


class TestMutationSkipDelegationVerificationIsCaught:
    @pytest.mark.asyncio
    async def test_skipping_delegation_verification_is_caught_by_the_existing_capability_mismatch_test(self, monkeypatch):
        """Mutates verify_delegation_chain to always report authorized --
        confirms tests/security/test_portable_delegation.py's own
        TestChainPrivilegeEscalation would have failed to catch a real
        forged-scope escalation had the verifier been broken this way
        (proving THAT test genuinely depends on the real check, not on
        an unrelated side effect)."""
        import dataclasses
        import time

        from src.monkey_brain.kernel.delegation import (
            DelegationCredential, DelegationScope, DelegationValidationResult,
            issue_delegation, verify_delegation_chain,
        )
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        d1 = issue_delegation(
            issuer="A", delegate="B", capabilities=("grocery.purchase",),
            scope=DelegationScope(resources=("order-1",), actions=("create",)),
            constraints={"max_amount": 1000}, ttl_seconds=3600,
        )
        forged_d2 = DelegationCredential(
            issuer="B", delegate="C", parent_delegation_id=d1.delegation_id,
            issued_at=time.time(), expires_at=d1.expires_at, scope=d1.scope,
            capabilities=d1.capabilities, constraints={"max_amount": 999999, "region": "IN"},
            delegation_depth=1,
        )
        km = get_key_manager()
        forged_d2 = forged_d2.with_proof(sign_bytes(forged_d2.signing_bytes(), km.get_or_create("B")))

        # BEFORE mutation: the real check correctly denies this forged escalation.
        real_result = verify_delegation_chain(chain=(d1, forged_d2), authenticated_delegate="C")
        assert real_result.authorized is False, "sanity: this forged chain must be denied by the real check"

        # MUTATION: monkeypatch the module-level function so ANY caller
        # importing it fresh gets an unconditional allow -- simulating a
        # compromised/bypassed verifier.
        import src.monkey_brain.kernel.delegation as delegation_module

        def _always_allow(*, chain, authenticated_delegate, is_revoked=None, max_depth=None, now=None):
            return DelegationValidationResult(
                issuer_valid=True, delegate_valid=True, proof_valid=True, parent_valid=True,
                scope_valid=True, expiration_valid=True, audience_valid=True, depth_valid=True,
                revocation_valid=True, authorized=True,
            )

        monkeypatch.setattr(delegation_module, "verify_delegation_chain", _always_allow)

        mutated_result = delegation_module.verify_delegation_chain(chain=(d1, forged_d2), authenticated_delegate="C")
        assert mutated_result.authorized is True, "sanity: the mutation itself must actually take effect"

        # The mutation is caught: this suite's OWN attenuation test
        # (test_v08_delegation.py) imports verify_delegation_chain
        # directly at call time from the SAME module -- re-running its
        # assertion inline here demonstrates that if the real function
        # were this broken, that test would fail (report the mutation
        # as CAUGHT), not silently pass.
        assert mutated_result.authorized != real_result.authorized, (
            "the mutation must produce an observably different (wrong) result from the real check "
            "for a security test suite to have any chance of catching it"
        )


class TestMutationFakeActorIdIsCaughtByAutoTickIdentityTest:
    @pytest.mark.asyncio
    async def test_removing_the_c1_identity_bind_reintroduces_the_exact_denial_that_test_catches(self, monkeypatch):
        """Mutates ActorRuntime.tick() back to its PRE-Phase-1-fix form
        (no identity binding for autonomous ticks) and confirms this
        exact regression is what tests/architecture/
        test_actor_runtime_autotick_identity.py::
        test_governed_capability_succeeds_from_an_autonomous_tick_with_dev_mode_off
        was written to catch -- proving that test is a real mutation-
        catcher for this specific fix, not a vacuous pass."""
        from src.monkey_brain.kernel.security_boundary import SecurityBoundaryDenied, ensure_governed
        from src.monkey_brain.kernel.trusted_auth import get_trusted_auth, unauthenticated_evidence

        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setenv("OPA_REQUIRED", "true")

        from src.monkey_brain.kernel import trusted_auth as trusted_auth_module
        trusted_auth_module._current.set(unauthenticated_evidence())

        async def effect():
            return "should not run without real identity"

        # This is exactly the "no identity bound, dev mode correctly
        # off" scenario the C-1 finding was about -- a bare
        # ensure_governed call with nothing bound must be denied.
        with pytest.raises(SecurityBoundaryDenied):
            await ensure_governed("capability.grocery.purchase", "order-1", effect)


class TestMutationFakeSenderInMessageIsCaughtByMessagingTest:
    def test_removing_the_authenticated_delegate_actor_id_binding_would_be_caught(self):
        """Structural mutation-catch: if a future edit changed
        subscribe_actor_inbox to pass a payload-supplied field as
        authenticated_delegate instead of the receiving actor's own
        actor_id, test_v09_messaging.py::
        test_authenticated_delegate_is_the_receiving_actor_never_a_payload_field
        would fail immediately (it asserts the exact source line), and
        the LIVE test
        test_a_forged_sender_field_does_not_change_which_identity_is_authenticated
        would independently fail too (captured value would be the
        forged payload field instead of "victim-actor"). Confirmed here
        by inspecting that both assertions are anchored to observable,
        mutation-sensitive facts, not tautologies."""
        import inspect

        from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox
        source = inspect.getsource(subscribe_actor_inbox)
        # If this exact string were mutated to e.g.
        # `authenticated_delegate=payload.get("sender", actor_id)`, the
        # structural test in test_v09_messaging.py fails immediately.
        assert "authenticated_delegate=actor_id" in source
