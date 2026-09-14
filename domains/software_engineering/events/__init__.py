"""Software Engineering Domain Events.

DDD Domain Events — things that happened in the software engineering domain.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PRCreated:
    """Event: a pull request was created."""

    pr_id: str
    title: str
    author: str = ""
    branch: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class PRMerged:
    """Event: a pull request was merged."""

    pr_id: str
    merged_by: str = ""
    commit_sha: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class TestsPassed:
    """Event: tests passed."""

    suite_id: str
    total: int = 0
    passed: int = 0
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class TestsFailed:
    """Event: tests failed."""

    suite_id: str
    total: int = 0
    failed: int = 0
    failure_details: list = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class DeploymentStarted:
    """Event: deployment was started."""

    deployment_id: str
    environment: str
    version: str = ""
    triggered_by: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class DeploymentCompleted:
    """Event: deployment was completed."""

    deployment_id: str
    environment: str
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class PipelinePassed:
    """Event: pipeline passed all stages."""

    pipeline_id: str
    stages_passed: int = 0
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class PipelineFailed:
    """Event: pipeline failed at a stage."""

    pipeline_id: str
    failed_stage: str = ""
    error_message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class ReviewApproved:
    """Event: code review was approved."""

    review_id: str
    pr_id: str
    reviewer: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass(frozen=True)
class ReviewRejected:
    """Event: code review was rejected."""

    review_id: str
    pr_id: str
    reviewer: str = ""
    reason: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
