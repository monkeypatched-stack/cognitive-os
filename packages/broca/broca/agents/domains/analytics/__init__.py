"""Analytics agents — Dashboard, KPI, Forecast, Reporting, Alerting."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.analytics")


class DashboardAgent(BaseDDDAgent):
    agent_type = "dashboard"
    description = "Creates and manages analytics dashboards"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "dashboard_id": context.get("dashboard_id", ""),
            "metrics": context.get("metrics", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"dashboard.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"dashboard.{decision['Operation']}", "success": True}


class KPIAgent(BaseDDDAgent):
    agent_type = "kpi"
    description = "Defines, tracks, and reports on Key Performance Indicators"
    ddd_layer = "value_object"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "kpi_id": context.get("kpi_id", ""),
            "operation": context.get("operation", "evaluate"),
            "value": context.get("value", 0),
            "target": context.get("target", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        value = perception.get("value", 0)
        target = perception.get("target", 1)
        met = value >= target
        return {
            "operation": perception["operation"],
            "action": f"kpi.{perception['operation']}",
            "met": met,
            "progress": value / max(target, 1),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"kpi.{decision['Operation']}",
            "met": decision.get("met", False),
            "progress": decision.get("progress", 0),
        }


class ForecastAgent(BaseDDDAgent):
    agent_type = "forecast"
    description = "Generates forecasts using statistical and ML models"
    ddd_layer = "value_object"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric": context.get("metric", ""),
            "historical_data": context.get("historical_data", []),
            "horizon": context.get("horizon", 30),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "forecast.predict",
            "metric": perception.get("metric", ""),
            "predictions": [],
            "confidence": 0.8,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "forecast.predict",
            "success": True,
            "predictions": decision.get("predictions", []),
            "confidence": decision.get("confidence", 0),
        }


class ReportingAgent(BaseDDDAgent):
    agent_type = "reporting"
    description = "Generates reports from data sources"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "generate"),
            "report_type": context.get("report_type", "summary"),
            "data_source": context.get("data_source", ""),
            "format": context.get("format", "pdf"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"reporting.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"reporting.{decision['Operation']}",
            "success": True,
            "report_id": f"rpt-{decision.get('report_type', 'summary')[:8]}",
        }


class AlertingAgent(BaseDDDAgent):
    agent_type = "alerting"
    description = "Monitors metrics and triggers alerts based on rules"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "metric": context.get("metric", ""),
            "value": context.get("value", 0),
            "threshold": context.get("threshold", 0),
            "operator": context.get("operator", "gt"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        value = perception.get("value", 0)
        threshold = perception.get("threshold", 0)
        op = perception.get("operator", "gt")
        triggered = (
            (value > threshold)
            if op == "gt"
            else (
                (value < threshold)
                if op == "lt"
                else ((value >= threshold) if op == "gte" else (value <= threshold) if op == "lte" else False)
            )
        )
        return {
            "action": "alerting.evaluate",
            "triggered": triggered,
            "value": value,
            "threshold": threshold,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "alerting.evaluate",
            "triggered": decision.get("triggered", False),
            "alert_id": (f"alert-{decision.get('metric', '')[:8]}" if decision.get("triggered") else None),
        }
