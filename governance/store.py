"""Durable storage for ApprovalArtifacts and their current status.

Persists to plain JSON files on disk (one per approval, under a directory
this store owns) rather than an in-memory dictionary — Section 20 of the
approval-artifact spec explicitly requires the registry not be an
in-memory cache, and requires write/read failure to fail closed rather
than silently succeed.

This is a separate, self-contained store — it does not use CognitiveOS's
Mongo/Redis/AuditLog infrastructure. See governance/README.md for why:
this package governs a development workflow, not the CognitiveOS product,
and must not be wired into or confused with the product's own durable
security/audit stores.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from governance.approval_artifact import (
    STATUS_TRANSITIONS,
    ApprovalArtifact,
    ApprovalArtifactError,
    ApprovalStatus,
    InvalidApprovalTransition,
)

DEFAULT_APPROVAL_DIR = Path(__file__).resolve().parent / "approvals"


class ApprovalPersistenceError(Exception):
    """Raised when an approval record cannot be durably written or read.

    Callers MUST treat this as fail-closed: an approval that cannot be
    durably verified does not authorize anything (Section 20).
    """


class ApprovalIntegrityError(ApprovalPersistenceError):
    """The stored content_hash does not match the artifact's recomputed
    hash — the record was mutated (or corrupted) after creation."""


@dataclass
class ApprovalRecord:
    """An ApprovalArtifact plus its current, transitionable status and
    an append-only history of status changes. The artifact itself is
    immutable (frozen dataclass); only `status`/`status_history` here
    change, and only through `ApprovalRecordStore.transition_status()`.
    """

    artifact: ApprovalArtifact
    status: ApprovalStatus
    status_history: list[dict[str, Any]] = field(default_factory=list)
    stored_content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "status": self.status.value,
            "status_history": self.status_history,
            "content_hash": self.artifact.content_hash(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalRecord":
        artifact = ApprovalArtifact.from_dict(data["artifact"])
        stored_hash = data.get("content_hash", "")
        recomputed = artifact.content_hash()
        if stored_hash != recomputed:
            raise ApprovalIntegrityError(
                f"approval {artifact.approval_id}: stored content_hash does not match "
                "recomputed hash — record was mutated or corrupted after creation",
            )
        return cls(
            artifact=artifact,
            status=ApprovalStatus(data["status"]),
            status_history=list(data.get("status_history") or []),
            stored_content_hash=stored_hash,
        )


class ApprovalRecordStore:
    """File-backed durable store. One JSON file per approval_id.

    Every write is atomic (write to a temp file in the same directory,
    then os.replace) so a crash mid-write cannot leave a half-written,
    silently-corrupt record that a later read would misinterpret.
    """

    def __init__(self, directory: Path | str = DEFAULT_APPROVAL_DIR) -> None:
        self._dir = Path(directory)
        self._lock = threading.Lock()

    def _path(self, approval_id: str) -> Path:
        if not approval_id or any(c in approval_id for c in ("/", "\\", "..")):
            raise ApprovalPersistenceError(f"invalid approval_id for storage: {approval_id!r}")
        return self._dir / f"{approval_id}.json"

    def path_for(self, approval_id: str) -> Path:
        """Public accessor for the record's on-disk path — used to check
        git provenance (governance.git_provenance) without exposing the
        rest of the store's internals."""
        return self._path(approval_id)

    def create(self, artifact: ApprovalArtifact, *, initial_status: ApprovalStatus) -> ApprovalRecord:
        """Persist a brand-new record. Fails closed (raises) if a record
        with this approval_id already exists — approval_id must be unique
        and immutable (Section 3/12); this is never an upsert."""
        if initial_status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ApprovalArtifactError(
                f"initial_status must be APPROVED or REJECTED (the decision), got {initial_status}",
            )
        path = self._path(artifact.approval_id)
        with self._lock:
            if path.exists():
                raise ApprovalPersistenceError(f"approval {artifact.approval_id} already exists — cannot overwrite")
            record = ApprovalRecord(
                artifact=artifact,
                status=initial_status,
                status_history=[{"status": initial_status.value, "reason": "created"}],
            )
            self._write(path, record)
            return record

    def get(self, approval_id: str) -> ApprovalRecord:
        """Fail closed: any read/parse/integrity failure raises rather
        than returning None or a permissive default."""
        path = self._path(approval_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ApprovalPersistenceError(f"no approval record for {approval_id!r}") from exc
        except OSError as exc:
            raise ApprovalPersistenceError(f"failed to read approval {approval_id!r}: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApprovalPersistenceError(f"approval {approval_id!r} is not valid JSON: {exc}") from exc
        return ApprovalRecord.from_dict(data)

    def transition_status(
        self,
        approval_id: str,
        target: ApprovalStatus,
        *,
        reason: str = "",
    ) -> ApprovalRecord:
        """The one canonical way to change a record's status after
        creation. Validates the transition against the same table the
        artifact's state model defines; rejects (raises) anything not in
        that table — no caller may implement an alternative check."""
        with self._lock:
            record = self.get(approval_id)
            allowed = STATUS_TRANSITIONS.get(record.status, frozenset())
            if target not in allowed:
                raise InvalidApprovalTransition(approval_id, record.status, target)
            record.status = target
            record.status_history = [
                *record.status_history,
                {"status": target.value, "reason": reason},
            ]
            self._write(self._path(approval_id), record)
            return record

    def list_ids(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def _write(self, path: Path, record: ApprovalRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.to_dict(), indent=2, sort_keys=True)
        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(self._dir), prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise ApprovalPersistenceError(
                f"failed to durably write approval {record.artifact.approval_id!r}: {exc}"
            ) from exc
