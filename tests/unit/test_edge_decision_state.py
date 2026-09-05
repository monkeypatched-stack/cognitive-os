"""EdgeExecutionAssessment / EdgeDecisionState (kernel/edge/decision_state.py)
-- proves connectivity, policy freshness, world-state freshness, and
authority freshness are kept as four independent dimensions, and that
LocalGovernanceEvaluator produces the specific decision state each branch
of its logic actually corresponds to."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.monkey_brain.kernel.edge.decision_state import (
    EdgeDecisionState,
    EdgeExecutionAssessment,
    assess_edge_execution,
)
from src.monkey_brain.kernel.edge.freshness import Freshness
from src.monkey_brain.kernel.edge.local_governance import LocalGovernanceEvaluator
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.pipeline.offline_safety import ConnectivityStatus

PRINCIPAL = "actor-1"


class TestFourDimensionsAreIndependent:
    def test_connected_plus_stale_policy_is_a_valid_non_healthy_state(self):
        assessment = assess_edge_execution(
            connectivity=ConnectivityStatus.CONNECTED, policy_freshness=Freshness.STALE_MUST_REFRESH,
            world_state_freshness=Freshness.FRESH, authority_freshness=Freshness.FRESH,
        )
        assert assessment.connectivity == ConnectivityStatus.CONNECTED
        assert assessment.policy_freshness == Freshness.STALE_MUST_REFRESH
        assert assessment.fully_healthy is False

    def test_connected_stale_policy_fresh_world_state_is_not_reported_healthy(self):
        """The exact example from the spec: must NOT read as fully healthy."""
        assessment = assess_edge_execution(
            connectivity=ConnectivityStatus.CONNECTED, policy_freshness=Freshness.STALE_MUST_REFRESH,
            world_state_freshness=Freshness.FRESH, authority_freshness=Freshness.FRESH,
        )
        assert assessment.fully_healthy is False

    def test_all_dimensions_favorable_is_fully_healthy(self):
        assessment = assess_edge_execution(
            connectivity=ConnectivityStatus.CONNECTED, policy_freshness=Freshness.FRESH,
            world_state_freshness=Freshness.FRESH, authority_freshness=Freshness.FRESH,
        )
        assert assessment.fully_healthy is True

    def test_disconnected_with_otherwise_fresh_state_is_not_healthy(self):
        assessment = assess_edge_execution(
            connectivity=ConnectivityStatus.DISCONNECTED, policy_freshness=Freshness.FRESH,
            world_state_freshness=Freshness.FRESH, authority_freshness=Freshness.FRESH,
        )
        assert assessment.fully_healthy is False

    def test_to_dict_surfaces_all_four_dimensions_for_telemetry(self):
        assessment = assess_edge_execution(
            connectivity=ConnectivityStatus.DEGRADED, policy_freshness=Freshness.FRESH,
            world_state_freshness=Freshness.STALE_BUT_USABLE, authority_freshness=Freshness.UNKNOWN,
        )
        d = assessment.to_dict()
        assert d["connectivity"] == "degraded"
        assert d["policy_freshness"] == "fresh"
        assert d["world_state_freshness"] == "stale_but_usable"
        assert d["authority_freshness"] == "unknown"
        assert d["fully_healthy"] is False


class TestLocalGovernanceProducesSpecificDecisionStates:
    def _gov(self, tmp_path):
        store = EdgeLocalStore(str(tmp_path / "edge.db"))
        return LocalGovernanceEvaluator(EdgePolicyCache(store))

    def test_no_snapshot_is_escalate_policy(self, tmp_path):
        gov = self._gov(tmp_path)
        outcome = gov.evaluate(principal=PRINCIPAL, action="capability.x", resource="x", authenticated_principal=PRINCIPAL)
        assert outcome.escalate is True
        assert outcome.decision_state == EdgeDecisionState.ESCALATE_POLICY

    def test_deny_snapshot_is_local_deny(self, tmp_path):
        gov = self._gov(tmp_path)
        snap = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.x", resource="x",
            policy_decision={"allowed": False, "approval_mode": "DENY", "policy_rule": "r"},
        )
        gov._policy_cache.store_snapshot(snap)
        outcome = gov.evaluate(principal=PRINCIPAL, action="capability.x", resource="x", authenticated_principal=PRINCIPAL)
        assert outcome.decision_state == EdgeDecisionState.LOCAL_DENY

    def test_human_approval_required_snapshot_is_its_own_state(self, tmp_path):
        gov = self._gov(tmp_path)
        snap = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.x", resource="x",
            policy_decision={"allowed": False, "approval_mode": "HUMAN_APPROVAL_REQUIRED", "policy_rule": "r"},
        )
        gov._policy_cache.store_snapshot(snap)
        outcome = gov.evaluate(principal=PRINCIPAL, action="capability.x", resource="x", authenticated_principal=PRINCIPAL)
        assert outcome.escalate is True
        assert outcome.decision_state == EdgeDecisionState.LOCAL_HUMAN_APPROVAL_REQUIRED

    def test_valid_allow_snapshot_is_local_allow(self, tmp_path):
        gov = self._gov(tmp_path)
        snap = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.x", resource="x",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "r"},
        )
        gov._policy_cache.store_snapshot(snap)
        outcome = gov.evaluate(principal=PRINCIPAL, action="capability.x", resource="x", authenticated_principal=PRINCIPAL)
        assert outcome.allowed is True
        assert outcome.decision_state == EdgeDecisionState.LOCAL_ALLOW

    def test_invalid_delegation_is_local_deny_not_an_escalation(self, tmp_path):
        gov = self._gov(tmp_path)
        bad_chain = (MagicMock(parent_delegation_id="", delegation_id="d1"),)
        outcome = gov.evaluate(
            principal=PRINCIPAL, action="capability.x", resource="x", authenticated_principal="someone-else",
            delegation_chain=bad_chain,
        )
        assert outcome.escalate is False
        assert outcome.decision_state == EdgeDecisionState.LOCAL_DENY
