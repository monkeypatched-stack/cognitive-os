"""Tests for governance.decision — the deterministic audit-failure
precedence model (Section 20 of the spec this implements).

These are unit tests against `decide()` directly using synthetic
ApprovalValidationResult instances (dataclasses.replace off a known-good
baseline) — fast, and exercise every combination in the failure matrix
without needing git/filesystem machinery. End-to-end CLI-level coverage
(the one row that requires a real audit call — "valid + audit fails") is
in test_cli.py.
"""

from __future__ import annotations

import dataclasses

import pytest

from governance.decision import FailureCode, GovernanceDecision, decide
from governance.validator import ApprovalValidationResult


def _valid_result(**overrides) -> ApprovalValidationResult:
    base = dict(
        schema_valid=True,
        decision_approved=True,
        status_approved=True,
        handoff_matches=True,
        revision_matches=True,
        within_validity_window=True,
        scope_covers_request=True,
        identity_plausible=True,
        integrity_valid=True,
    )
    base.update(overrides)
    return ApprovalValidationResult(**base)


class TestGovernanceDecisionStructuralInvariant:
    def test_cannot_construct_executable_true_with_failure_reasons(self):
        with pytest.raises(ValueError):
            GovernanceDecision(
                approval_valid=True,
                audit_durable=True,
                authorized=True,
                executable=True,
                failure_reasons=("X",),
            )

    def test_cannot_construct_executable_true_when_a_gate_is_false(self):
        with pytest.raises(ValueError):
            GovernanceDecision(
                approval_valid=True,
                audit_durable=False,
                authorized=True,
                executable=True,
            )

    def test_cannot_construct_executable_false_without_any_reason(self):
        with pytest.raises(ValueError):
            GovernanceDecision(
                approval_valid=False,
                audit_durable=True,
                authorized=False,
                executable=False,
            )

    def test_valid_construction_succeeds(self):
        d = GovernanceDecision(approval_valid=True, audit_durable=True, authorized=True, executable=True)
        assert d.executable is True


class TestDecideFailureMatrix:
    """Section 17's failure matrix, at the decide() level."""

    def test_valid_approval_audit_succeeds_is_executable(self):
        d = decide(_valid_result(), audit_durable=True)
        assert d.executable is True
        assert d.failure_reasons == ()

    def test_valid_approval_audit_fails_is_blocked(self):
        d = decide(_valid_result(), audit_durable=False)
        assert d.executable is False
        assert d.approval_valid is True  # the approval itself was fine
        assert d.audit_durable is False
        assert FailureCode.AUDIT_PERSISTENCE_FAILED.value in d.failure_reasons

    def test_invalid_approval_audit_succeeds_is_blocked(self):
        d = decide(_valid_result(scope_covers_request=False), audit_durable=True)
        assert d.executable is False
        assert d.approval_valid is False
        assert FailureCode.SCOPE_MISMATCH.value in d.failure_reasons
        assert FailureCode.AUDIT_PERSISTENCE_FAILED.value not in d.failure_reasons

    def test_invalid_approval_audit_fails_is_blocked_and_both_preserved(self):
        """Section 5: the approval remains the PRIMARY failure even when
        audit also fails — never collapsed into 'audit failure only'."""
        d = decide(_valid_result(scope_covers_request=False), audit_durable=False)
        assert d.executable is False
        assert d.approval_valid is False
        assert FailureCode.SCOPE_MISMATCH.value in d.failure_reasons
        assert FailureCode.AUDIT_PERSISTENCE_FAILED.value in d.failure_reasons

    def test_expired_approval_audit_succeeds_is_blocked(self):
        d = decide(_valid_result(within_validity_window=False), audit_durable=True)
        assert d.executable is False
        assert FailureCode.APPROVAL_EXPIRED.value in d.failure_reasons

    def test_expired_approval_audit_fails_is_blocked(self):
        d = decide(_valid_result(within_validity_window=False), audit_durable=False)
        assert d.executable is False
        assert FailureCode.APPROVAL_EXPIRED.value in d.failure_reasons
        assert FailureCode.AUDIT_PERSISTENCE_FAILED.value in d.failure_reasons

    def test_wrong_scope_audit_succeeds_is_blocked(self):
        d = decide(_valid_result(scope_covers_request=False), audit_durable=True)
        assert d.executable is False

    def test_wrong_scope_audit_fails_is_blocked(self):
        d = decide(_valid_result(scope_covers_request=False), audit_durable=False)
        assert d.executable is False

    def test_wrong_revision_audit_succeeds_is_blocked(self):
        d = decide(_valid_result(revision_matches=False), audit_durable=True)
        assert d.executable is False
        assert FailureCode.REVISION_MISMATCH.value in d.failure_reasons

    def test_wrong_revision_audit_fails_is_blocked(self):
        d = decide(_valid_result(revision_matches=False), audit_durable=False)
        assert d.executable is False
        assert FailureCode.REVISION_MISMATCH.value in d.failure_reasons
        assert FailureCode.AUDIT_PERSISTENCE_FAILED.value in d.failure_reasons


class TestNeverConvertsDenyToAllow:
    def test_audit_failure_alone_never_produces_executable_true(self):
        """Never: DENY + audit failure -> ALLOW (Section 6)."""
        denied = _valid_result(decision_approved=False)
        d_ok_audit = decide(denied, audit_durable=True)
        d_failed_audit = decide(denied, audit_durable=False)
        assert d_ok_audit.executable is False
        assert d_failed_audit.executable is False


class TestDeterministicOrdering:
    def test_multiple_failures_are_all_preserved_not_collapsed(self):
        result = _valid_result(revision_matches=False, scope_covers_request=False, identity_plausible=False)
        d = decide(result, audit_durable=False)
        assert set(d.failure_reasons) == {
            FailureCode.REVISION_MISMATCH.value,
            FailureCode.SCOPE_MISMATCH.value,
            FailureCode.IDENTITY_IMPLAUSIBLE.value,
            FailureCode.AUDIT_PERSISTENCE_FAILED.value,
        }

    def test_ordering_is_deterministic_across_repeated_calls(self):
        result = _valid_result(revision_matches=False, scope_covers_request=False, identity_plausible=False)
        orders = {decide(result, audit_durable=False).failure_reasons for _ in range(5)}
        assert len(orders) == 1  # always the same tuple, never reshuffled

    def test_structural_failures_precede_scope_and_audit_in_order(self):
        result = _valid_result(schema_valid=False, scope_covers_request=False)
        d = decide(result, audit_durable=False)
        assert d.failure_reasons.index(FailureCode.SCHEMA_INVALID.value) < d.failure_reasons.index(
            FailureCode.SCOPE_MISMATCH.value,
        )
        assert d.failure_reasons.index(FailureCode.SCOPE_MISMATCH.value) < d.failure_reasons.index(
            FailureCode.AUDIT_PERSISTENCE_FAILED.value,
        )


class TestMonotonicity:
    """Section 21: introducing a required governance failure into an
    otherwise-executable decision can only ever flip executable to False,
    never leave it True or somehow re-enable it."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("within_validity_window", False),  # approval expired
            ("scope_covers_request", False),  # scope mismatch
            ("revision_matches", False),  # revision mismatch
            ("decision_approved", False),  # authorization denied
            ("handoff_matches", False),
            ("identity_plausible", False),
        ],
    )
    def test_introducing_a_single_failure_flips_executable_to_false(self, field, value):
        before = decide(_valid_result(), audit_durable=True)
        assert before.executable is True

        after = decide(_valid_result(**{field: value}), audit_durable=True)
        assert after.executable is False

    def test_introducing_audit_unavailability_flips_executable_to_false(self):
        before = decide(_valid_result(), audit_durable=True)
        assert before.executable is True

        after = decide(_valid_result(), audit_durable=False)
        assert after.executable is False

    def test_no_combination_of_starting_false_ever_becomes_true_by_adding_more_failures(
        self,
    ):
        """Adding failures to an already-false decision must never surface
        executable=True — monotonicity is one-directional."""
        already_bad = _valid_result(revision_matches=False)
        worse = dataclasses.replace(already_bad, scope_covers_request=False)
        d1 = decide(already_bad, audit_durable=True)
        d2 = decide(worse, audit_durable=False)
        assert d1.executable is False
        assert d2.executable is False
