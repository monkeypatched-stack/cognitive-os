"""Software Engineering Domain Entities.

DDD Entities — objects with identity that represent domain concepts.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PullRequest:
    """Pull request entity — a code change request."""

    id: str
    title: str
    status: str  # open, merged, closed
    author: str = ""
    branch: str = ""
    target_branch: str = "main"
    reviewers: list = None
    files_changed: list = None

    def __post_init__(self):
        if self.reviewers is None:
            self.reviewers = []
        if self.files_changed is None:
            self.files_changed = []


@dataclass
class TestSuite:
    """Test suite entity — a collection of tests."""

    id: str
    name: str
    status: str  # passed, failed, running
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    coverage_pct: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class Deployment:
    """Deployment entity — a deployment to an environment."""

    id: str
    environment: str  # staging, production, canary
    status: str  # pending, in_progress, completed, failed
    version: str = ""
    triggered_by: str = ""
    commit_sha: str = ""


@dataclass
class Pipeline:
    """Pipeline entity — a CI/CD pipeline."""

    id: str
    name: str
    status: str  # running, passed, failed
    stages: list = None
    trigger: str = ""  # push, PR, manual

    def __post_init__(self):
        if self.stages is None:
            self.stages = []


@dataclass
class CodeReview:
    """Code review entity — a review of code changes."""

    id: str
    pr_id: str
    reviewer: str
    status: str  # pending, approved, changes_requested
    comments: list = None
    approval_count: int = 0

    def __post_init__(self):
        if self.comments is None:
            self.comments = []


@dataclass
class Commit:
    """Commit entity — a code commit."""

    sha: str
    message: str
    author: str = ""
    timestamp: str = ""
    files_changed: list = None

    def __post_init__(self):
        if self.files_changed is None:
            self.files_changed = []


@dataclass
class Branch:
    """Branch entity — a git branch."""

    name: str
    head_sha: str = ""
    base_branch: str = "main"
    is_protected: bool = False
