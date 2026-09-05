"""Systems Validation Suite — Section 32: property-based testing.

`hypothesis` is not installed in this environment (confirmed:
`ModuleNotFoundError`) and this pass does not add a new dependency on
its own initiative -- these properties are instead checked with manual,
seeded randomized generation (Python's own `random`, fixed seed for
reproducibility), which is the same principle (arbitrary valid inputs,
not hand-picked examples) without a new third-party dependency.
"""
from __future__ import annotations

import random

from src.monkey_brain.kernel.delegation import (
    DelegationScope, issue_delegation, reset_delegation_store_for_tests,
)
from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState
from src.monkey_brain.kernel.society.domain import ActorStatus


class TestDelegationAttenuationPropertyHoldsForArbitraryScopes:
    """For arbitrary valid parent/child scopes: child amount/region/ttl
    must never exceed the parent's."""

    def test_1000_random_child_delegations_never_exceed_their_parents_bounds(self):
        rng = random.Random(20260906)
        violations = []
        for i in range(1000):
            reset_delegation_store_for_tests()
            parent_amount = rng.randint(100, 100_000)
            parent_ttl = rng.randint(60, 86_400)
            parent = issue_delegation(
                issuer="A", delegate="B", capabilities=("grocery.purchase",),
                scope=DelegationScope(resources=("order-1",), actions=("create",)),
                constraints={"max_amount": parent_amount, "region": "IN"}, ttl_seconds=parent_ttl,
            )
            # A valid, randomly-generated NARROWER child (issue_delegation
            # itself enforces the property at construction -- this
            # confirms it holds for 1000 random valid attempts, not just
            # hand-picked ones).
            child_amount = rng.randint(1, parent_amount)
            child_ttl = rng.randint(1, parent_ttl)
            child = issue_delegation(
                issuer="B", delegate="C", capabilities=("grocery.purchase",),
                scope=parent.scope, constraints={"max_amount": child_amount, "region": "IN"},
                ttl_seconds=child_ttl, parent=parent,
            )
            if child.constraints["max_amount"] > parent.constraints["max_amount"]:
                violations.append(("amount", i, child.constraints["max_amount"], parent.constraints["max_amount"]))
            if child.expires_at > parent.expires_at:
                violations.append(("expiry", i, child.expires_at, parent.expires_at))
        assert violations == [], f"delegation attenuation violated for {len(violations)}/1000 random cases: {violations[:5]}"

    def test_1000_random_over_widening_attempts_are_all_rejected(self):
        from src.monkey_brain.kernel.delegation import DelegationDeniedError

        rng = random.Random(20260906)
        not_rejected = []
        for i in range(1000):
            reset_delegation_store_for_tests()
            parent_amount = rng.randint(100, 100_000)
            parent = issue_delegation(
                issuer="A", delegate="B", capabilities=("grocery.purchase",),
                scope=DelegationScope(resources=("order-1",), actions=("create",)),
                constraints={"max_amount": parent_amount, "region": "IN"}, ttl_seconds=3600,
            )
            # Deliberately WIDER than the parent, by a random positive amount.
            widened_amount = parent_amount + rng.randint(1, 50_000)
            try:
                issue_delegation(
                    issuer="B", delegate="C", capabilities=("grocery.purchase",),
                    scope=parent.scope, constraints={"max_amount": widened_amount, "region": "IN"},
                    ttl_seconds=1800, parent=parent,
                )
                not_rejected.append((i, parent_amount, widened_amount))
            except DelegationDeniedError:
                pass
        assert not_rejected == [], f"{len(not_rejected)}/1000 over-widened delegations were NOT rejected: {not_rejected[:5]}"


class TestActorIdentityPropertyHoldsForArbitraryPlacementSequences:
    """For arbitrary sequences of migrations, actor_id remains constant."""

    def test_actor_id_survives_20_random_migrations_in_a_row(self):
        from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        from .conftest import FakeRedis, force_redis_authoritative, register

        rng = random.Random(20260906)
        shared = FakeRedis()
        node_ids = [f"node-{i}" for i in range(5)]
        prs = {}
        for nid in node_ids:
            pr = PlanetaryRuntime(); pr._redis = shared; pr._node_id = nid
            force_redis_authoritative(pr)
            prs[nid] = pr
        prs[node_ids[0]].register_node(ExecutionNode(node_id=node_ids[0], capacity=100))
        for nid in node_ids[1:]:
            prs[node_ids[0]].register_node(ExecutionNode(node_id=nid, capacity=100))

        state = register(prs[node_ids[0]], "PropertyActor")
        original_id = state.actor_id
        current_node = node_ids[0]
        prs[current_node].set_actor_desired_node(original_id, current_node)
        assert prs[current_node].lifecycle.reconcile(original_id).action == "start"

        for _ in range(20):
            candidates = [n for n in node_ids if n != current_node]
            target = rng.choice(candidates)
            decision = prs[current_node].scheduler.migrate_actor(original_id, target_node_id=target)
            assert decision.node_id == target
            resume = prs[target].lifecycle.reconcile(original_id)
            assert resume.action == "resume"
            assert resume.actor_id == original_id, "actor_id changed across a migration -- property violated"
            current_node = target

        assert prs[current_node].locate_actor(original_id).actor_id == original_id


class TestExecutionNeverBypassesGovernanceForArbitraryPlans:
    """For arbitrary generated action plans: no action bypasses
    governance. Generated here as random capability names/resources
    fed through the real ActionExecutor -> ensure_governed boundary,
    checking every one is denied under a fail-closed policy (rather
    than some being silently allowed by accident of naming)."""

    def test_100_random_capability_resource_pairs_all_require_real_authorization(self):
        import asyncio

        from src.monkey_brain.kernel.approval import reset_approval_store
        from src.monkey_brain.kernel.security_boundary import (
            SecurityBoundaryDenied, ensure_governed, reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth

        rng = random.Random(20260906)

        async def _deny_everything(action, resource, extra, *, verified_delegation=None):
            return {"allowed": False, "approval_mode": "DENY", "reason": "property-test fail-closed default"}

        async def _run_one(capability: str, resource: str):
            reset_approval_store()
            reset_governed_pipeline_for_tests()
            bind_trusted_auth(TrustedAuthEvidence(
                authenticated=True, token_valid=True, principal_id="property-actor",
                principal_type="service", mfa_status="satisfied",
            ))
            ran = {"value": False}

            async def effect():
                ran["value"] = True
                return "ok"

            import src.monkey_brain.kernel.security_boundary as sb
            sb._authorize = _deny_everything
            try:
                await ensure_governed(f"capability.{capability}", resource, effect)
                return ran["value"], False  # executed, no exception -- a bypass
            except SecurityBoundaryDenied:
                return ran["value"], True  # correctly denied

        import os
        os.environ.pop("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", None)
        os.environ["OPA_REQUIRED"] = "true"

        bypasses = []
        for i in range(100):
            capability = "".join(rng.choices("abcdefghij.", k=rng.randint(5, 20)))
            resource = "".join(rng.choices("klmnopqrst-0123456789", k=rng.randint(3, 15)))
            ran, denied = asyncio.run(_run_one(capability, resource))
            if ran or not denied:
                bypasses.append((capability, resource, ran, denied))

        assert bypasses == [], f"found {len(bypasses)} governance bypasses among 100 random capability/resource pairs: {bypasses[:5]}"
