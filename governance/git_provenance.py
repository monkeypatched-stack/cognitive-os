"""Git-commit provenance for an approval record — a stronger signal than
a free-text `approved_by` field, but still NOT authentication.

HONESTY NOTE: git commit authorship is configured locally by whoever runs
`git config user.name/user.email` — it is not verified by any identity
provider, and (without GPG signing) anyone with local git access can set
it to any value. This module therefore does not "authenticate" an
approver. What it DOES give you, which a free-text field alone does not:

  - proof the approval record was actually committed to version control
    (not just an uncommitted file sitting in a working tree that only
    the agent's session ever saw)
  - the commit's timestamp and author as git recorded them, independent
    of anything the ApprovalArtifact itself claims
  - if the repository's commits are GPG-signed (many are not, including
    this one, by default), the signing key identity, which IS a real
    cryptographic signal — but only as strong as the key-management
    practice around it, which this module does not audit

Use this as one more piece of evidence in a human review, not as a
pass/fail gate on its own.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitProvenanceError(Exception):
    pass


@dataclass(frozen=True)
class GitProvenance:
    committed: bool
    commit_sha: str = ""
    author_name: str = ""
    author_email: str = ""
    committed_at: str = ""
    gpg_signed: bool = False
    gpg_signer: str = ""


def approval_git_provenance(approval_path: Path | str, *, repo_root: Path | str) -> GitProvenance:
    """Whether `approval_path` has ever been committed, and by whom
    (per git's own, locally-configured notion of "whom" — see module
    docstring). Returns committed=False — not an error — for an untracked
    file, an uncommitted file, or even a repository with zero commits yet:
    all three mean the same thing here ("no proof this was committed"),
    and treating them as committed=False rather than raising is the
    correct fail-closed-safe answer, not a special case to work around.
    """
    root = Path(repo_root)
    try:
        rel = str(Path(approval_path).resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise GitProvenanceError(f"{approval_path!r} is not inside repo_root {root!r}") from exc

    result = subprocess.run(
        [
            "git",
            "log",
            "-1",
            "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%G?%x1f%GS",
            "--",
            rel,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return GitProvenance(committed=False)

    parts = result.stdout.strip("\n").split("\x1f")
    if len(parts) != 6:
        raise GitProvenanceError(f"unexpected git log output shape for {rel!r}: {log_output!r}")
    commit_sha, author_name, author_email, committed_at, gpg_status, gpg_signer = parts
    # %G? : "G" good signature, "B" bad, "U" unknown, "X"/"Y"/"R"/"E" other
    # invalid/expired states, "N" no signature at all.
    gpg_signed = gpg_status == "G"
    return GitProvenance(
        committed=True,
        commit_sha=commit_sha,
        author_name=author_name,
        author_email=author_email,
        committed_at=committed_at,
        gpg_signed=gpg_signed,
        gpg_signer=gpg_signer if gpg_signed else "",
    )
