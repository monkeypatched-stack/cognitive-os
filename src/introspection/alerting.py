"""Alerting — event-driven alert system.

Generate alerts for:
- Failures
- Latency thresholds
- Memory pressure
- Queue saturation
- Capability failures
- Provider failures
- Database connectivity
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


def _notify_webhook(alert: "Alert") -> None:
    """Gate 11 (production readiness): AlertManager could fire and track
    alerts, but nothing ever left the process — confirmed live via
    GET /api/v1/agentos/observability showing real alert activity with
    no way anyone would ever see it outside a dashboard pull. ALERT_WEBHOOK_URL
    is a plain HTTP POST of a Slack-compatible JSON body ({"text": ...}) —
    Slack, Discord (with its /slack-compatible endpoint suffix), and most
    generic incident webhooks all accept this shape directly; PagerDuty
    needs its own Events API v2 shape and isn't covered by this, since
    that requires a routing key this deployment doesn't have configured
    anywhere. Unset (the default) makes this a silent no-op, same as
    every other optional-integration env var in this codebase
    (KEYSTORE_SECRET, OLLAMA_BASE_URL, ...). Uses urllib (stdlib) rather
    than httpx/requests since AlertManager has no async call path and no
    HTTP client dependency of its own to reuse — a short, best-effort,
    fire-and-forget POST with a tight timeout so a slow or unreachable
    webhook endpoint never blocks the tick/request path that triggered
    the alert.
    """
    url = os.getenv("ALERT_WEBHOOK_URL", "")
    if not url:
        return
    try:
        body = json.dumps(
            {
                "text": f"[{alert.severity.value.upper()}] {alert.name}: {alert.message}",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception as exc:
        logger.warning("Alert webhook delivery failed for %r: %s", alert.name, exc)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"
    SILENCED = "silenced"


@dataclass
class Alert:
    """An alert instance."""

    alert_id: str = field(default_factory=lambda: f"alert-{uuid4().hex[:8]}")
    name: str = ""
    severity: AlertSeverity = AlertSeverity.WARNING
    status: AlertStatus = AlertStatus.FIRING
    message: str = ""
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "name": self.name,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "source": self.source,
            "timestamp": self.timestamp,
        }


class AlertRule:
    """A rule that triggers alerts."""

    def __init__(
        self,
        name: str,
        condition: Callable[[dict], bool],
        severity: AlertSeverity = AlertSeverity.WARNING,
        message_template: str = "",
    ):
        self.name = name
        self.condition = condition
        self.severity = severity
        self.message_template = message_template

    def evaluate(self, context: dict) -> Alert | None:
        try:
            if self.condition(context):
                return Alert(
                    name=self.name,
                    severity=self.severity,
                    message=self.message_template.format(**context),
                )
        except Exception as e:
            logger.debug("Alert rule %r failed to evaluate: %s", self.name, e)
        return None


class AlertManager:
    """Alert management system.

    Responsibilities:
    - Evaluate alert rules
    - Fire alerts
    - Track alert history
    - Support alert suppression
    """

    def __init__(self):
        self._rules: list[AlertRule] = []
        self._alerts: list[Alert] = []
        self._active: dict[str, Alert] = {}

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def evaluate(self, context: dict) -> list[Alert]:
        """Evaluate all rules against context."""
        new_alerts = []
        for rule in self._rules:
            alert = rule.evaluate(context)
            if alert:
                self._alerts.append(alert)
                self._active[alert.alert_id] = alert
                new_alerts.append(alert)
                _notify_webhook(alert)
        return new_alerts

    def fire(
        self,
        name: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        **metadata: Any,
    ) -> Alert:
        """Manually fire an alert."""
        alert = Alert(name=name, severity=severity, message=message, metadata=metadata)
        self._alerts.append(alert)
        self._active[alert.alert_id] = alert
        _notify_webhook(alert)
        return alert

    def resolve(self, alert_id: str) -> bool:
        """Resolve an active alert."""
        if alert_id in self._active:
            self._active[alert_id].status = AlertStatus.RESOLVED
            del self._active[alert_id]
            return True
        return False

    def get_active(self) -> list[Alert]:
        return list(self._active.values())

    def get_history(self, limit: int = 50) -> list[Alert]:
        return self._alerts[-limit:]

    def summary(self) -> dict:
        return {
            "rules": len(self._rules),
            "active_alerts": len(self._active),
            "total_alerts": len(self._alerts),
        }
