"""Evidence Collector — Collects operational evidence during execution."""

from __future__ import annotations

import time
from typing import Any, Dict
from .evidence_package import EvidencePackage, WorkloadInfo, GoalInfo


class EvidenceCollector:
    """Collects operational evidence during a single execution."""

    def __init__(self, workload: WorkloadInfo, goal: GoalInfo):
        """Initialize evidence collector for a specific workload and goal."""
        self.evidence = EvidencePackage(workload=workload, goal=goal)
        self._start_time = time.time()

    def increment_requests(self, count: int = 1) -> None:
        """Increment the request count."""
        self.evidence.runtime.requests += count

    def increment_llm_calls(self, count: int = 1) -> None:
        """Increment the LLM call count."""
        self.evidence.runtime.llm_calls += count

    def record_generation_result(self, success: bool = True) -> None:
        """Record the result of a code generation attempt."""
        if success:
            self.evidence.runtime.successful_generations += 1
        else:
            self.evidence.runtime.failed_generations += 1

    def record_fallback(self, fallback_type: str) -> None:
        """Record a fallback event."""
        self.evidence.runtime.fallbacks[fallback_type] = self.evidence.runtime.fallbacks.get(fallback_type, 0) + 1

    def increment_files_examined(self, count: int = 1) -> None:
        """Increment the files examined count."""
        self.evidence.code.files_examined += count

    def increment_files_modified(self, count: int = 1) -> None:
        """Increment the files modified count."""
        self.evidence.code.files_modified += count

    def add_code_changes(self, insertions: int = 0, deletions: int = 0, lines_changed: int = 0) -> None:
        """Add code change metrics."""
        self.evidence.code.insertions += insertions
        self.evidence.code.deletions += deletions
        self.evidence.code.lines_changed += lines_changed

    def record_validation_result(
        self,
        syntax_pass: bool = True,
        unit_test_pass: bool = True,
        integration_test_pass: bool = True,
    ) -> None:
        """Record validation test results."""
        if syntax_pass:
            self.evidence.validation.syntax_pass += 1
        else:
            self.evidence.validation.syntax_fail += 1

        if unit_test_pass:
            self.evidence.validation.unit_tests_passed += 1
        else:
            self.evidence.validation.unit_tests_failed += 1

        if integration_test_pass:
            self.evidence.validation.integration_tests_passed += 1
        else:
            self.evidence.validation.integration_tests_failed += 1

    def add_observation(self, observation: str) -> None:
        """Add an observation to the evidence."""
        self.evidence.add_observation(observation)

    def add_recommendation(self, recommendation: str) -> None:
        """Add a recommendation to the evidence."""
        self.evidence.add_recommendation(recommendation)

    def add_error(self, error_type: str, message: str, **details) -> None:
        """Add an error detail to the evidence."""
        self.evidence.add_error(error_type, message, **details)

    def add_context(self, key: str, value: Any) -> None:
        """Add context information to the evidence."""
        self.evidence.context[key] = value

    def complete_execution(self, status: str = "SUCCESS", confidence: float = 1.0) -> None:
        """Complete the execution and calculate final metrics."""
        self.evidence.runtime.duration_ms = (time.time() - self._start_time) * 1000
        self.evidence.set_status(status, confidence)

    def get_evidence(self) -> EvidencePackage:
        """Get the collected evidence package."""
        return self.evidence

    def to_dict(self) -> Dict[str, Any]:
        """Convert collected evidence to dictionary."""
        return self.evidence.to_dict()
