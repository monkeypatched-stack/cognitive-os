"""Evidence Package — Deterministic document structure for operational evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List
import uuid


@dataclass
class WorkloadInfo:
    """Information about the workload being executed."""

    name: str
    version: str
    module: str | None = None
    capability: str | None = None


@dataclass
class GoalInfo:
    """Information about the goal being pursued."""

    description: str
    success_criteria: str | None = None
    priority: str = "medium"


@dataclass
class RuntimeMetrics:
    """Runtime execution metrics."""

    requests: int = 0
    llm_calls: int = 0
    successful_generations: int = 0
    failed_generations: int = 0
    fallbacks: Dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class CodeMetrics:
    """Code modification metrics."""

    files_examined: int = 0
    files_modified: int = 0
    insertions: int = 0
    deletions: int = 0
    lines_changed: int = 0


@dataclass
class ValidationResults:
    """Validation and testing results."""

    syntax_pass: int = 0
    syntax_fail: int = 0
    unit_tests_passed: int = 0
    unit_tests_failed: int = 0
    integration_tests_passed: int = 0
    integration_tests_failed: int = 0


@dataclass
class EvidencePackage:
    """Deterministic evidence package for a single execution."""

    execution_id: str = field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    workload: WorkloadInfo
    goal: GoalInfo

    status: str = "UNKNOWN"
    confidence: float = 0.0

    runtime: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    code: CodeMetrics = field(default_factory=CodeMetrics)
    validation: ValidationResults = field(default_factory=ValidationResults)

    observations: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    error_details: List[Dict[str, Any]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def add_observation(self, observation: str) -> None:
        """Add an observation to the evidence package."""
        self.observations.append(observation)

    def add_recommendation(self, recommendation: str) -> None:
        """Add a recommendation to the evidence package."""
        self.recommendations.append(recommendation)

    def add_error(self, error_type: str, message: str, **details) -> None:
        """Add an error detail to the evidence package."""
        error_detail = {
            "type": error_type,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            **details,
        }
        self.error_details.append(error_detail)

    def set_status(self, status: str, confidence: float = 1.0) -> None:
        """Set the overall status of the execution."""
        self.status = status
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        """Convert evidence package to dictionary for serialization."""
        return {
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "workload": {
                "name": self.workload.name,
                "version": self.workload.version,
                "module": self.workload.module,
                "capability": self.workload.capability,
            },
            "goal": {
                "description": self.goal.description,
                "success_criteria": self.goal.success_criteria,
                "priority": self.goal.priority,
            },
            "status": self.status,
            "confidence": self.confidence,
            "runtime": {
                "requests": self.runtime.requests,
                "llm_calls": self.runtime.llm_calls,
                "successful_generations": self.runtime.successful_generations,
                "failed_generations": self.runtime.failed_generations,
                "fallbacks": self.runtime.fallbacks,
                "duration_ms": self.runtime.duration_ms,
            },
            "code": {
                "files_examined": self.code.files_examined,
                "files_modified": self.code.files_modified,
                "insertions": self.code.insertions,
                "deletions": self.code.deletions,
                "lines_changed": self.code.lines_changed,
            },
            "validation": {
                "syntax_pass": self.validation.syntax_pass,
                "syntax_fail": self.validation.syntax_fail,
                "unit_tests_passed": self.validation.unit_tests_passed,
                "unit_tests_failed": self.validation.unit_tests_failed,
                "integration_tests_passed": self.validation.integration_tests_passed,
                "integration_tests_failed": self.validation.integration_tests_failed,
            },
            "observations": self.observations,
            "recommendations": self.recommendations,
            "error_details": self.error_details,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidencePackage:
        """Create evidence package from dictionary."""
        workload_data = data.get("workload", {})
        goal_data = data.get("goal", {})
        runtime_data = data.get("runtime", {})
        code_data = data.get("code", {})
        validation_data = data.get("validation", {})

        return cls(
            execution_id=data.get("execution_id", f"exec-{uuid.uuid4().hex[:8]}"),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat()),
            workload=WorkloadInfo(
                name=workload_data.get("name", "unknown"),
                version=workload_data.get("version", "1.0.0"),
                module=workload_data.get("module"),
                capability=workload_data.get("capability"),
            ),
            goal=GoalInfo(
                description=goal_data.get("description", ""),
                success_criteria=goal_data.get("success_criteria"),
                priority=goal_data.get("priority", "medium"),
            ),
            status=data.get("status", "UNKNOWN"),
            confidence=data.get("confidence", 0.0),
            runtime=RuntimeMetrics(
                requests=runtime_data.get("requests", 0),
                llm_calls=runtime_data.get("llm_calls", 0),
                successful_generations=runtime_data.get("successful_generations", 0),
                failed_generations=runtime_data.get("failed_generations", 0),
                fallbacks=runtime_data.get("fallbacks", {}),
                duration_ms=runtime_data.get("duration_ms", 0.0),
            ),
            code=CodeMetrics(
                files_examined=code_data.get("files_examined", 0),
                files_modified=code_data.get("files_modified", 0),
                insertions=code_data.get("insertions", 0),
                deletions=code_data.get("deletions", 0),
                lines_changed=code_data.get("lines_changed", 0),
            ),
            validation=ValidationResults(
                syntax_pass=validation_data.get("syntax_pass", 0),
                syntax_fail=validation_data.get("syntax_fail", 0),
                unit_tests_passed=validation_data.get("unit_tests_passed", 0),
                unit_tests_failed=validation_data.get("unit_tests_failed", 0),
                integration_tests_passed=validation_data.get("integration_tests_passed", 0),
                integration_tests_failed=validation_data.get("integration_tests_failed", 0),
            ),
            observations=data.get("observations", []),
            recommendations=data.get("recommendations", []),
            error_details=data.get("error_details", []),
            context=data.get("context", {}),
        )
