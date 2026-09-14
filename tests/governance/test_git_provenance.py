"""Tests for governance.git_provenance — a real, checkable "was this
approval actually committed to version control" signal. Explicitly NOT a
test that this constitutes authentication (it doesn't — see the module's
own docstring); these tests only verify the mechanism does what it claims.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance.git_provenance import GitProvenanceError, approval_git_provenance


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "approver@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Approving Human"], cwd=root, check=True)


class TestGitProvenance:
    def test_uncommitted_file_reports_not_committed(self, tmp_path):
        _init_repo(tmp_path)
        approval_file = tmp_path / "approvals" / "APR-1.json"
        approval_file.parent.mkdir()
        approval_file.write_text("{}")

        provenance = approval_git_provenance(approval_file, repo_root=tmp_path)
        assert provenance.committed is False
        assert provenance.commit_sha == ""

    def test_committed_file_reports_author_and_commit(self, tmp_path):
        _init_repo(tmp_path)
        approval_file = tmp_path / "approvals" / "APR-1.json"
        approval_file.parent.mkdir()
        approval_file.write_text('{"approval_id": "APR-1"}')
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "approve APR-1"], cwd=tmp_path, check=True)

        provenance = approval_git_provenance(approval_file, repo_root=tmp_path)
        assert provenance.committed is True
        assert len(provenance.commit_sha) == 40
        assert provenance.author_name == "Approving Human"
        assert provenance.author_email == "approver@example.com"
        assert provenance.committed_at  # non-empty ISO timestamp
        # No GPG signing configured in this throwaway repo.
        assert provenance.gpg_signed is False

    def test_nonexistent_path_outside_repo_raises(self, tmp_path):
        _init_repo(tmp_path)
        with pytest.raises(GitProvenanceError):
            approval_git_provenance(Path("/definitely/not/in/repo.json"), repo_root=tmp_path)
