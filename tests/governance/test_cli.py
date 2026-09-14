"""End-to-end tests for governance.cli — the real, runnable enforcement
entrypoint. Exercises the full validate_approval() path through main(),
in an isolated throwaway git repo + approval store (never the real repo).

The git repo lives at tmp_path/repo; the handoff/approval-store artifacts
live directly under tmp_path (a SIBLING, not inside the repo) — this
mirrors the realistic case where governance metadata about a repo is not
itself part of the repo's own working tree, and avoids the repo's revision
hash changing every time a governance artifact is written next to it.
(This package's OWN approvals/ directory, which does live inside this
same repository, is handled separately by DEFAULT_EXCLUDE_PREFIXES in
governance.revision — see test_revision.py.)
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from governance.approval_artifact import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalStatus,
    DiscoveryHandoff,
    create_artifact,
)
from governance.cli import main
from governance.revision import compute_repository_revision
from governance.store import ApprovalRecordStore


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def _write_handoff(path: Path, *, repository_revision: str, files: tuple[str, ...]) -> DiscoveryHandoff:
    handoff = DiscoveryHandoff(
        handoff_id="DH-cli-test-001",
        repository_revision=repository_revision,
        scope=ApprovalScope(task="test task", files=files, behaviors=("do the thing",)),
    )
    path.write_text(json.dumps(handoff.to_dict()))
    return handoff


@pytest.fixture
def repo(tmp_path):
    repo_dir = tmp_path / "repo"
    _init_repo(repo_dir)
    return repo_dir


@pytest.fixture
def approvals_dir(tmp_path):
    return tmp_path / "approvals"


class TestCliCheck:
    def test_valid_approval_authorized_exit_zero(self, tmp_path, repo, approvals_dir, capsys):
        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-cli-001",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        code = main(
            [
                "check",
                "--approval-id",
                "APR-cli-001",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/foo.py",
                "--behaviors",
                "do the thing",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "implementation_authorized: YES" in out

    def test_wrong_revision_blocks_exit_one(self, tmp_path, repo, approvals_dir, capsys):
        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-cli-002",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        # Mutate the working tree AFTER the approval was created, changing
        # the current revision string away from what was approved.
        (repo / "a.txt").write_text("changed after approval")

        code = main(
            [
                "check",
                "--approval-id",
                "APR-cli-002",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "implementation_authorized: NO" in out
        assert "revision_validation: FAIL" in out

    def test_out_of_scope_file_blocks_exit_one(self, tmp_path, repo, approvals_dir, capsys):
        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-cli-003",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        code = main(
            [
                "check",
                "--approval-id",
                "APR-cli-003",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/somewhere/else.py",
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "scope_validation: FAIL" in out

    def test_missing_approval_blocks_exit_one(self, tmp_path, repo, approvals_dir, capsys):
        revision = compute_repository_revision(repo).as_string()
        _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )

        code = main(
            [
                "check",
                "--approval-id",
                "APR-does-not-exist",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "implementation_authorized: NO" in out

    def test_require_git_provenance_blocks_uncommitted_approval(self, tmp_path, repo, approvals_dir, capsys):
        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-cli-004",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        # Deliberately NOT committing the approval file anywhere.

        code = main(
            [
                "check",
                "--approval-id",
                "APR-cli-004",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/foo.py",
                "--behaviors",
                "do the thing",
                "--require-git-provenance",
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "git_provenance_validation: FAIL" in out

    def test_require_git_provenance_allows_committed_approval(self, tmp_path, repo, capsys):
        """The approval store lives at governance/approvals/ INSIDE this
        repo, matching DEFAULT_EXCLUDE_PREFIXES in governance.revision —
        that's what breaks the chicken-and-egg loop where persisting (then
        committing) the approval would otherwise change the very revision
        the immutable artifact is already bound to. This is the realistic
        arrangement (this package's own approvals/ dir lives in-repo)."""
        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        approvals_dir = repo / "governance" / "approvals"
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-cli-005",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        # The approval file, under governance/approvals/, does NOT change
        # compute_repository_revision()'s result even before it's
        # committed — confirm that, then commit it anyway (a real deploy
        # would still want it in version control for durability/history,
        # just not counted as part of "the reviewed code").
        assert compute_repository_revision(repo).as_string() == revision
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "record approval APR-cli-005"],
            cwd=repo,
            check=True,
        )
        assert compute_repository_revision(repo).as_string() == revision

        code = main(
            [
                "check",
                "--approval-id",
                "APR-cli-005",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/foo.py",
                "--behaviors",
                "do the thing",
                "--require-git-provenance",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "git_provenance_validation: PASS" in out
        assert "implementation_authorized: YES" in out

    def test_invalid_handoff_file_exits_two(self, tmp_path, repo):
        bad_handoff = tmp_path / "bad.json"
        bad_handoff.write_text("not json at all")
        code = main(
            [
                "check",
                "--approval-id",
                "APR-x",
                "--handoff",
                str(bad_handoff),
                "--repo-root",
                str(repo),
            ]
        )
        assert code == 2


class TestGovernanceAuditWiring:
    """Gap 5: the CLI must actually emit governance.audit events on a
    blocking result — not merely have record_governance_event() defined
    and unused."""

    def test_blocked_result_emits_both_governance_events(self, tmp_path, repo, approvals_dir, capsys):
        from governance.audit import read_governance_events

        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-audit-001",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        audit_log = tmp_path / "audit.jsonl"

        code = main(
            [
                "check",
                "--approval-id",
                "APR-audit-001",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--audit-log",
                str(audit_log),
                "--files",
                "src/somewhere/else.py",  # deliberately out of scope -> blocked
            ]
        )
        assert code == 1

        events = read_governance_events(audit_log)
        event_types = [e["event_type"] for e in events]
        assert "approval_validation_failed" in event_types
        assert "implementation_blocked_by_approval" in event_types
        for event in events:
            assert event["details"]["approval_id"] == "APR-audit-001"
            assert event["details"]["handoff_id"] == "DH-cli-test-001"
            # No secrets ever pass through this metadata.
            assert "token" not in json.dumps(event).lower()
            assert "credential" not in json.dumps(event).lower()

    def test_authorized_result_emits_approval_authorized_event(self, tmp_path, repo, approvals_dir):
        """Superseded expectation, deliberately: an EARLIER turn's design
        emitted nothing on the success path (no event type fit). THIS
        turn's audit-failure-precedence work makes "no durable audit
        evidence -> no governed execution" real for the success path too
        (Section 1/4), which requires something to actually attempt and
        durably record there — approval_authorized (governance/audit.py)
        is that one, deliberately-added event type. See
        governance/README.md "Audit failure precedence" for why adding it
        was necessary despite the general "don't invent event types" rule."""
        from governance.audit import read_governance_events

        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-audit-002",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        audit_log = tmp_path / "audit.jsonl"

        code = main(
            [
                "check",
                "--approval-id",
                "APR-audit-002",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--audit-log",
                str(audit_log),
                "--files",
                "src/foo.py",
                "--behaviors",
                "do the thing",
            ]
        )
        assert code == 0
        events = read_governance_events(audit_log)
        assert [e["event_type"] for e in events] == ["approval_authorized"]
        assert events[0]["details"]["approval_id"] == "APR-audit-002"
        assert events[0]["event_id"]  # unique id present (Section 13)
        # No other event type was invented — exactly the one required event.
        assert len(events) == 1

    def test_audit_persistence_failure_does_not_produce_false_success(
        self,
        tmp_path,
        repo,
        approvals_dir,
        capsys,
        monkeypatch,
    ):
        """Gap 5 fail-closed requirement: if the governance audit for a
        blocking result cannot be durably recorded, the CLI must not
        report success — it gets its OWN exit code (2), distinct from
        both a clean authorization (0) and a cleanly-audited block (1)."""
        import governance.cli as cli_module
        from governance.audit import GovernanceAuditError

        def _boom(*a, **k):
            raise GovernanceAuditError("simulated durable-write failure")

        monkeypatch.setattr(cli_module, "record_governance_event", _boom)

        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-audit-003",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        code = main(
            [
                "check",
                "--approval-id",
                "APR-audit-003",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/somewhere/else.py",  # out of scope -> blocked, then audit fails
            ]
        )
        out = capsys.readouterr().out
        assert code == 2
        assert code != 0
        # The printed report already correctly said NO before the audit
        # call was even attempted — that must never flip to YES.
        assert "implementation_authorized: NO" in out
        assert "implementation_authorized: YES" not in out
        # Section 5: the approval's own invalidity remains the PRIMARY
        # reason even though audit persistence also failed — both facts
        # survive in the printed report, never collapsed into one.
        assert "approval_valid: NO" in out
        assert "SCOPE_MISMATCH" in out
        assert "AUDIT_PERSISTENCE_FAILED" in out

    def test_valid_approval_with_audit_failure_is_blocked_not_authorized(
        self,
        tmp_path,
        repo,
        approvals_dir,
        capsys,
        monkeypatch,
    ):
        """The critical row from the failure matrix (Section 17/18): an
        otherwise-VALID approval whose success-path audit write fails must
        still be blocked (exit 2), never exit 0 — audit durability is a
        genuine precondition for execution, not merely a log emitted after
        the fact (Section 1's core invariant, exercised end-to-end)."""
        import governance.cli as cli_module
        from governance.audit import GovernanceAuditError

        def _boom(*a, **k):
            raise GovernanceAuditError("simulated durable-write failure")

        monkeypatch.setattr(cli_module, "record_governance_event", _boom)

        revision = compute_repository_revision(repo).as_string()
        handoff = _write_handoff(
            tmp_path / "handoff.json",
            repository_revision=revision,
            files=("src/foo.py",),
        )
        store = ApprovalRecordStore(approvals_dir)
        artifact = create_artifact(
            handoff=handoff,
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-audit-004",
            lifetime=timedelta(hours=24),
        )
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)

        code = main(
            [
                "check",
                "--approval-id",
                "APR-audit-004",
                "--handoff",
                str(tmp_path / "handoff.json"),
                "--approvals-dir",
                str(approvals_dir),
                "--repo-root",
                str(repo),
                "--files",
                "src/foo.py",
                "--behaviors",
                "do the thing",  # in scope -> would otherwise be VALID
            ]
        )
        out = capsys.readouterr().out

        assert code == 2
        assert code != 0
        # The approval itself was fine — only audit durability failed.
        assert "approval_valid: YES" in out
        assert "audit_durable: NO" in out
        assert "AUDIT_PERSISTENCE_FAILED" in out
        assert "implementation_authorized: NO" in out
        assert "implementation_authorized: YES" not in out
