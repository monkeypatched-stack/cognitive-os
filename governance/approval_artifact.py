"""ApprovalArtifact — a first-class record of a human governance decision
authorizing a specific discovery handoff, repository revision, and bounded
implementation scope, for a limited period.

Read governance/README.md before using this for anything you intend to
rely on. In short: this is a structured record and a consistent validator
for a DEVELOPMENT-PROCESS decision (did a human approve this plan, is that
approval still current). It is not authentication, not a signature, and
not a runtime security control — see README.md "What this is not."

Design mirrors this repository's existing kernel state-machine style
(src/monkey_brain/kernel/execution_attempt.py, security_operation.py):
a frozen/immutable core record, a small explicit status-transition table,
and one canonical validator that every caller must use rather than
re-implementing the checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

ARTIFACT_VERSION = 1
DEFAULT_APPROVAL_LIFETIME = timedelta(hours=24)

AUTHENTICATED_APPROVER_AVAILABLE: bool = False
"""Programmatic capability flag for Gap 1 (approver authentication).

This environment has no trusted authentication mechanism — no login
system, JWT, session, or OPA-fronted identity service — that this package
can bind `approved_by` to. It therefore records an OPERATOR-ASSERTED
identifier, never an authenticated one, and this constant makes that a
checkable fact rather than only a documentation claim:

    approved_by            == operator-asserted identifier
    identity_plausible      == passed a name-blocklist heuristic
    identity_authenticated  == DOES NOT EXIST as a field, anywhere, and
                                must never be added while this flag is False

If a future environment provides a real trusted-identity boundary, this
flag (and the identity-check machinery below) is exactly what should
change — not by renaming `identity_plausible` to imply authentication
retroactively, but by adding a genuinely new, separately-named
`identity_authenticated` check once `AUTHENTICATED_APPROVER_AVAILABLE`
can honestly be `True`.
"""

AUTHENTICATED_APPROVER_UNAVAILABLE = "AUTHENTICATED_APPROVER_UNAVAILABLE"
"""Named limitation sentinel — the literal string a caller/report can
grep for or display, e.g. in a CLI footer, without needing to import and
inspect AUTHENTICATED_APPROVER_AVAILABLE's boolean value."""

# Placeholder/self-referential values that must never be accepted as a
# human approving identity. This is a weak heuristic (a name blocklist),
# not authentication — see README.md.
_DISALLOWED_APPROVER_IDENTITIES = frozenset(
    {
        "agent",
        "llm",
        "system",
        "anonymous",
        "unknown",
        "claude",
        "assistant",
        "ai",
        "bot",
        "",
        "n/a",
        "none",
        "null",
    }
)


class ApprovalDecision(str, Enum):
    """The human's explicit decision. Never inferred, never defaulted."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(str, Enum):
    """Current validity of an approval, distinct from the original decision.

    A REJECTED decision never becomes APPROVED status. An APPROVED
    decision can still have EXPIRED/REVOKED/SUPERSEDED status — status
    answers "is this currently authorized," decision answers "what did
    the human originally decide."
    """

    CREATED = "created"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


TERMINAL_STATUSES = frozenset(
    {
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.REVOKED,
        ApprovalStatus.SUPERSEDED,
        ApprovalStatus.INVALID,
    }
)

# CREATED -> APPROVED|REJECTED is set once, at construction, from the
# decision itself (see ApprovalRecord.create) — it is not a separate call.
STATUS_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.CREATED: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}),
    ApprovalStatus.APPROVED: frozenset(
        {
            ApprovalStatus.EXPIRED,
            ApprovalStatus.REVOKED,
            ApprovalStatus.SUPERSEDED,
        }
    ),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.REVOKED: frozenset(),
    ApprovalStatus.SUPERSEDED: frozenset(),
    ApprovalStatus.INVALID: frozenset(),
}


class InvalidApprovalTransition(Exception):
    def __init__(self, approval_id: str, source: ApprovalStatus, target: ApprovalStatus) -> None:
        self.approval_id = approval_id
        self.source = source
        self.target = target
        super().__init__(f"invalid approval-status transition {approval_id}: {source.value} -> {target.value}")


class ApprovalArtifactError(ValueError):
    """Raised when artifact construction/deserialization violates the schema."""


@dataclass(frozen=True)
class ApprovalScope:
    """Structured, semantic implementation scope — not just file paths
    (Section 7/8 of the approval-artifact spec this implements).

    `files`/`symbols` are the path/identifier scope. `behaviors` and
    `security_boundaries` are free-text semantic labels the discovery
    handoff names explicitly (e.g. "execution-attempt state canonicalization",
    "shared isinstance type-guard utility") — a change matching a file path
    but not named as an approved behavior is still out of scope.
    """

    task: str
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    behaviors: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    migrations: tuple[str, ...] = ()
    security_boundaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task or not self.task.strip():
            raise ApprovalArtifactError("scope.task is required")

    def covers(
        self,
        *,
        files: tuple[str, ...] = (),
        behaviors: tuple[str, ...] = (),
        security_boundaries: tuple[str, ...] = (),
    ) -> tuple[bool, list[str]]:
        """Whether every requested file/behavior/security-boundary is
        within this scope.

        Returns (covered, reasons) — reasons lists exactly what fell
        outside scope, for a ScopeChangeRequest if covered is False.
        `security_boundaries` is checked independently of `files`/
        `behaviors` — a matching file path never implies authorization
        for a security-boundary change living in that file (Section 8/12:
        e.g. an approved `execution_attempt.py` file-scope does not
        authorize an OPA or MFA behavior change also living there).
        """
        reasons: list[str] = []
        approved_files = set(self.files)
        approved_behaviors = set(self.behaviors)
        approved_boundaries = set(self.security_boundaries)
        for f in files:
            if f not in approved_files:
                reasons.append(f"file not in approved scope: {f}")
        for b in behaviors:
            if b not in approved_behaviors:
                reasons.append(f"behavior not in approved scope: {b}")
        for s in security_boundaries:
            if s not in approved_boundaries:
                reasons.append(f"security boundary not in approved scope: {s}")
        return (not reasons, reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "files": list(self.files),
            "symbols": list(self.symbols),
            "behaviors": list(self.behaviors),
            "tests": list(self.tests),
            "migrations": list(self.migrations),
            "security_boundaries": list(self.security_boundaries),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalScope":
        try:
            return cls(
                task=data["task"],
                files=tuple(data.get("files") or ()),
                symbols=tuple(data.get("symbols") or ()),
                behaviors=tuple(data.get("behaviors") or ()),
                tests=tuple(data.get("tests") or ()),
                migrations=tuple(data.get("migrations") or ()),
                security_boundaries=tuple(data.get("security_boundaries") or ()),
            )
        except KeyError as exc:
            raise ApprovalArtifactError(f"scope missing required field: {exc}") from exc


@dataclass(frozen=True)
class DiscoveryHandoff:
    """The minimal fields of a discovery handoff needed to validate an
    approval against it. Not the full handoff document — just its binding
    identity (handoff_id, repository_revision) and the scope being approved.
    """

    handoff_id: str
    repository_revision: str
    scope: ApprovalScope

    def __post_init__(self) -> None:
        if not self.handoff_id or not self.handoff_id.strip():
            raise ApprovalArtifactError("handoff_id is required")
        if not self.repository_revision or not self.repository_revision.strip():
            raise ApprovalArtifactError("repository_revision is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "repository_revision": self.repository_revision,
            "scope": self.scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryHandoff":
        try:
            return cls(
                handoff_id=data["handoff_id"],
                repository_revision=data["repository_revision"],
                scope=ApprovalScope.from_dict(data["scope"]),
            )
        except KeyError as exc:
            raise ApprovalArtifactError(f"handoff missing required field: {exc}") from exc


@dataclass(frozen=True)
class ApprovalArtifact:
    """The immutable core of an approval decision.

    Every field here is immutable for the lifetime of this Python object
    (a frozen dataclass — attempting to set any field after construction
    raises dataclasses.FrozenInstanceError). Scope expansion or expiration
    extension is never done by mutating an artifact; create a new one
    (optionally with `supersedes_approval_id` pointing at the old one —
    see ApprovalRecordStore.renew()).

    `status` deliberately does NOT live here — it is tracked by
    ApprovalRecord/ApprovalRecordStore, because status legitimately
    transitions over the artifact's lifetime (CREATED -> APPROVED ->
    EXPIRED/REVOKED/SUPERSEDED) while every field below must not.
    """

    artifact_version: int
    approval_id: str
    handoff_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    repository_revision: str
    scope: ApprovalScope
    decision: ApprovalDecision
    supersedes_approval_id: str | None = None

    def __post_init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("approval_id", self.approval_id),
                ("handoff_id", self.handoff_id),
                ("approved_by", self.approved_by),
                ("repository_revision", self.repository_revision),
            )
            if not value or not str(value).strip()
        ]
        if missing:
            raise ApprovalArtifactError(f"missing required field(s): {', '.join(missing)}")
        if not isinstance(self.decision, ApprovalDecision):
            raise ApprovalArtifactError(f"decision must be an ApprovalDecision, got {type(self.decision)!r}")
        if not isinstance(self.scope, ApprovalScope):
            raise ApprovalArtifactError(f"scope must be an ApprovalScope, got {type(self.scope)!r}")
        if self.approved_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ApprovalArtifactError("approved_at/expires_at must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ApprovalArtifactError("expires_at must be strictly after approved_at")
        if self.artifact_version != ARTIFACT_VERSION:
            raise ApprovalArtifactError(
                f"unsupported artifact_version {self.artifact_version} (expected {ARTIFACT_VERSION})",
            )

    @property
    def approver_identity_is_disallowed(self) -> bool:
        """Weak heuristic only — see README.md. Catches the obvious
        self-approval anti-pattern (an agent naming itself as approver),
        not a real identity check."""
        return self.approved_by.strip().lower() in _DISALLOWED_APPROVER_IDENTITIES

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_version": self.artifact_version,
            "approval_id": self.approval_id,
            "handoff_id": self.handoff_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "repository_revision": self.repository_revision,
            "scope": self.scope.to_dict(),
            "decision": self.decision.value,
            "supersedes_approval_id": self.supersedes_approval_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalArtifact":
        try:
            return cls(
                artifact_version=data["artifact_version"],
                approval_id=data["approval_id"],
                handoff_id=data["handoff_id"],
                approved_by=data["approved_by"],
                approved_at=datetime.fromisoformat(data["approved_at"]),
                expires_at=datetime.fromisoformat(data["expires_at"]),
                repository_revision=data["repository_revision"],
                scope=ApprovalScope.from_dict(data["scope"]),
                decision=ApprovalDecision(data["decision"]),
                supersedes_approval_id=data.get("supersedes_approval_id"),
            )
        except KeyError as exc:
            raise ApprovalArtifactError(f"artifact missing required field: {exc}") from exc
        except ValueError as exc:
            raise ApprovalArtifactError(f"artifact schema invalid: {exc}") from exc

    def content_hash(self) -> str:
        """SHA-256 over the canonical (sorted-key) JSON of every field,
        including scope and expiration — not just approval_id (Section 19).

        This is an INTEGRITY check (detects mutation-after-creation of the
        stored record), not a signature: it proves the stored bytes match
        what content_hash() recomputes from them, not who wrote them.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_artifact(
    *,
    handoff: DiscoveryHandoff,
    approved_by: str,
    decision: ApprovalDecision,
    approval_id: str,
    approved_at: datetime | None = None,
    lifetime: timedelta = DEFAULT_APPROVAL_LIFETIME,
    supersedes_approval_id: str | None = None,
) -> ApprovalArtifact:
    """Construct a new ApprovalArtifact bound to `handoff`.

    `approved_at` defaults to now (UTC) if not given by the caller — pass
    it explicitly in tests. `approval_id` is required (not generated here)
    so callers/tests control identity generation explicitly rather than
    this module reaching for time-based or random IDs.
    """
    created_at = approved_at or datetime.now(timezone.utc)
    return ApprovalArtifact(
        artifact_version=ARTIFACT_VERSION,
        approval_id=approval_id,
        handoff_id=handoff.handoff_id,
        approved_by=approved_by,
        approved_at=created_at,
        expires_at=created_at + lifetime,
        repository_revision=handoff.repository_revision,
        scope=handoff.scope,
        decision=decision,
        supersedes_approval_id=supersedes_approval_id,
    )
