"""Software Engineering Domain Services — cross-aggregate operations."""

from __future__ import annotations
import logging
from typing import Any
from domains.package import DomainServiceAgent

logger = logging.getLogger("domain.software_engineering.services")


class CodeReviewService(DomainServiceAgent):
    """Orchestrates code review: PR entity + reviewer entities + approval policy."""

    def __init__(self) -> None:
        super().__init__(name="code_review_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        pr_id = perception.get("operation", {}).get("pr_id")
        return {"service": self.name, "action": "review_pr", "pr_id": pr_id}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "review_initiated",
            "pr_id": decision.get("pr_id"),
            "result": "pending_approval",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class CICDService(DomainServiceAgent):
    """CI/CD orchestration — triggers pipeline, monitors stages, gates deployment."""

    def __init__(self) -> None:
        super().__init__(name="cicd_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "trigger_pipeline",
            "trigger": perception.get("operation", {}).get("trigger", "push"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "pipeline_triggered",
            "trigger": decision.get("trigger"),
            "result": "running",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class ReleaseService(DomainServiceAgent):
    """Release service — coordinates version bump, tag, changelog, deployment."""

    def __init__(self) -> None:
        super().__init__(name="release_service")

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "prepare_release",
            "bump": perception.get("operation", {}).get("version", "patch"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "service": self.name,
            "action": "release_prepared",
            "bump": decision.get("bump"),
            "result": "ready_for_deployment",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)
