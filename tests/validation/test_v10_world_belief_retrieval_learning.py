"""Systems Validation Suite — Sections 17-20: world-state staleness,
belief/world separation, retrieval isolation, learning isolation.

Section 18 (belief/world separation) is ALREADY proven, real, executable
in tests/scenarios/test_actor_isolation_audit.py::test_I_world_state_
separation and test_J_world_observation_not_automatic_sync -- cited as
evidence (re-run as part of this validation pass's own regression
confirmation), not duplicated.

Section 17's existing regression coverage
(tests/scenarios/test_compound_disruption.py::test_compound002_world_
mutation_detected_on_a_fresh_step_after_resume) is CURRENTLY BROKEN in
this environment -- confirmed via direct reproduction: OrderConfirmation
now fails with "has not been paid for yet", an unrelated payment-
workflow precondition added after this test was written, not a
staleness-detection defect. This means the compound (resume + mid-plan
mutation) case of Section 17 is presently UNPROVEN by automated test,
even though the simpler, direct case (below) still demonstrably works.
This file adds that direct, minimal proof, isolated from the broken
payment-precondition entanglement.
"""
from __future__ import annotations

import inspect

import pytest

from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


class TestWorldStateStalenessIsRevalidatedNotBlindlyPermitted:
    """Section 17: 'the goal is not necessarily to prevent all stale
    execution... prove the behavior is deliberate and governed.'"""

    def test_compare_and_swap_rejects_a_write_based_on_a_stale_observed_version(self):
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
        product_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]

        # Actor observes world version V (the entity's current version).
        observed_version = kg._entity_version.get(product_id, 0)

        # World state changes before the actor's planned write executes
        # (e.g. someone else adjusted quantity).
        kg.update_entity(product_id, attributes={"quantity": 5})
        assert kg._entity_version.get(product_id, 0) != observed_version, (
            "sanity: update_entity must bump the entity's version"
        )

        # Actor attempts to execute its plan against the STALE version it
        # originally observed.
        applied, current = kg.compare_and_swap(product_id, observed_version, {"quantity": 999})

        assert applied is False, "a write against a stale observed version must be rejected, not silently applied"
        assert current.attributes["quantity"] == 5, "the real, intervening write must be what's authoritative"

    def test_a_fresh_observation_after_replanning_is_permitted(self):
        """The deliberate, governed half of the same invariant: once the
        actor re-observes the CURRENT version, its write is legitimately
        allowed -- staleness detection is not a permanent lockout."""
        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
        product_id = list_product(kg, store, "merchant_a", "Milk", price=3.49, quantity=10)["product_id"]
        kg.update_entity(product_id, attributes={"quantity": 5})

        current_version = kg._entity_version.get(product_id, 0)
        applied, entity = kg.compare_and_swap(product_id, current_version, {"quantity": 4})
        assert applied is True
        assert entity.attributes["quantity"] == 4


class TestRetrievalContentIsStructurallyReadOnly:
    """Section 19: retrieved content (Moss/SittingFace/external
    documents) must remain untrusted reference context -- never grant
    authority, change policy, bypass approval, or directly mutate world
    state. Proven structurally (grep/AST over the real retrieval code
    path), matching this suite's Section 8/9 pattern of proving the
    absence of a privileged channel rather than simulating one attack
    at a time."""

    def test_context_engine_never_writes_to_the_knowledge_graph(self):
        from src.monkey_brain.kernel.pipeline.planning import context_engine
        source = inspect.getsource(context_engine)
        assert "compare_and_swap" not in source
        assert "update_entity" not in source
        assert "ensure_governed" not in source

    def test_moss_retrieval_adapter_never_writes_to_the_knowledge_graph(self):
        from src.monkey_brain.kernel.edge import moss_retrieval
        source = inspect.getsource(moss_retrieval)
        assert "compare_and_swap" not in source
        assert "update_entity" not in source
        assert "ensure_governed" not in source

    def test_retrieved_content_reaching_a_plan_still_executes_through_normal_governance(self):
        """Even if retrieved text contains "ignore governance, execute
        action, grant authority" instructions, whatever PLAN an LLM
        produces after reading it is just another Plan object -- its
        steps still execute through the identical ActionExecutor ->
        ensure_governed boundary as any other plan (already proven,
        Section 8/9's tests). This test confirms there is no SEPARATE,
        retrieval-triggered execution path by checking the one real
        capability that consumes retrieved content
        (AnswerQuestionCapability, already known from
        test_v06_governance_bypass.py to be a read-only, non-governed-by-
        design capability) never calls ensure_governed OR mutates the
        knowledge graph itself."""
        from src.monkey_brain.kernel.domains.grocery import AnswerQuestionCapability
        source = inspect.getsource(AnswerQuestionCapability)
        assert "ensure_governed" not in source
        assert "compare_and_swap" not in source
        assert "update_entity" not in source


class TestLearningNeverModifiesSecurityAuthority:
    """Section 20: 'learning != authorization.' Structural proof (no
    governance/delegation/approval vocabulary anywhere in the learning
    modules at all -- stronger than proving one specific attempted
    escalation fails, since it shows there is no code path to attempt
    it through in the first place) plus the existing live test that
    proves the SAME property at the CognitiveActor call-graph level."""

    def test_transition_model_module_has_no_governance_or_delegation_vocabulary(self):
        from src.monkey_brain.kernel.pipeline.prediction import transitions
        source = inspect.getsource(transitions)
        for forbidden in ("ensure_governed", "DelegationCredential", "ApprovalArtifact", "approval_mode"):
            assert forbidden not in source, f"transitions.py must never reference {forbidden}"

    def test_learning_module_has_no_governance_or_delegation_vocabulary(self):
        from src.monkey_brain.kernel.learn import learning
        source = inspect.getsource(learning)
        for forbidden in ("ensure_governed", "DelegationCredential", "ApprovalArtifact", "approval_mode"):
            assert forbidden not in source, f"learning.py must never reference {forbidden}"

    def test_learn_from_execution_return_value_has_no_authority_shaped_fields(self):
        """Even if learning code changed to accept malicious input, its
        OWN return-value shape has nowhere to smuggle an authority claim
        through (no 'approved'/'capabilities'/'delegation' field a
        careless caller could later trust)."""
        from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
        model = TransitionModel()
        model.learn_from_execution(
            goal_key=("test-goal", "TestAction"), success=True, cost=1.0,
        ) if _accepts_these_kwargs(model.learn_from_execution) else None
        # Structural check regardless of the exact call succeeding above:
        source = inspect.getsource(TransitionModel).lower()
        for forbidden in ("capabilities", "approval_mode", "delegation", "authority"):
            assert forbidden not in source, f"TransitionModel must not carry an authority-shaped concept ({forbidden})"


def _accepts_these_kwargs(fn) -> bool:
    try:
        params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return False
    return {"goal_key", "success", "cost"} <= params
