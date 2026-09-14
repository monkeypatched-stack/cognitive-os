"""Manufacturing Domain Services — cross-aggregate manufacturing operations."""

from __future__ import annotations
import logging
from typing import Any
from domains.package import DomainServiceAgent

logger = logging.getLogger("domain.manufacturing.services")


class WorkOrderDispatchService(DomainServiceAgent):
    """Dispatches work orders to workers: validates WO → assigns → confirms equipment."""

    def __init__(self) -> None:
        super().__init__(name="work_order_dispatch_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        wo_id = perception.get("operation", {}).get("wo_id")
        worker = perception.get("operation", {}).get("worker")
        return {
            "service": self.name,
            "action": "dispatch",
            "wo_id": wo_id,
            "worker": worker,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "dispatched",
            "wo_id": decision.get("wo_id"),
            "worker": decision.get("worker"),
            "result": "in_progress",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class BatchReleaseService(DomainServiceAgent):
    """Batch release: QA review → yield check → release or hold decision."""

    def __init__(self) -> None:
        super().__init__(name="batch_release_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        batch_id = perception.get("operation", {}).get("batch_id")
        return {
            "service": self.name,
            "action": "evaluate_release",
            "batch_id": batch_id,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "release_evaluated",
            "batch_id": decision.get("batch_id"),
            "result": "pending_qa",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class ChangeControlReviewService(DomainServiceAgent):
    """Reviews change requests across QA, engineering, and production."""

    def __init__(self) -> None:
        super().__init__(name="change_control_review_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        change_id = perception.get("operation", {}).get("change_id")
        return {"service": self.name, "action": "review_change", "change_id": change_id}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "change_reviewed",
            "change_id": decision.get("change_id"),
            "result": "under_review",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)
