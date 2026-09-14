"""Domain-Specific Evidence Adapters - Convert ExecutionOutcome to domain evidence."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
import uuid

from .core import ExecutionOutcome

logger = logging.getLogger(__name__)


@dataclass
class DomainEvidence:
    """Base class for all domain-specific evidence."""

    domain: str
    execution_id: str
    goal_id: str
    evidence_id: str = field(default_factory=lambda: f"evidence-{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = "UNKNOWN"
    confidence: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    raw_outcome: Dict[str, Any] = field(default_factory=dict)


class BaseEvidenceAdapter(ABC):
    """Base class for domain-specific evidence adapters."""

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain this adapter handles."""
        pass

    @abstractmethod
    def adapt(self, outcome: ExecutionOutcome) -> DomainEvidence:
        """Convert ExecutionOutcome to domain-specific evidence."""
        pass

    def can_adapt(self, outcome: ExecutionOutcome) -> bool:
        """Check if this adapter can handle the outcome."""
        return outcome.domain == self.domain


class SoftwareEngineeringEvidence(DomainEvidence):
    """Software Engineering domain evidence."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.domain = "software_engineering"


class SoftwareEngineeringEvidenceAdapter(BaseEvidenceAdapter):
    """Adapter for Software Engineering evidence."""

    @property
    def domain(self) -> str:
        return "software_engineering"

    def adapt(self, outcome: ExecutionOutcome) -> SoftwareEngineeringEvidence:
        """Convert to Software Engineering evidence."""
        # Extract software engineering specific metrics
        se_metrics = {
            "agents_executed": len(outcome.results),
            "successful_agents": sum(1 for r in outcome.results.values() if "error" not in r),
            "failed_agents": sum(1 for r in outcome.results.values() if "error" in r),
            "code_changes": 0,
            "tests_passed": 0,
            "tests_failed": 0,
        }

        # Extract findings and recommendations from evidence
        findings = []
        recommendations = []

        for agent_id, evidence in outcome.evidence.items():
            if isinstance(evidence, dict):
                # Extract SE-specific findings
                if "observations" in evidence:
                    findings.extend(evidence["observations"])
                if "recommendations" in evidence:
                    recommendations.extend(evidence["recommendations"])

                # Extract code metrics if available
                if "code_changes" in evidence:
                    se_metrics["code_changes"] += evidence["code_changes"]
                if "tests_passed" in evidence:
                    se_metrics["tests_passed"] += evidence["tests_passed"]
                if "tests_failed" in evidence:
                    se_metrics["tests_failed"] += evidence["tests_failed"]

        return SoftwareEngineeringEvidence(
            domain=self.domain,
            execution_id=outcome.execution_id,
            goal_id=outcome.goal_id,
            status=outcome.status,
            confidence=outcome.confidence,
            metrics=se_metrics,
            findings=findings,
            recommendations=recommendations,
            raw_outcome=outcome.results,
        )


class ManufacturingEvidence(DomainEvidence):
    """Manufacturing domain evidence."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.domain = "manufacturing"


class ManufacturingEvidenceAdapter(BaseEvidenceAdapter):
    """Adapter for Manufacturing evidence."""

    @property
    def domain(self) -> str:
        return "manufacturing"

    def adapt(self, outcome: ExecutionOutcome) -> ManufacturingEvidence:
        """Convert to Manufacturing evidence."""
        # Extract manufacturing specific metrics
        mfg_metrics = {
            "agents_executed": len(outcome.results),
            "successful_agents": sum(1 for r in outcome.results.values() if "error" not in r),
            "failed_agents": sum(1 for r in outcome.results.values() if "error" in r),
            "batch_records_processed": 0,
            "quality_issues_detected": 0,
            "sop_violations": 0,
        }

        findings = []
        recommendations = []

        for agent_id, evidence in outcome.evidence.items():
            if isinstance(evidence, dict):
                if "observations" in evidence:
                    findings.extend(evidence["observations"])
                if "recommendations" in evidence:
                    recommendations.extend(evidence["recommendations"])

                # Extract manufacturing metrics
                if "batch_records" in evidence:
                    mfg_metrics["batch_records_processed"] += evidence["batch_records"]
                if "quality_issues" in evidence:
                    mfg_metrics["quality_issues_detected"] += evidence["quality_issues"]
                if "sop_violations" in evidence:
                    mfg_metrics["sop_violations"] += evidence["sop_violations"]

        return ManufacturingEvidence(
            domain=self.domain,
            execution_id=outcome.execution_id,
            goal_id=outcome.goal_id,
            status=outcome.status,
            confidence=outcome.confidence,
            metrics=mfg_metrics,
            findings=findings,
            recommendations=recommendations,
            raw_outcome=outcome.results,
        )


class RoboticsEvidence(DomainEvidence):
    """Robotics domain evidence."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.domain = "robotics"


class RoboticsEvidenceAdapter(BaseEvidenceAdapter):
    """Adapter for Robotics evidence."""

    @property
    def domain(self) -> str:
        return "robotics"

    def adapt(self, outcome: ExecutionOutcome) -> RoboticsEvidence:
        """Convert to Robotics evidence."""
        # Extract robotics specific metrics
        robotics_metrics = {
            "agents_executed": len(outcome.results),
            "successful_agents": sum(1 for r in outcome.results.values() if "error" not in r),
            "failed_agents": sum(1 for r in outcome.results.values() if "error" in r),
            "navigation_success_rate": 0.0,
            "collision_avoidance_events": 0,
            "safety_violations": 0,
        }

        findings = []
        recommendations = []

        for agent_id, evidence in outcome.evidence.items():
            if isinstance(evidence, dict):
                if "observations" in evidence:
                    findings.extend(evidence["observations"])
                if "recommendations" in evidence:
                    recommendations.extend(evidence["recommendations"])

                # Extract robotics metrics
                if "navigation_success" in evidence:
                    robotics_metrics["navigation_success_rate"] = evidence["navigation_success"]
                if "collision_avoidance" in evidence:
                    robotics_metrics["collision_avoidance_events"] += evidence["collision_avoidance"]
                if "safety_violations" in evidence:
                    robotics_metrics["safety_violations"] += evidence["safety_violations"]

        # Calculate success rate if we have data
        if robotics_metrics["agents_executed"] > 0:
            robotics_metrics["navigation_success_rate"] = (
                robotics_metrics["successful_agents"] / robotics_metrics["agents_executed"]
            )

        return RoboticsEvidence(
            domain=self.domain,
            execution_id=outcome.execution_id,
            goal_id=outcome.goal_id,
            status=outcome.status,
            confidence=outcome.confidence,
            metrics=robotics_metrics,
            findings=findings,
            recommendations=recommendations,
            raw_outcome=outcome.results,
        )


class EvidenceAdapterRegistry:
    """Registry for domain-specific evidence adapters."""

    def __init__(self):
        self._adapters: Dict[str, BaseEvidenceAdapter] = {}  # domain -> adapter

    def register_adapter(self, adapter: BaseEvidenceAdapter) -> None:
        """Register an evidence adapter."""
        self._adapters[adapter.domain] = adapter

    def adapt_outcome(self, outcome: ExecutionOutcome) -> DomainEvidence:
        """Convert ExecutionOutcome to domain-specific evidence."""
        adapter = self._adapters.get(outcome.domain)
        if not adapter:
            raise ValueError(f"No evidence adapter registered for domain: {outcome.domain}")

        return adapter.adapt(outcome)

    def get_adapter(self, domain: str) -> Optional[BaseEvidenceAdapter]:
        """Get adapter for a specific domain."""
        return self._adapters.get(domain)

    def get_all_domains(self) -> List[str]:
        """Get all domains with registered adapters."""
        return list(self._adapters.keys())


class EvidencePublisher:
    """Publishes domain evidence to appropriate destinations."""

    def __init__(self, adapter_registry: EvidenceAdapterRegistry):
        self.adapter_registry = adapter_registry
        self.publishing_handlers = {
            "software_engineering": self._publish_software_engineering,
            "manufacturing": self._publish_manufacturing,
            "robotics": self._publish_robotics,
            # Add more domains as needed
        }

    async def publish_outcome(self, outcome: ExecutionOutcome) -> DomainEvidence:
        """Convert and publish ExecutionOutcome."""
        # Convert to domain evidence
        evidence = self.adapter_registry.adapt_outcome(outcome)

        # Publish to domain-specific destination
        handler = self.publishing_handlers.get(evidence.domain)
        if handler:
            await handler(evidence)

        return evidence

    async def _publish_software_engineering(self, evidence: SoftwareEngineeringEvidence) -> None:
        """Publish software engineering evidence."""
        # STUB: needs integration with Operational Evidence DB, CI/CD metrics, developer dashboards
        logger.info(
            "Publishing SE evidence: id=%s status=%s findings=%d recommendations=%d",
            evidence.evidence_id,
            evidence.status,
            len(evidence.findings),
            len(evidence.recommendations),
        )

    async def _publish_manufacturing(self, evidence: ManufacturingEvidence) -> None:
        """Publish manufacturing evidence."""
        # STUB: needs integration with MES, quality management system, compliance auditing
        logger.info(
            "Publishing manufacturing evidence: id=%s status=%s quality_issues=%d",
            evidence.evidence_id,
            evidence.status,
            evidence.metrics.get("quality_issues_detected", 0),
        )

    async def _publish_robotics(self, evidence: RoboticsEvidence) -> None:
        """Publish robotics evidence."""
        # STUB: needs integration with robot fleet management, safety monitoring, simulation validation
        logger.info(
            "Publishing robotics evidence: id=%s status=%s nav_success=%.1f%%",
            evidence.evidence_id,
            evidence.status,
            evidence.metrics.get("navigation_success_rate", 0) * 100,
        )


# Example usage
async def example_usage():
    """Example of how the domain-agnostic system works."""

    # 1. Create Monkey Brain runtime
    from .core import MonkeyBrainRuntime, DomainGoal

    MonkeyBrainRuntime()

    # 2. Register domain components (simplified example)
    # In production, you would register real agents and capabilities
    # For this example, we'll just create the structure

    # 3. Create evidence adapters
    adapter_registry = EvidenceAdapterRegistry()
    adapter_registry.register_adapter(SoftwareEngineeringEvidenceAdapter())
    adapter_registry.register_adapter(ManufacturingEvidenceAdapter())
    adapter_registry.register_adapter(RoboticsEvidenceAdapter())

    # 4. Create evidence publisher
    publisher = EvidencePublisher(adapter_registry)

    # 5. Execute a goal (simulated)
    goal = DomainGoal(
        domain="software_engineering",
        objective="Refactor legacy code module",
        success_criteria="All tests pass and code quality improved",
    )

    # Simulate execution outcome
    outcome = ExecutionOutcome(
        execution_id="exec-test123",
        goal_id=goal.goal_id,
        domain=goal.domain,
        status="SUCCESS",
        confidence=0.95,
        results={
            "refactor_agent": {"status": "success", "files_changed": 12},
            "test_agent": {"status": "success", "tests_passed": 42, "tests_failed": 2},
        },
        evidence={
            "refactor_agent": {
                "observations": ["Found cyclic dependencies", "Detected anti-patterns"],
                "recommendations": [
                    "Apply dependency injection",
                    "Use factory pattern",
                ],
                "code_changes": 418,
                "tests_passed": 42,
                "tests_failed": 2,
            }
        },
    )

    # 6. Publish the outcome
    evidence = await publisher.publish_outcome(outcome)

    print(f"\n✅ Domain Evidence Created:")
    print(f"   Domain: {evidence.domain}")
    print(f"   Status: {evidence.status}")
    print(f"   Confidence: {evidence.confidence:.1%}")
    print(f"   Metrics: {evidence.metrics}")
    print(f"   Findings: {evidence.findings}")
    print(f"   Recommendations: {evidence.recommendations}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(example_usage())
