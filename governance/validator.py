"""The one canonical approval validator.

Every caller that needs to know "does this approval currently authorize
this work" must call `validate_approval()` — no alternative/weaker check
should be implemented elsewhere (Section 21 of the approval-artifact spec
this implements).

validate_approval() reports facts; it does not itself decide what to do
with an invalid result (that's the caller's IMPLEMENTATION_AUTHORIZATION_CHECK
step) — see governance/README.md for the full workflow this fits into.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from governance.approval_artifact import (
    ApprovalDecision,
    ApprovalStatus,
    DiscoveryHandoff,
)
from governance.git_provenance import (
    GitProvenance,
    GitProvenanceError,
    approval_git_provenance,
)
from governance.store import (
    ApprovalIntegrityError,
    ApprovalPersistenceError,
    ApprovalRecord,
    ApprovalRecordStore,
)


@dataclass(frozen=True)
class ApprovalValidationResult:
    schema_valid: bool
    decision_approved: bool
    status_approved: bool
    handoff_matches: bool
    revision_matches: bool
    within_validity_window: bool
    scope_covers_request: bool
    identity_plausible: bool
    integrity_valid: bool
    reasons: tuple[str, ...] = ()
    # Informational, not required for `authorized` unless the caller passed
    # require_git_provenance=True to validate_approval() — see that
    # function's docstring and governance/git_provenance.py's own honesty
    # note (this is provenance evidence, not authentication).
    git_provenance: GitProvenance | None = None
    git_provenance_required: bool = False

    @property
    def git_provenance_satisfied(self) -> bool:
        if not self.git_provenance_required:
            return True
        return bool(self.git_provenance and self.git_provenance.committed)

    @property
    def authorized(self) -> bool:
        return all(
            (
                self.schema_valid,
                self.decision_approved,
                self.status_approved,
                self.handoff_matches,
                self.revision_matches,
                self.within_validity_window,
                self.scope_covers_request,
                self.identity_plausible,
                self.integrity_valid,
                self.git_provenance_satisfied,
            )
        )


@dataclass(frozen=True)
class ScopeChangeRequest:
    """Not an approval. Produced when requested work exceeds an approval's
    scope, so implementation can stop and a new discovery/approval cycle
    can be run — see Section 24 of the approval-artifact spec."""

    request_id: str
    original_approval_id: str
    original_handoff_id: str
    requested_change: str
    reason: str
    affected_files: tuple[str, ...] = ()
    affected_behaviors: tuple[str, ...] = ()
    security_impact: str = ""
    persistence_impact: str = ""
    compatibility_impact: str = ""
    requires_new_approval: bool = True


def _fail(reasons: list[str], *, git_provenance_required: bool = False, **overrides: bool) -> ApprovalValidationResult:
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
    return ApprovalValidationResult(
        reasons=tuple(reasons),
        git_provenance_required=git_provenance_required,
        **base,
    )


def validate_approval(
    approval_id: str,
    *,
    store: ApprovalRecordStore,
    handoff: DiscoveryHandoff,
    current_revision: str,
    now: datetime,
    requested_files: tuple[str, ...] = (),
    requested_behaviors: tuple[str, ...] = (),
    requested_security_boundaries: tuple[str, ...] = (),
    require_git_provenance: bool = False,
    repo_root: Path | str | None = None,
) -> ApprovalValidationResult:
    """Look up `approval_id` in `store` and check every required condition.

    A missing artifact, a persistence/integrity failure, or any single
    failed check produces authorized=False — this function never returns
    a permissive result by default (Section 3/9/20: missing/invalid/expired
    approval must fail closed).

    `require_git_provenance=True` additionally requires the approval
    record to have been committed to git (see governance/git_provenance.py)
    before it counts as authorized — a real but non-authenticating
    strengthening of "who approved this," opt-in because most approvals in
    an active review may legitimately still be uncommitted. `repo_root`
    is required when this is True.
    """
    try:
        record: ApprovalRecord = store.get(approval_id)
    except (ApprovalPersistenceError, ApprovalIntegrityError) as exc:
        return _fail(
            [f"approval could not be durably verified: {exc}"],
            schema_valid=False,
            integrity_valid=False,
            git_provenance_required=require_git_provenance,
        )

    reasons: list[str] = []
    artifact = record.artifact

    decision_approved = artifact.decision is ApprovalDecision.APPROVED
    if not decision_approved:
        reasons.append(f"decision is {artifact.decision.value}, not approved")

    status_approved = record.status is ApprovalStatus.APPROVED
    if not status_approved:
        reasons.append(f"status is {record.status.value}, not approved (decision was {artifact.decision.value})")

    handoff_matches = artifact.handoff_id == handoff.handoff_id
    if not handoff_matches:
        reasons.append(f"approval is for handoff {artifact.handoff_id!r}, not {handoff.handoff_id!r}")

    revision_matches = artifact.repository_revision == current_revision
    if not revision_matches:
        reasons.append(
            f"approval reviewed revision {artifact.repository_revision!r}, "
            f"current revision is {current_revision!r} — requires revalidation",
        )

    within_window = artifact.approved_at <= now < artifact.expires_at
    if not within_window:
        if now < artifact.approved_at:
            reasons.append(f"now ({now.isoformat()}) is before approved_at ({artifact.approved_at.isoformat()})")
        else:
            reasons.append(f"approval expired at {artifact.expires_at.isoformat()} (now {now.isoformat()})")

    covered, scope_reasons = handoff.scope.covers(
        files=requested_files,
        behaviors=requested_behaviors,
        security_boundaries=requested_security_boundaries,
    )
    reasons.extend(scope_reasons)

    identity_plausible = not artifact.approver_identity_is_disallowed
    if not identity_plausible:
        reasons.append(
            f"approved_by {artifact.approved_by!r} is a disallowed placeholder identity "
            "(this is a name-blocklist heuristic, not authentication — see README.md)",
        )

    integrity_valid = record.stored_content_hash == artifact.content_hash() or not record.stored_content_hash
    # store.get() already raises ApprovalIntegrityError on mismatch (caught
    # above), so reaching here with a record means integrity already held —
    # this recheck is defense-in-depth against an in-memory record handed
    # in by a caller that bypassed the store.
    if not integrity_valid:
        reasons.append("artifact content_hash does not match recomputed hash")

    git_provenance: GitProvenance | None = None
    if require_git_provenance:
        if repo_root is None:
            reasons.append("require_git_provenance=True but no repo_root was given")
        else:
            try:
                git_provenance = approval_git_provenance(store.path_for(approval_id), repo_root=repo_root)
            except GitProvenanceError as exc:
                reasons.append(f"git provenance check failed: {exc}")
            else:
                if not git_provenance.committed:
                    reasons.append(
                        f"require_git_provenance=True but approval {approval_id!r} has not been committed to git",
                    )

    return ApprovalValidationResult(
        schema_valid=True,
        decision_approved=decision_approved,
        status_approved=status_approved,
        handoff_matches=handoff_matches,
        revision_matches=revision_matches,
        within_validity_window=within_window,
        scope_covers_request=covered,
        identity_plausible=identity_plausible,
        integrity_valid=integrity_valid,
        reasons=tuple(reasons),
        git_provenance=git_provenance,
        git_provenance_required=require_git_provenance,
    )
