"""Tests for governance.revision — dirty-tree-aware repository revision
binding, fixing the "exact HEAD SHA can't distinguish two different dirty
trees at the same commit" gap identified during discovery.

Every test operates on its own throwaway git repo under tmp_path — never
the real monkeypatched repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance.revision import (
    RepositoryRevision,
    compute_repository_revision,
    revision_diff,
)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


class TestRepositoryRevision:
    def test_clean_tree_has_empty_dirty_hash(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")

        rev = compute_repository_revision(tmp_path)
        assert rev.head_sha
        assert rev.dirty_tree_hash == ""
        assert rev.as_string() == rev.head_sha

    def test_uncommitted_modification_changes_dirty_hash(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")

        clean = compute_repository_revision(tmp_path)
        (tmp_path / "a.txt").write_text("hello, modified")
        dirty = compute_repository_revision(tmp_path)

        assert dirty.head_sha == clean.head_sha  # same commit
        assert dirty.dirty_tree_hash != clean.dirty_tree_hash
        assert dirty.as_string() != clean.as_string()  # combined string differs even at same HEAD

    def test_untracked_new_file_changes_dirty_hash(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")

        before = compute_repository_revision(tmp_path)
        (tmp_path / "new_untracked.py").write_text("print('new')")
        after = compute_repository_revision(tmp_path)

        assert after.head_sha == before.head_sha
        assert after.dirty_tree_hash != before.dirty_tree_hash

    def test_two_different_dirty_trees_at_same_head_are_distinguishable(self, tmp_path):
        """The exact gap this module fixes: two different working-tree
        states sharing one HEAD SHA must not look identical."""
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")

        (tmp_path / "scratch.txt").write_text("state A")
        state_a = compute_repository_revision(tmp_path)
        (tmp_path / "scratch.txt").write_text("state B — materially different content")
        state_b = compute_repository_revision(tmp_path)

        assert state_a.head_sha == state_b.head_sha
        assert state_a.as_string() != state_b.as_string()

    def test_commit_touching_only_excluded_paths_does_not_change_revision(self, tmp_path):
        """The exact chicken-and-egg case this fix addresses: committing
        an approval record under governance/approvals/ must not itself
        invalidate the revision that same approval is bound to."""
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")
        before = compute_repository_revision(tmp_path)

        approvals_dir = tmp_path / "governance" / "approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "APR-1.json").write_text('{"approval_id": "APR-1"}')
        _commit_all(tmp_path, "record approval APR-1")

        after = compute_repository_revision(tmp_path)
        assert after.as_string() == before.as_string()

    def test_commit_touching_excluded_and_non_excluded_paths_does_change_revision(self, tmp_path):
        """A commit that ALSO changes real code must still count — the
        exclusion is only for commits that are PURELY governance bookkeeping."""
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")
        before = compute_repository_revision(tmp_path)

        approvals_dir = tmp_path / "governance" / "approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "APR-1.json").write_text('{"approval_id": "APR-1"}')
        (tmp_path / "a.txt").write_text("hello, also changed")
        _commit_all(tmp_path, "record approval APR-1 AND change real code")

        after = compute_repository_revision(tmp_path)
        assert after.as_string() != before.as_string()

    def test_parse_round_trips_as_string(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")
        (tmp_path / "b.txt").write_text("dirty")

        rev = compute_repository_revision(tmp_path)
        parsed = RepositoryRevision.parse(rev.as_string())
        assert parsed == rev


class TestRevisionDiff:
    def test_no_diff_when_identical(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")
        rev = compute_repository_revision(tmp_path).as_string()
        assert revision_diff(rev, rev) == []

    def test_reports_head_change(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")
        rev1 = compute_repository_revision(tmp_path).as_string()

        (tmp_path / "a.txt").write_text("hello v2")
        _commit_all(tmp_path, "second commit")
        rev2 = compute_repository_revision(tmp_path).as_string()

        diffs = revision_diff(rev1, rev2)
        assert any("HEAD changed" in d for d in diffs)

    def test_reports_dirty_tree_change_at_same_head(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "a.txt").write_text("hello")
        _commit_all(tmp_path, "initial")

        (tmp_path / "scratch.txt").write_text("v1")
        rev1 = compute_repository_revision(tmp_path).as_string()
        (tmp_path / "scratch.txt").write_text("v2")
        rev2 = compute_repository_revision(tmp_path).as_string()

        diffs = revision_diff(rev1, rev2)
        assert any("working-tree content changed" in d for d in diffs)
        assert not any("HEAD changed" in d for d in diffs)
