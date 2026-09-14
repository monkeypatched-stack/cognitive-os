"""Repository-revision binding stronger than a bare HEAD SHA.

Fixes a gap identified during discovery: this repository's working tree
is routinely dirty (uncommitted new/modified files) while a discovery
handoff is being reviewed. Binding an approval to `git rev-parse HEAD`
alone cannot distinguish "reviewed while these N files were present" from
some other dirty-tree state at the same HEAD — two different working
trees can share one HEAD SHA.

`compute_repository_revision()` combines HEAD SHA with a stable hash of
the dirty/untracked file set's *content* (not just names), so an approval
captures what was actually on disk at discovery time, not just which
commit the tree was based on.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RevisionComputationError(Exception):
    """Raised when the repository revision cannot be determined — fail
    closed rather than falling back to a placeholder revision string."""


def _run_git(args: list[str], repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RevisionComputationError(f"git {' '.join(args)} failed: {exc}") from exc
    return result.stdout


@dataclass(frozen=True)
class RepositoryRevision:
    """head_sha: the commit HEAD points at (empty string if repo has no
    commits yet). dirty_tree_hash: sha256 over sorted (path, content-sha256)
    pairs for every file git reports as modified/added/untracked — empty
    string means the working tree exactly matches HEAD.
    """

    head_sha: str
    dirty_tree_hash: str

    def as_string(self) -> str:
        """The single string stored as ApprovalArtifact.repository_revision
        / DiscoveryHandoff.repository_revision."""
        return f"{self.head_sha}:{self.dirty_tree_hash}" if self.dirty_tree_hash else self.head_sha

    @classmethod
    def parse(cls, value: str) -> "RepositoryRevision":
        head_sha, _, dirty = value.partition(":")
        return cls(head_sha=head_sha, dirty_tree_hash=dirty)


DEFAULT_EXCLUDE_PREFIXES = ("governance/approvals/",)
"""Excluded from the dirty-tree hash by default: this package's own
approval-record store lives inside this same repository. Without this
exclusion, durably persisting (or committing) an approval record would
itself change the working tree it's meant to be bound to — a
chicken-and-egg problem, not a security feature. Governance artifacts are
metadata ABOUT the reviewed state, not part of the code under review.
"""


def _effective_head_sha(root: Path, head_sha: str, exclude_prefixes: tuple[str, ...]) -> str:
    """Walk HEAD back past any commit whose ENTIRE changeset touches only
    excluded paths (e.g. a "record approval" commit under
    governance/approvals/).

    Without this, committing an approval record (so it has real git
    provenance — see governance/git_provenance.py) would itself advance
    HEAD, which would then immediately invalidate the very approval that
    commit just recorded, on the next revision check — a chicken-and-egg
    problem, not a real change to the code under review. This makes
    "effective head" mean "the last commit that changed anything other
    than governance bookkeeping," which is what revision-binding actually
    cares about.
    """
    current = head_sha
    while current:
        try:
            parents = _run_git(["rev-list", "--parents", "-n", "1", current], root).split()
        except RevisionComputationError:
            return current
        if len(parents) < 2:
            return current  # root commit — nothing to walk back past
        parent = parents[1]
        try:
            changed = [
                line for line in _run_git(["diff", "--name-only", parent, current], root).splitlines() if line.strip()
            ]
        except RevisionComputationError:
            return current
        if changed and all(any(c.startswith(p) for p in exclude_prefixes) for c in changed):
            current = parent
            continue
        return current
    return current


def compute_repository_revision(
    repo_root: Path | str,
    *,
    exclude_prefixes: tuple[str, ...] = DEFAULT_EXCLUDE_PREFIXES,
) -> RepositoryRevision:
    root = Path(repo_root)
    try:
        raw_head_sha = _run_git(["rev-parse", "HEAD"], root).strip()
        head_sha = _effective_head_sha(root, raw_head_sha, exclude_prefixes)
    except RevisionComputationError:
        head_sha = ""  # a repo with zero commits yet; dirty_tree_hash still covers everything

    # --untracked-files=all: without it, git collapses an entirely-new,
    # untracked DIRECTORY into one line ("?? governance/") instead of
    # listing its files — which would silently defeat exclude_prefixes
    # matching against a path inside that directory (e.g.
    # "governance/approvals/x.json") and, more generally, would hash a
    # whole new directory as a single opaque unit instead of its actual
    # per-file contents.
    status = _run_git(["status", "--porcelain=v1", "--no-renames", "--untracked-files=all"], root)
    changed_paths: list[str] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        # Porcelain v1: two status chars, one space, then the path.
        path = line[3:]
        if any(path.startswith(prefix) for prefix in exclude_prefixes):
            continue
        changed_paths.append(path)

    if not changed_paths:
        return RepositoryRevision(head_sha=head_sha, dirty_tree_hash="")

    entries: list[str] = []
    for rel_path in sorted(changed_paths):
        full_path = root / rel_path
        if full_path.is_file():
            content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
        else:
            # Deleted file (or a directory entry from a rare porcelain
            # edge case) — record its absence explicitly rather than
            # skipping it silently, so a deletion still changes the hash.
            content_hash = "ABSENT"
        entries.append(f"{rel_path}:{content_hash}")

    combined = "\n".join(entries)
    dirty_tree_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return RepositoryRevision(head_sha=head_sha, dirty_tree_hash=dirty_tree_hash)


def revision_diff(approved: str, current: str) -> list[str]:
    """Human-readable list of what differs between an approved revision
    string and the current one — never silently decide "close enough,"
    report the difference (per the discovery report's own §6 requirement)."""
    a = RepositoryRevision.parse(approved)
    b = RepositoryRevision.parse(current)
    diffs: list[str] = []
    if a.head_sha != b.head_sha:
        diffs.append(f"HEAD changed: {a.head_sha!r} -> {b.head_sha!r}")
    if a.dirty_tree_hash != b.dirty_tree_hash:
        diffs.append(
            f"working-tree content changed (dirty_tree_hash {a.dirty_tree_hash!r} -> {b.dirty_tree_hash!r})",
        )
    return diffs
