"""GovernanceDecision — a single, deterministic precedence model combining
"is this approval valid" with "was the required governance audit durably
recorded" into one answer about whether governed execution is permitted.

SECURITY/GOVERNANCE FAILURE PRECEDENCE (Section 20 of the spec this
implements):

    1. Any invalid approval prevents execution.
    2. Any failed authorization prevents execution.
    3. Any required audit persistence failure prevents execution.
    4. No failure may be converted into ALLOW by another failure.
    5. Multiple failures are preserved diagnostically.
    6. Audit failure is fail-closed.
    7. Audit failure never overrides or masks the underlying authorization
       decision — it is recorded ALONGSIDE it, never in place of it.
    8. No governed implementation begins unless every required
       precondition succeeds.

`authorized` here means "CognitiveOS's normal runtime security controls
(authentication, MFA, OPA, idempotency, audit-before-effect) also allow
it" — this package has no separate implementation of that; it is exactly
`approval_valid` in this tool today because this package touches nothing
beyond approval governance (see governance/README.md "What this is NOT").
The field exists so the model matches the conceptually distinct
`approval_valid AND authorized AND audit_durable` shape even though, in
THIS codebase, `authorized == approval_valid`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from governance.validator import ApprovalValidationResult


class FailureCode(str, Enum):
    """Stable, machine-checkable failure codes — distinct from
    ApprovalValidationResult.reasons' free-text human strings. Ordering
    below (_PRECEDENCE_ORDER) is diagnostic display order, not an
    execution-permission order: `executable` is always the conjunction of
    every gate, never influenced by which failure is listed first.
    """

    SCHEMA_INVALID = "SCHEMA_INVALID"
    INTEGRITY_INVALID = "INTEGRITY_INVALID"
    DECISION_NOT_APPROVED = "DECISION_NOT_APPROVED"
    STATUS_NOT_APPROVED = "STATUS_NOT_APPROVED"
    HANDOFF_MISMATCH = "HANDOFF_MISMATCH"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    IDENTITY_IMPLAUSIBLE = "IDENTITY_IMPLAUSIBLE"
    GIT_PROVENANCE_MISSING = "GIT_PROVENANCE_MISSING"
    AUDIT_PERSISTENCE_FAILED = "AUDIT_PERSISTENCE_FAILED"


# P0 (structural/schema problems) before P1 (ordinary approval-validity
# mismatches) before P2 (audit persistence) — matches the spec's P0/P1/P2
# framing while keeping "executable" itself precedence-independent (see
# GovernanceDecision docstring).
_PRECEDENCE_ORDER: tuple[FailureCode, ...] = (
    FailureCode.SCHEMA_INVALID,
    FailureCode.INTEGRITY_INVALID,
    FailureCode.DECISION_NOT_APPROVED,
    FailureCode.STATUS_NOT_APPROVED,
    FailureCode.HANDOFF_MISMATCH,
    FailureCode.REVISION_MISMATCH,
    FailureCode.APPROVAL_EXPIRED,
    FailureCode.SCOPE_MISMATCH,
    FailureCode.IDENTITY_IMPLAUSIBLE,
    FailureCode.GIT_PROVENANCE_MISSING,
    FailureCode.AUDIT_PERSISTENCE_FAILED,
)


def _sort_reasons(codes: set[FailureCode]) -> tuple[str, ...]:
    def key(code: FailureCode) -> int:
        try:
            return _PRECEDENCE_ORDER.index(code)
        except ValueError:
            return len(_PRECEDENCE_ORDER)

    return tuple(c.value for c in sorted(codes, key=key))


@dataclass(frozen=True)
class GovernanceDecision:
    """The final, single answer: is governed execution permitted.

    `executable` is not an independent field a caller sets — it is
    STRUCTURALLY derived and checked in __post_init__, so it is
    impossible to construct a GovernanceDecision where `executable=True`
    while any gating condition is False (Section 8's core invariant,
    enforced by the type itself, not by caller discipline).
    """

    approval_valid: bool
    audit_durable: bool
    authorized: bool
    executable: bool
    failure_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected = self.approval_valid and self.audit_durable and self.authorized
        if self.executable != expected:
            raise ValueError(
                f"GovernanceDecision.executable ({self.executable}) must equal "
                f"approval_valid AND audit_durable AND authorized ({expected}) — "
                "this is a structural invariant, not a convention a caller can override",
            )
        if self.executable and self.failure_reasons:
            raise ValueError("a GovernanceDecision cannot be executable=True and carry failure_reasons")
        if not self.executable and not self.failure_reasons:
            raise ValueError("a non-executable GovernanceDecision must record at least one failure reason")


def decide(
    result: ApprovalValidationResult,
    *,
    audit_durable: bool,
) -> GovernanceDecision:
    """Combine a validate_approval() result with a separately-determined
    audit-durability outcome into one GovernanceDecision.

    Monotonicity (Section 7/21): flipping `audit_durable` from True to
    False can only ever move `executable` from True to False, never the
    reverse — enforced structurally by GovernanceDecision itself, not
    merely by this function's logic. An invalid approval's own reasons
    are ALWAYS included even when audit_durable is also False (Section 5:
    "the approval remains the primary authorization failure" — audit
    failure is additive, never a replacement for the original reason).
    """
    codes: set[FailureCode] = set()
    if not result.schema_valid:
        codes.add(FailureCode.SCHEMA_INVALID)
    if not result.integrity_valid:
        codes.add(FailureCode.INTEGRITY_INVALID)
    if not result.decision_approved:
        codes.add(FailureCode.DECISION_NOT_APPROVED)
    if not result.status_approved:
        codes.add(FailureCode.STATUS_NOT_APPROVED)
    if not result.handoff_matches:
        codes.add(FailureCode.HANDOFF_MISMATCH)
    if not result.revision_matches:
        codes.add(FailureCode.REVISION_MISMATCH)
    if not result.within_validity_window:
        codes.add(FailureCode.APPROVAL_EXPIRED)
    if not result.scope_covers_request:
        codes.add(FailureCode.SCOPE_MISMATCH)
    if not result.identity_plausible:
        codes.add(FailureCode.IDENTITY_IMPLAUSIBLE)
    if result.git_provenance_required and not result.git_provenance_satisfied:
        codes.add(FailureCode.GIT_PROVENANCE_MISSING)

    approval_valid = bool(result.authorized)
    if not audit_durable:
        codes.add(FailureCode.AUDIT_PERSISTENCE_FAILED)

    executable = approval_valid and audit_durable
    return GovernanceDecision(
        approval_valid=approval_valid,
        audit_durable=audit_durable,
        # No separate authorization layer exists in this package beyond
        # approval validity — see module docstring.
        authorized=approval_valid,
        executable=executable,
        failure_reasons=() if executable else _sort_reasons(codes),
    )
