"""Software Engineering aggregate roots.

Each aggregate root enforces domain invariants for its bounded context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class PRStatus(StrEnum):
    OPEN = "open"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    MERGED = "merged"
    REJECTED = "rejected"


class DeploymentStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class PipelineStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PullRequestAggregate:
    """Aggregate root for pull requests."""

    pr_id: str = ""
    title: str = ""
    author: str = ""
    source_branch: str = ""
    target_branch: str = ""
    status: PRStatus = PRStatus.OPEN
    reviewers: list[str] = field(default_factory=list)
    reviews: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    merged_at: datetime | None = None

    def approve(self, reviewer: str) -> None:
        if self.status not in (PRStatus.OPEN, PRStatus.REVIEWING):
            raise ValueError(f"Cannot approve PR in {self.status} state")
        self.reviews[reviewer] = "approved"
        self.status = PRStatus.APPROVED

    def reject(self, reviewer: str, reason: str = "") -> None:
        if self.status not in (PRStatus.OPEN, PRStatus.REVIEWING):
            raise ValueError(f"Cannot reject PR in {self.status} state")
        self.reviews[reviewer] = f"rejected: {reason}"
        self.status = PRStatus.REJECTED

    def merge(self) -> None:
        if self.status != PRStatus.APPROVED:
            raise ValueError("Cannot merge unapproved PR")
        self.status = PRStatus.MERGED
        self.merged_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pr_id,
            "title": self.title,
            "status": self.status.value,
            "author": self.author,
            "reviews": self.reviews,
        }


@dataclass
class TestSuiteAggregate:
    """Aggregate root for test suites."""

    suite_id: str = ""
    name: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    status: str = "pending"
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.passed / max(self.total, 1)

    def record_result(self, name: str, passed: bool, duration_ms: float = 0.0) -> None:
        self.total += 1
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        self.results.append(
            {"name": name, "passed": passed, "duration_ms": duration_ms}
        )

    def finalize(self) -> None:
        self.status = "passed" if self.failed == 0 else "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.suite_id,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "status": self.status,
            "success_rate": self.success_rate,
        }


@dataclass
class DeploymentAggregate:
    """Aggregate root for deployments."""

    deployment_id: str = ""
    version: str = ""
    environment: str = ""
    status: DeploymentStatus = DeploymentStatus.PENDING
    steps: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)

    def start(self) -> None:
        if self.status != DeploymentStatus.PENDING:
            raise ValueError(f"Cannot start deployment in {self.status} state")
        self.status = DeploymentStatus.IN_PROGRESS

    def complete_step(self, step: str) -> None:
        if step not in self.steps:
            raise ValueError(f"Unknown step: {step}")
        self.completed_steps.append(step)

    def complete(self) -> None:
        if set(self.completed_steps) != set(self.steps):
            raise ValueError("Cannot complete deployment with unfinished steps")
        self.status = DeploymentStatus.COMPLETED

    def rollback(self) -> None:
        self.status = DeploymentStatus.ROLLED_BACK

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.deployment_id,
            "version": self.version,
            "environment": self.environment,
            "status": self.status.value,
        }


@dataclass
class PipelineAggregate:
    """Aggregate root for CI/CD pipelines."""

    pipeline_id: str = ""
    name: str = ""
    status: PipelineStatus = PipelineStatus.CREATED
    stages: list[str] = field(default_factory=list)
    completed_stages: list[str] = field(default_factory=list)

    def start(self) -> None:
        self.status = PipelineStatus.RUNNING

    def complete_stage(self, stage: str, passed: bool) -> None:
        if not passed:
            self.status = PipelineStatus.FAILED
            return
        self.completed_stages.append(stage)
        if set(self.completed_stages) == set(self.stages):
            self.status = PipelineStatus.PASSED

    def cancel(self) -> None:
        self.status = PipelineStatus.CANCELLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pipeline_id,
            "name": self.name,
            "status": self.status.value,
            "stages": len(self.stages),
        }


@dataclass
class CodeReviewAggregate:
    """Aggregate root for code reviews."""

    review_id: str = ""
    pr_id: str = ""
    reviewer: str = ""
    status: str = "pending"
    findings: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0

    def add_finding(self, severity: str, description: str, file_path: str = "") -> None:
        self.findings.append(
            {"severity": severity, "description": description, "file": file_path}
        )

    def complete(self, score: float) -> None:
        self.score = score
        self.status = "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.review_id,
            "pr_id": self.pr_id,
            "status": self.status,
            "findings": len(self.findings),
            "score": self.score,
        }
