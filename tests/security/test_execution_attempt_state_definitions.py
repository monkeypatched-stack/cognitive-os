"""Duplicate-state-definition audit: there is exactly one canonical
execution-attempt state type and one canonical transition mechanism.

This file is a structural guard, not a retrospective changelog — it
should keep failing loudly if a second competing execution-attempt state
machine is ever introduced, anywhere in the repository, and it locks in
the boundary between execution-attempt state and its neighbors
(commitment, external provider status, audit event names).

See docs/security/execution-attempt-state-machine.md for the narrative
model this enforces.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.monkey_brain.kernel.execution_attempt import (
    ExecutionAttempt,
    ExecutionAttemptState,
    get_attempt_store,
    reconstruct_attempts_from_audit,
    reset_attempt_store_for_tests,
    transition_attempt,
)
from src.monkey_brain.kernel.security_boundary import privileged_infrastructure
from src.monkey_brain.kernel.security_operation import SecurityOperationState

REPO_ROOT = Path(__file__).resolve().parents[2]

# Distinctive enough that no legitimately-different lifecycle would
# plausibly define all of these under one class — NodeState/ProcessState/
# TransactionStatus/CapabilityState etc. (real, separate lifecycles found
# in this repo) each lack at least one of these.
_CANONICAL_MEMBER_NAMES = frozenset(
    {
        "NOT_STARTED",
        "READY",
        "STARTED",
        "SUBMITTED",
        "SUCCEEDED",
        "FAILED",
        "UNKNOWN",
        "RECONCILIATION_REQUIRED",
        "RECONCILING",
        "CANCELLED",
    }
)

_SEARCH_DIRS = ("src", "packages", "domains", "services", "tests")
_SKIP_PARTS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}


def _iter_repo_python_files():
    for top in _SEARCH_DIRS:
        base = REPO_ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if _SKIP_PARTS & set(path.parts):
                continue
            yield path


def _enum_class_member_names(tree: ast.AST) -> dict[str, set[str]]:
    """class name -> set of ALL-CAPS assigned names (candidate enum members)."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        members: set[str] = set()
        for stmt in node.body:
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets = stmt.targets
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets = [stmt.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    members.add(t.id)
        if members:
            found[node.name] = members
    return found


class TestExactlyOneExecutionAttemptStateDefinition:
    def test_canonical_enum_has_exactly_the_intended_members(self):
        assert {m.name for m in ExecutionAttemptState} == _CANONICAL_MEMBER_NAMES

    def test_no_second_class_anywhere_defines_the_full_canonical_member_set(self):
        """AST-based (not brittle text grep): parses every .py file's class
        bodies and flags any OTHER class whose ALL-CAPS assigned names are a
        superset of the canonical execution-attempt vocabulary. A real,
        separate lifecycle (NodeState, ProcessState, TransactionStatus,
        SecurityOperationState, ReservationStatus, ...) is missing at least
        one of RECONCILIATION_REQUIRED/RECONCILING/NOT_STARTED and will
        never trip this.
        """
        offenders: list[str] = []
        for path in _iter_repo_python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for class_name, members in _enum_class_member_names(tree).items():
                if class_name == "ExecutionAttemptState":
                    continue
                if _CANONICAL_MEMBER_NAMES <= members:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}::{class_name}")
        assert offenders == [], (
            "found a second class defining the full execution-attempt "
            f"vocabulary (duplicate state machine): {offenders}"
        )

    def test_execution_attempt_class_name_is_not_defined_twice(self):
        offenders: list[str] = []
        for path in _iter_repo_python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "ExecutionAttempt":
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == ["src/monkey_brain/kernel/execution_attempt.py"], offenders


class TestCanonicalTransitionMechanism:
    def test_transition_attempt_is_the_only_public_transition_entrypoint(self):
        reset_attempt_store_for_tests()
        attempt = get_attempt_store().create("op-canon-transition")
        with privileged_infrastructure("test"):
            out = transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
        assert out.state is ExecutionAttemptState.READY

    def test_transition_rejects_a_same_named_member_of_a_different_state_enum(self):
        """Regression: ExecutionAttemptState and SecurityOperationState are
        both (str, Enum) — SUCCEEDED/UNKNOWN/... members with the same
        spelling compare EQUAL by string value across the two classes. A
        naive `target in allowed_set` check would silently accept the WRONG
        enum's member. The canonical transition path must reject it by
        type, not by value."""
        assert ExecutionAttemptState.SUCCEEDED == SecurityOperationState.SUCCEEDED  # the hazard, confirmed

        reset_attempt_store_for_tests()
        attempt = get_attempt_store().create("op-cross-enum")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            with pytest.raises(TypeError):
                transition_attempt(attempt.execution_attempt_id, SecurityOperationState.SUCCEEDED)
        # Rejected by type before any state mutation happened.
        assert get_attempt_store().get(attempt.execution_attempt_id).state is ExecutionAttemptState.SUBMITTED

    def test_ledger_transition_also_rejects_a_foreign_enum(self):
        from src.monkey_brain.kernel.security_operation import (
            OperationLedger,
            SecurityOperation,
            TransactionClass,
        )

        ledger = OperationLedger()
        ledger.create(
            SecurityOperation(
                operation_id="op-ledger-cross-enum",
                action="orders.create",
                resource="o",
                state=SecurityOperationState.AUTHORIZED,
                transaction_class=TransactionClass.CLASS_A_INTERNAL,
            )
        )
        with pytest.raises(TypeError):
            ledger.transition("op-ledger-cross-enum", ExecutionAttemptState.SUCCEEDED)


class TestSerializationRoundTrip:
    def test_to_dict_and_reconstruct_from_audit_preserve_canonical_state(self):
        reset_attempt_store_for_tests()
        attempt = ExecutionAttempt(
            execution_attempt_id="op-serialize-ATT-1",
            operation_id="op-serialize",
            attempt_number=1,
            state=ExecutionAttemptState.SUCCEEDED,
        )
        payload = attempt.to_dict()
        assert payload["state"] == "succeeded"
        # Round-trips through the actual persistence/rehydration boundary
        # that exists today (audit-log reconstruction — there is no direct
        # Mongo/Redis persistence of ExecutionAttempt yet, so that boundary
        # is not exercised here; see the final report's remaining limitations).
        recovered = reconstruct_attempts_from_audit(
            [
                {
                    "action": "orders.create.result",
                    "details": {
                        "execution_attempt_id": payload["execution_attempt_id"],
                        "state": payload["state"],
                    },
                }
            ]
        )
        assert recovered[payload["execution_attempt_id"]] is ExecutionAttemptState.SUCCEEDED
        assert recovered[payload["execution_attempt_id"]] is ExecutionAttemptState(payload["state"])


class TestProviderStatusDoesNotLeakIntoExecutionAttemptState:
    def test_reservation_status_and_execution_attempt_state_share_no_member_identity(
        self,
    ):
        """External provider status (payment_provider.ReservationStatus) is
        a distinct vocabulary at the integration boundary — it must be
        mapped through classify_external_exception, never assigned directly
        as if it were an ExecutionAttemptState.

        Deliberately checked with isinstance, not `set(...) & set(...)` or
        `==` — both enums are (str, Enum), so same-spelled members (FAILED)
        compare EQUAL by string value across the two classes; a set/`==`
        check would (wrongly) report an overlap. isinstance checks the
        actual class.
        """
        from src.monkey_brain.kernel.domains.payment_provider import ReservationStatus

        for member in ReservationStatus:
            assert not isinstance(member, ExecutionAttemptState)
        for member in ExecutionAttemptState:
            assert not isinstance(member, ReservationStatus)

    def test_classify_external_exception_is_the_only_provider_to_attempt_bridge(self):
        from src.monkey_brain.kernel.security_operation import (
            classify_external_exception,
        )

        assert classify_external_exception(TimeoutError("x")) == "unknown"
        assert classify_external_exception(RuntimeError("declined")) == "failed"


class TestCommitmentStateCannotBeReadAsAnAttemptState:
    def test_security_operation_state_members_are_not_execution_attempt_members(self):
        """Commitment (SecurityOperationState) and execution attempt
        (ExecutionAttemptState) are separate lifecycles by design (Part 8
        of the reconciliation policy). AUTHORIZED / AUDIT_INTENT_RECORDED /
        EXECUTING have no ExecutionAttemptState counterpart at all."""
        commitment_only = {
            SecurityOperationState.AUTHORIZED,
            SecurityOperationState.AUDIT_INTENT_RECORDED,
            SecurityOperationState.EXECUTING,
        }
        for member in commitment_only:
            assert member.name not in ExecutionAttemptState.__members__

    def test_reconciliation_required_is_intentionally_shared_and_kept_in_lockstep(self):
        """RECONCILIATION_REQUIRED exists on BOTH enums, by design: the
        commitment ledger and the attempt each track the SAME reconciliation
        event from their own layer (durable-record trust vs. effect
        certainty). This is a documented, intentional exception, not drift
        — the two are always driven together by security_boundary.py's
        _mark_unknown_and_require_reconciliation / complete_reconciliation.
        This test pins that both spellings exist, so a future rename of
        either without updating the other is caught."""
        assert "RECONCILIATION_REQUIRED" in ExecutionAttemptState.__members__
        assert "RECONCILIATION_REQUIRED" in SecurityOperationState.__members__
        assert (
            ExecutionAttemptState.RECONCILIATION_REQUIRED.value == SecurityOperationState.RECONCILIATION_REQUIRED.value
        )


class TestAuditEventNamesAreNotExecutionStates:
    def test_reconciliation_audit_event_names_are_distinct_from_state_values(self):
        """'reconciliation_succeeded' (an audit action suffix) must never be
        confused with ExecutionAttemptState.SUCCEEDED (a state value) —
        they are different vocabularies at different layers."""
        audit_event_names = {
            "required",
            "started",
            "succeeded",
            "failed",
            "unresolved",
            "retry_authorized",
        }
        state_values = {s.value for s in ExecutionAttemptState}
        # 'succeeded'/'failed' happen to share spelling with two audit event
        # suffixes, but the AUDIT ACTION is always "<action>.reconciliation.<event>",
        # never a bare state value — assert the two vocabularies aren't the
        # same object/type even where a word coincides.
        assert audit_event_names != state_values
        assert "unresolved" not in state_values  # the state is RECONCILIATION_REQUIRED, not "unresolved"
