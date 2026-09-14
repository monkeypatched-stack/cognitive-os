"""A real, runnable enforcement point for approval validation.

This is an EXPLICIT, CALLER-CONTROLLED gate, not an automatically-enforced
repository-wide one — there is no request-interception hook inside Claude
Code (or anywhere else in this repository) for a validator to gate
without something choosing to invoke it. What this script does provide:
a real caller with a real, checkable exit code, usable by a human before
starting implementation, or wired into a pre-commit hook or CI job THAT
SOMEONE HAS ADDED — this repository does not currently have one. Until
something invokes `python -m governance.cli check ...`, nothing blocks
anything; that remains a discipline, not a mechanism. It closes the
narrower, genuinely fixable gap: until this file existed, nothing in the
repository called validate_approval() at all.

Usage:
    python -m governance.cli check \\
        --approval-id APR-... \\
        --handoff path/to/handoff.json \\
        [--repo-root .] [--require-git-provenance] \\
        [--files a.py b.py] [--behaviors "..."] [--security-boundaries "..."]

Exit codes (stable, do not change the meaning of an existing code):
    0 = GovernanceDecision.executable == True (approval VALID and, where a
        governance event was required, durably recorded)
    1 = executable == False because the approval itself is invalid/
        expired/out-of-scope/etc.; the blocking governance events were
        durably recorded (audit_durable == True despite the block)
    2 = usage/argument error, an invalid handoff file, OR a required
        governance audit event could NOT be durably recorded on EITHER
        path (audit_durable == False) — a harder stop than exit 1, since
        part of what failed is the governance layer's own observability,
        not only the approval. This can happen even for an otherwise-VALID
        approval — see decide() in governance/decision.py and Section 1's
        core invariant: no durable audit evidence -> no governed execution.

Audit-failure precedence (governance/decision.py has the full model):
    an invalid approval is ALWAYS reported as the primary reason, even
    when audit persistence ALSO fails — audit failure is additive
    diagnostic information, never a replacement for, or override of, the
    underlying approval decision. Audit failure can only make an
    otherwise-permitted result more restrictive; it can never convert a
    denial into an allow.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from governance.approval_artifact import ApprovalArtifactError, DiscoveryHandoff
from governance.audit import (
    DEFAULT_EVENT_LOG,
    GovernanceAuditError,
    record_governance_event,
)
from governance.decision import GovernanceDecision, decide
from governance.revision import compute_repository_revision
from governance.store import ApprovalRecordStore
from governance.validator import ApprovalValidationResult, validate_approval


def _approval_status_label(result: ApprovalValidationResult) -> str:
    """One-word summary distinguishing the shape of a non-authorized
    result — VALID/EXPIRED/INVALID/BLOCKED — rather than only a flat
    PASS/FAIL per field. Priority: structural problems (INVALID) are
    reported ahead of a time-window problem (EXPIRED), which is reported
    ahead of every other kind of mismatch (BLOCKED)."""
    if result.authorized:
        return "VALID"
    if not result.schema_valid or not result.integrity_valid:
        return "INVALID"
    if not result.within_validity_window:
        return "EXPIRED"
    return "BLOCKED"


def _print_authorization_check(
    *,
    approval_id: str,
    handoff: DiscoveryHandoff,
    current_revision: str,
    now: datetime,
    result: ApprovalValidationResult,
    decision: GovernanceDecision,
) -> None:
    def pass_fail(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print("IMPLEMENTATION_AUTHORIZATION_CHECK")
    print()
    print(f"handoff_id: {handoff.handoff_id}")
    print(f"approval_id: {approval_id}")
    print()
    # repository_revision is a binding over the effective HEAD commit AND
    # relevant dirty working-tree content (governance.revision) — never
    # describe this as merely a Git SHA in output or documentation.
    print(f"approved_revision: {handoff.repository_revision}")
    print(f"current_revision: {current_revision}")
    print(f"current_time: {now.isoformat()}")
    print()
    print(f"schema_validation: {pass_fail(result.schema_valid)}")
    print(f"decision_validation: {pass_fail(result.decision_approved)}")
    print(f"status_validation: {pass_fail(result.status_approved)}")
    print(f"handoff_validation: {pass_fail(result.handoff_matches)}")
    print(f"revision_validation: {pass_fail(result.revision_matches)}")
    print(f"expiration_validation: {pass_fail(result.within_validity_window)}")
    print(f"scope_validation: {pass_fail(result.scope_covers_request)}")
    # identity_validation reflects the operator-asserted-approver
    # blocklist heuristic ONLY — it is never authentication. See
    # governance.approval_artifact.AUTHENTICATED_APPROVER_AVAILABLE.
    print(f"identity_validation: {pass_fail(result.identity_plausible)}")
    print(f"artifact_integrity: {pass_fail(result.integrity_valid)}")
    if result.git_provenance_required:
        print(f"git_provenance_validation: {pass_fail(result.git_provenance_satisfied)}")
    print()
    if result.reasons:
        print("reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")
        print()
    print(f"approval_status: {_approval_status_label(result)}")
    print()
    # The GovernanceDecision fields below are the FINAL word — audit
    # durability can turn an otherwise-valid approval non-executable, so
    # `implementation_authorized` reflects decision.executable, never
    # result.authorized directly (Section 1/4: audit failure blocks a
    # valid approval too, it does not merely explain a block that already
    # existed for another reason).
    print(f"approval_valid: {'YES' if decision.approval_valid else 'NO'}")
    print(f"audit_durable: {'YES' if decision.audit_durable else 'NO'}")
    if decision.failure_reasons:
        print("failure_codes:")
        for code in decision.failure_reasons:
            print(f"  - {code}")
    print(f"implementation_authorized: {'YES' if decision.executable else 'NO'}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m governance.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check",
        help="Validate an approval and print IMPLEMENTATION_AUTHORIZATION_CHECK",
    )
    check.add_argument("--approval-id", required=True)
    check.add_argument(
        "--handoff",
        required=True,
        type=Path,
        help="Path to a DiscoveryHandoff JSON file",
    )
    check.add_argument(
        "--approvals-dir",
        type=Path,
        default=None,
        help="Override the approval store directory",
    )
    check.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="Override the governance event log path (default: <approvals-dir>/audit.jsonl, "
        "or the package default if --approvals-dir is also unset)",
    )
    check.add_argument("--repo-root", type=Path, default=Path("."))
    check.add_argument("--require-git-provenance", action="store_true")
    check.add_argument("--files", nargs="*", default=[])
    check.add_argument("--behaviors", nargs="*", default=[])
    check.add_argument("--security-boundaries", nargs="*", default=[])

    return parser.parse_args(argv)


def _governance_event_details(
    *,
    approval_id: str,
    handoff: DiscoveryHandoff,
    current_revision: str,
    result: ApprovalValidationResult,
) -> dict:
    checks = {
        "schema_valid": result.schema_valid,
        "decision_approved": result.decision_approved,
        "status_approved": result.status_approved,
        "handoff_matches": result.handoff_matches,
        "revision_matches": result.revision_matches,
        "within_validity_window": result.within_validity_window,
        "scope_covers_request": result.scope_covers_request,
        "identity_plausible": result.identity_plausible,
        "integrity_valid": result.integrity_valid,
    }
    if result.git_provenance_required:
        checks["git_provenance_satisfied"] = result.git_provenance_satisfied
    return {
        "approval_id": approval_id,
        "handoff_id": handoff.handoff_id,
        "repository_revision": current_revision,
        "checks": checks,
        "reasons": list(result.reasons),
    }


def _emit_blocking_governance_events(
    *,
    approval_id: str,
    handoff: DiscoveryHandoff,
    current_revision: str,
    result: ApprovalValidationResult,
    audit_log_path: Path,
) -> None:
    """Durably record that this CLI run blocked implementation.

    Two distinct events, per the governance event vocabulary
    (governance/audit.py::EVENT_TYPES) — not a new one: the validation
    itself failing (approval_validation_failed) and the CLI's own
    decision to block on that result (implementation_blocked_by_approval).
    They co-occur every time in THIS caller, but represent different
    facts, so both are recorded. Metadata is limited to identifiers and
    check statuses — no secrets, tokens, or credentials ever pass through
    ApprovalArtifact/ApprovalValidationResult to begin with.

    Raises GovernanceAuditError on persistence failure — callers must not
    swallow this (see main()): a blocking decision that could not be
    durably audited is a harder failure than an ordinary block.
    """
    details = _governance_event_details(
        approval_id=approval_id,
        handoff=handoff,
        current_revision=current_revision,
        result=result,
    )
    record_governance_event("approval_validation_failed", details=dict(details), path=audit_log_path)
    record_governance_event("implementation_blocked_by_approval", details=dict(details), path=audit_log_path)


def _emit_authorized_governance_event(
    *,
    approval_id: str,
    handoff: DiscoveryHandoff,
    current_revision: str,
    result: ApprovalValidationResult,
    audit_log_path: Path,
) -> None:
    """Durably record that this CLI run found the approval valid.

    This is what makes Section 1's core invariant ("no durable audit
    evidence -> no governed implementation") REAL for the success path,
    not merely vacuously true: `approval_authorized` is the one governance
    event type that exists specifically so a valid approval also has
    something required to durably record before `executable` may be True
    — see governance/README.md "Audit failure precedence" for why this
    one event type was added despite the general "don't invent event
    types" rule from the prior closure report.

    Raises GovernanceAuditError on persistence failure — the caller (see
    main()) must treat this exactly like a blocking-path audit failure:
    fail closed, never report success.
    """
    details = _governance_event_details(
        approval_id=approval_id,
        handoff=handoff,
        current_revision=current_revision,
        result=result,
    )
    record_governance_event("approval_authorized", details=details, path=audit_log_path)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.command != "check":
        return 2

    try:
        handoff_data = args.handoff.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: could not read handoff file {args.handoff}: {exc}", file=sys.stderr)
        return 2

    import json

    try:
        handoff = DiscoveryHandoff.from_dict(json.loads(handoff_data))
    except (json.JSONDecodeError, ApprovalArtifactError) as exc:
        print(f"error: invalid handoff file {args.handoff}: {exc}", file=sys.stderr)
        return 2

    store = ApprovalRecordStore(args.approvals_dir) if args.approvals_dir else ApprovalRecordStore()
    if args.audit_log is not None:
        audit_log_path = args.audit_log
    elif args.approvals_dir is not None:
        audit_log_path = args.approvals_dir / "audit.jsonl"
    else:
        audit_log_path = DEFAULT_EVENT_LOG
    current_revision = compute_repository_revision(args.repo_root).as_string()
    now = datetime.now(timezone.utc)

    result = validate_approval(
        args.approval_id,
        store=store,
        handoff=handoff,
        current_revision=current_revision,
        now=now,
        requested_files=tuple(args.files),
        requested_behaviors=tuple(args.behaviors),
        requested_security_boundaries=tuple(args.security_boundaries),
        require_git_provenance=args.require_git_provenance,
        repo_root=args.repo_root,
    )

    # Validation runs and completes BEFORE any audit attempt (Section 3):
    # implementation is never attempted first and then explained by an
    # audit failure after the fact. `result` alone already fully answers
    # "is this approval valid" — the audit step below can only ever make
    # the FINAL decision more restrictive than `result.authorized`, never
    # less (Section 6/7 monotonicity), and it never runs at all until
    # validation has already produced a definite answer.
    audit_error: str | None = None
    if result.authorized:
        try:
            _emit_authorized_governance_event(
                approval_id=args.approval_id,
                handoff=handoff,
                current_revision=current_revision,
                result=result,
                audit_log_path=audit_log_path,
            )
            audit_durable = True
        except GovernanceAuditError as exc:
            audit_durable = False
            audit_error = str(exc)
    else:
        try:
            _emit_blocking_governance_events(
                approval_id=args.approval_id,
                handoff=handoff,
                current_revision=current_revision,
                result=result,
                audit_log_path=audit_log_path,
            )
            audit_durable = True
        except GovernanceAuditError as exc:
            # Fail closed, but the approval's own invalidity remains the
            # PRIMARY reason (Section 5) — decide() below preserves both
            # facts rather than collapsing them into "audit failure only".
            audit_durable = False
            audit_error = str(exc)

    decision = decide(result, audit_durable=audit_durable)

    _print_authorization_check(
        approval_id=args.approval_id,
        handoff=handoff,
        current_revision=current_revision,
        now=now,
        result=result,
        decision=decision,
    )

    if decision.executable:
        return 0
    if audit_error is not None:
        # A harder stop than an ordinary block: part of what failed is
        # the governance layer's own observability, not only the
        # approval — never reported identically to a cleanly-audited
        # block, and never allowed to look like exit 0 (success).
        print(
            f"error: governance audit could not be durably recorded: {audit_error}",
            file=sys.stderr,
        )
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
