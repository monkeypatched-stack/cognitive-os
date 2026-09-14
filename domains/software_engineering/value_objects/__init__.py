"""Software Engineering Domain Value Objects.

DDD Value Objects — immutable objects defined by their attributes.
"""

from dataclasses import dataclass
from enum import Enum


class PRStatus(Enum):
    """Pull request status value object."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"
    DRAFT = "draft"


class TestResult(Enum):
    """Test result value object."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class DeploymentEnvironment(Enum):
    """Deployment environment value object."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    CANARY = "canary"


class PipelineStage(Enum):
    """Pipeline stage value object."""

    BUILD = "build"
    TEST = "test"
    LINT = "lint"
    SECURITY = "security"
    DEPLOY = "deploy"
    APPROVE = "approve"


@dataclass(frozen=True)
class CodeMetrics:
    """Code metrics value object."""

    lines_added: int = 0
    lines_removed: int = 0
    files_changed: int = 0
    complexity_delta: float = 0.0

    @property
    def net_change(self) -> int:
        return self.lines_added - self.lines_removed


@dataclass(frozen=True)
class TestCoverage:
    """Test coverage value object."""

    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    function_coverage: float = 0.0

    @property
    def overall(self) -> float:
        return (self.line_coverage + self.branch_coverage + self.function_coverage) / 3
