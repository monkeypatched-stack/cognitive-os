"""Audit & Monitoring Service - Single Responsibility.

Responsibility: Audit logging, telemetry, governance checks, metrics.
Depends on: audit log, lemon (telemetry), governance
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.audit_service")


class AuditService:
    """Audit logging, governance checks, and telemetry.

    Responsibility: Record audit entries, validate governance rules,
    fire monitoring alerts. Never blocks execution.
    """

    def __init__(self, lemon: Any = None) -> None:
        self._lemon = lemon
        self._tensor: Any = None

    def set_lemon(self, lemon: Any) -> None:
        self._lemon = lemon

    def set_tensor(self, tensor: Any) -> None:
        self._tensor = tensor

    def record(
        self,
        event_type: str,
        action: str,
        actor: str = "",
        outcome: str = "success",
        details: dict | None = None,
    ) -> None:
        """Record an audit entry. Best-effort — never blocks execution."""
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            log = get_audit_log()
            log.record(
                runtime_id="local",
                event_type=event_type,
                action=action,
                actor=actor,
                outcome=outcome,
                details=details or {},
                world_revision=self._tensor.revision if self._tensor else 0,
            )
        except Exception as exc:
            logger.warning("[audit] record failed: %s", exc)

    def governance_check(self, graph: dict) -> dict:
        """Validate execution graph against governance rules.

        Returns {"blocked": bool, "reason": str, "violations": list}.
        """
        try:
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])

            violations = []

            for n in nodes:
                if isinstance(n, dict) and not n.get("agent", ""):
                    violations.append(
                        {
                            "rule": "agent_identity",
                            "severity": "high",
                            "node": n.get("id", ""),
                            "message": "Node has no agent",
                        }
                    )

            connected = set()
            for e in edges:
                if isinstance(e, dict):
                    connected.add(e.get("from", ""))
                    connected.add(e.get("to", ""))
            for n in nodes:
                if isinstance(n, dict) and n.get("id", "") not in connected:
                    violations.append(
                        {
                            "rule": "connectivity",
                            "severity": "medium",
                            "node": n.get("id", ""),
                            "message": "Orphan node",
                        }
                    )

            critical = [v for v in violations if v["severity"] == "high"]
            if self._lemon:
                for v in violations:
                    self._lemon.counter(f"governance.violation.{v['rule']}")

            return {
                "blocked": bool(critical),
                "reason": critical[0]["message"] if critical else "",
                "violations": violations,
            }
        except Exception as exc:
            logger.debug("Governance check skipped: %s", exc)
            return {"blocked": False, "reason": "", "violations": []}

    def alert(self, name: str, message: str, severity: str = "warning") -> None:
        """Fire a monitoring alert."""
        if self._lemon:
            self._lemon.alert(name=name, message=message, severity=severity)

    def counter(self, name: str, **tags: str) -> None:
        """Increment a counter metric."""
        if self._lemon:
            self._lemon.counter(name, **tags)

    def histogram(self, name: str, value: float, **tags: str) -> None:
        """Record a histogram value."""
        if self._lemon:
            self._lemon.histogram(name, value, **tags)
