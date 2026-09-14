"""CCB-500 — batch recall traceability and household response."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.domains.commerce import DomainCapability
from src.monkey_brain.kernel.integrations import KnowledgeGraphRecallIntegration


@dataclass(frozen=True)
class RecallNotification:
    notification_id: str = field(default_factory=lambda: uuid4().hex)
    recipient_id: str = ""
    batch_id: str = ""
    message: str = ""
    created_at: float = field(default_factory=time.time)


def recall_batch(
    kg: Any, batch_id: str, reason: str = "supplier recall"
) -> dict[str, Any]:
    """Compatibility wrapper; execution is delegated to an integration."""
    result = KnowledgeGraphRecallIntegration(kg).execute(batch_id, reason)
    result["notifications"] = tuple(
        RecallNotification(**notification) for notification in result["notifications"]
    )
    return result


class RecallCapability(DomainCapability):
    """Declarative capability surface for planner-approved recalls."""

    name = "recall"

    def __init__(self, integration: Any = None):
        self._integration = integration
        super().__init__({"execute": self._execute})

    def _execute(
        self,
        *,
        context: dict | None = None,
        batch_id: str = "",
        reason: str = "supplier recall",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        integration = self._integration
        if integration is None:
            kg = (context or {}).get("knowledge_graph")
            if kg is None:
                return {
                    "success": False,
                    "error": "no recall integration or knowledge graph available",
                }
            integration = KnowledgeGraphRecallIntegration(kg)
        return integration.execute(batch_id, reason)

    def handle(self, args: dict) -> dict[str, Any]:
        parameters = args.get("parameters", {}) or {}
        return self._execute(context=args.get("context"), **parameters)
