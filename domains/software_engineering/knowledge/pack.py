"""Software Engineering Knowledge Pack — knowledge from completed software workload runs.

Every successful software engineering workload publishes a pack containing:
- specification and goal
- generated source files
- governance and compliance findings
- benchmark results
- repair history
- workflow topology
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.knowledge.item import KnowledgeItem, Modality

logger = logging.getLogger(__name__)


@dataclass
class SoftwareEngineeringKnowledgePack:
    """Published knowledge from a completed software engineering workload run."""

    spec_id: str = ""
    goal: str = ""
    domain: str = ""
    generated_files: dict[str, str] = field(default_factory=dict)
    governance_findings: list[dict[str, Any]] = field(default_factory=list)
    compliance_findings: list[dict[str, Any]] = field(default_factory=list)
    benchmark_results: dict[str, Any] = field(default_factory=dict)
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    workflow_topology: list[str] = field(default_factory=list)
    confidence: float = 0.0
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "goal": self.goal,
            "domain": self.domain,
            "file_count": len(self.generated_files),
            "governance_findings": len(self.governance_findings),
            "compliance_findings": len(self.compliance_findings),
            "benchmark_results": self.benchmark_results,
            "repair_count": len(self.repair_history),
            "workflow_steps": len(self.workflow_topology),
            "confidence": self.confidence,
            "published_at": self.published_at.isoformat(),
        }

    def to_knowledge_items(self) -> list[KnowledgeItem]:
        items = []

        items.append(
            KnowledgeItem(
                id=f"swe-spec-{self.spec_id}",
                content=f"Specification: {self.goal} (domain={self.domain})",
                modality=Modality.DOCUMENT,
                source=f"swe_workload:{self.spec_id}",
                provenance=self.confidence,
            )
        )
        if self.generated_files:
            items.append(
                KnowledgeItem(
                    id=f"swe-code-{self.spec_id}",
                    content=f"Generated {len(self.generated_files)} files: {', '.join(list(self.generated_files.keys())[:5])}",
                    modality=Modality.CODE,
                    source=f"codegen:{self.spec_id}",
                    provenance=self.confidence,
                )
            )
        if self.governance_findings:
            items.append(
                KnowledgeItem(
                    id=f"swe-govern-{self.spec_id}",
                    content=f"Governance: {len(self.governance_findings)} findings, {sum(1 for f in self.governance_findings if f.get('severity') == 'critical')} critical",
                    modality=Modality.DOCUMENT,
                    source=f"governance:{self.spec_id}",
                    provenance=self.confidence,
                )
            )
        if self.benchmark_results:
            items.append(
                KnowledgeItem(
                    id=f"swe-bench-{self.spec_id}",
                    content=f"Benchmark: {json.dumps(self.benchmark_results)[:200]}",
                    modality=Modality.DOCUMENT,
                    source=f"benchmark:{self.spec_id}",
                    provenance=self.confidence,
                )
            )
        if self.workflow_topology:
            items.append(
                KnowledgeItem(
                    id=f"swe-workflow-{self.spec_id}",
                    content=f"Workflow: {' → '.join(self.workflow_topology)}",
                    modality=Modality.DOCUMENT,
                    source=f"workflow:{self.spec_id}",
                    provenance=self.confidence,
                )
            )
        return items


class SoftwareEngineeringKnowledgePublisher:
    """Publishes software engineering knowledge after successful workload execution."""

    def __init__(self):
        self._published: list[SoftwareEngineeringKnowledgePack] = []

    def publish(
        self,
        spec_id: str,
        goal: str,
        domain: str = "",
        generated_files: dict[str, str] | None = None,
        governance_findings: list[dict] | None = None,
        compliance_findings: list[dict] | None = None,
        benchmark_results: dict | None = None,
        repair_history: list[dict] | None = None,
        workflow_topology: list[str] | None = None,
        confidence: float = 0.8,
    ) -> SoftwareEngineeringKnowledgePack:
        kp = SoftwareEngineeringKnowledgePack(
            spec_id=spec_id,
            goal=goal,
            domain=domain,
            generated_files=generated_files or {},
            governance_findings=governance_findings or [],
            compliance_findings=compliance_findings or [],
            benchmark_results=benchmark_results or {},
            repair_history=repair_history or [],
            workflow_topology=workflow_topology or [],
            confidence=confidence,
        )
        self._published.append(kp)
        logger.info(
            "Published SWE KP: %s (%d items)", spec_id, len(kp.to_knowledge_items())
        )
        return kp

    def get_published(self) -> list[SoftwareEngineeringKnowledgePack]:
        return list(self._published)

    def search(self, goal: str) -> list[SoftwareEngineeringKnowledgePack]:
        goal_lower = goal.lower()
        return [kp for kp in self._published if goal_lower in kp.goal.lower()]

    def get_knowledge_items_for_retrieval(self, goal: str) -> list[KnowledgeItem]:
        items = []
        for kp in self.search(goal):
            items.extend(kp.to_knowledge_items())
        return items

    def summary(self) -> dict[str, Any]:
        return {
            "total_published": len(self._published),
            "domains": list({kp.domain for kp in self._published if kp.domain}),
            "total_items": sum(len(kp.to_knowledge_items()) for kp in self._published),
            "avg_confidence": sum(kp.confidence for kp in self._published)
            / max(len(self._published), 1),
        }
