"""Manufacturing Knowledge Pack — knowledge from completed manufacturing workload runs.

Every successful manufacturing workload publishes a pack containing:
- machine and asset metadata
- sensor readings and telemetry snapshots
- maintenance and calibration history
- downtime events and root causes
- quality inspection results
- workflow topology
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.monkey_brain.knowledge.item import KnowledgeItem, Modality

logger = logging.getLogger(__name__)


@dataclass
class ManufacturingKnowledgePack:
    """Published knowledge from a completed manufacturing workload run."""

    workload_id: str = ""
    goal: str = ""
    plant_id: str = ""
    machine_ids: list[str] = field(default_factory=list)
    sensor_snapshot: dict[str, Any] = field(default_factory=dict)
    maintenance_events: list[dict[str, Any]] = field(default_factory=list)
    calibration_records: list[dict[str, Any]] = field(default_factory=list)
    downtime_events: list[dict[str, Any]] = field(default_factory=list)
    quality_results: dict[str, Any] = field(default_factory=dict)
    workflow_topology: list[str] = field(default_factory=list)
    confidence: float = 0.0
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "goal": self.goal,
            "plant_id": self.plant_id,
            "machine_count": len(self.machine_ids),
            "sensor_keys": list(self.sensor_snapshot.keys()),
            "maintenance_events": len(self.maintenance_events),
            "calibration_records": len(self.calibration_records),
            "downtime_events": len(self.downtime_events),
            "quality_results": self.quality_results,
            "workflow_steps": len(self.workflow_topology),
            "confidence": self.confidence,
            "published_at": self.published_at.isoformat(),
        }

    def to_knowledge_items(self) -> list[KnowledgeItem]:
        items = []

        items.append(
            KnowledgeItem(
                id=f"mfg-goal-{self.workload_id}",
                content=f"Manufacturing workload: {self.goal} (plant={self.plant_id}, machines={len(self.machine_ids)})",
                modality=Modality.DOCUMENT,
                source=f"mfg_workload:{self.workload_id}",
                provenance=self.confidence,
            )
        )
        if self.sensor_snapshot:
            items.append(
                KnowledgeItem(
                    id=f"mfg-sensor-{self.workload_id}",
                    content=f"Sensor snapshot: {len(self.sensor_snapshot)} readings from plant {self.plant_id}",
                    modality=Modality.TELEMETRY,
                    source=f"sensors:{self.workload_id}",
                    provenance=self.confidence,
                )
            )
        if self.maintenance_events:
            critical = sum(
                1 for e in self.maintenance_events if e.get("severity") == "critical"
            )
            items.append(
                KnowledgeItem(
                    id=f"mfg-maint-{self.workload_id}",
                    content=f"Maintenance: {len(self.maintenance_events)} events, {critical} critical",
                    modality=Modality.DOCUMENT,
                    source=f"maintenance:{self.workload_id}",
                    provenance=self.confidence,
                )
            )
        if self.downtime_events:
            total_downtime = sum(
                e.get("duration_minutes", 0) for e in self.downtime_events
            )
            items.append(
                KnowledgeItem(
                    id=f"mfg-downtime-{self.workload_id}",
                    content=f"Downtime: {len(self.downtime_events)} events, {total_downtime:.0f} min total",
                    modality=Modality.DOCUMENT,
                    source=f"downtime:{self.workload_id}",
                    provenance=self.confidence,
                )
            )
        if self.quality_results:
            items.append(
                KnowledgeItem(
                    id=f"mfg-quality-{self.workload_id}",
                    content=f"Quality: pass_rate={self.quality_results.get('pass_rate', 'unknown')}",
                    modality=Modality.DOCUMENT,
                    source=f"quality:{self.workload_id}",
                    provenance=self.confidence,
                )
            )
        if self.workflow_topology:
            items.append(
                KnowledgeItem(
                    id=f"mfg-workflow-{self.workload_id}",
                    content=f"Workflow: {' → '.join(self.workflow_topology)}",
                    modality=Modality.DOCUMENT,
                    source=f"workflow:{self.workload_id}",
                    provenance=self.confidence,
                )
            )
        return items


class ManufacturingKnowledgePublisher:
    """Publishes manufacturing knowledge after successful workload execution."""

    def __init__(self):
        self._published: list[ManufacturingKnowledgePack] = []

    def publish(
        self,
        workload_id: str,
        goal: str,
        plant_id: str = "",
        machine_ids: list[str] | None = None,
        sensor_snapshot: dict[str, Any] | None = None,
        maintenance_events: list[dict] | None = None,
        calibration_records: list[dict] | None = None,
        downtime_events: list[dict] | None = None,
        quality_results: dict | None = None,
        workflow_topology: list[str] | None = None,
        confidence: float = 0.8,
    ) -> ManufacturingKnowledgePack:
        kp = ManufacturingKnowledgePack(
            workload_id=workload_id,
            goal=goal,
            plant_id=plant_id,
            machine_ids=machine_ids or [],
            sensor_snapshot=sensor_snapshot or {},
            maintenance_events=maintenance_events or [],
            calibration_records=calibration_records or [],
            downtime_events=downtime_events or [],
            quality_results=quality_results or {},
            workflow_topology=workflow_topology or [],
            confidence=confidence,
        )
        self._published.append(kp)
        logger.info(
            "Published MFG KP: %s (%d items)", workload_id, len(kp.to_knowledge_items())
        )
        return kp

    def get_published(self) -> list[ManufacturingKnowledgePack]:
        return list(self._published)

    def search(self, goal: str) -> list[ManufacturingKnowledgePack]:
        goal_lower = goal.lower()
        return [kp for kp in self._published if goal_lower in kp.goal.lower()]

    def get_knowledge_items_for_retrieval(self, goal: str) -> list[KnowledgeItem]:
        items = []
        for kp in self.search(goal):
            items.extend(kp.to_knowledge_items())
        return items

    def by_plant(self, plant_id: str) -> list[ManufacturingKnowledgePack]:
        return [kp for kp in self._published if kp.plant_id == plant_id]

    def summary(self) -> dict[str, Any]:
        return {
            "total_published": len(self._published),
            "plants": list({kp.plant_id for kp in self._published if kp.plant_id}),
            "total_items": sum(len(kp.to_knowledge_items()) for kp in self._published),
            "avg_confidence": sum(kp.confidence for kp in self._published)
            / max(len(self._published), 1),
        }
