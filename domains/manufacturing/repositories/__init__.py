"""Manufacturing Repository Agents — persistence for manufacturing domain entities."""

from __future__ import annotations
import logging
from typing import Any
from domains.package import RepositoryAgent

logger = logging.getLogger("domain.manufacturing.repositories")


class WorkOrderRepositoryAgent(RepositoryAgent):
    """Manages work order persistence — wraps CMMS/ERP API or local store."""

    def __init__(self) -> None:
        super().__init__(name="work_order_repository")

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        if decision.get("action") == "cache_hit":
            return {"repository": self.name, "action": "cache_hit", "source": "local"}
        return {
            "repository": self.name,
            "action": decision.get("action", "query"),
            "query_count": self._query_count,
        }


class BatchRepositoryAgent(RepositoryAgent):
    """Manages batch record persistence — wraps MES or local store."""

    def __init__(self) -> None:
        super().__init__(name="batch_repository")

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        return {
            "repository": self.name,
            "action": decision.get("action", "query"),
            "query_count": self._query_count,
        }


class CalibrationRepositoryAgent(RepositoryAgent):
    """Manages calibration records — wraps LIMS or local store."""

    def __init__(self) -> None:
        super().__init__(name="calibration_repository")

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        return {
            "repository": self.name,
            "action": decision.get("action", "query"),
            "query_count": self._query_count,
        }
