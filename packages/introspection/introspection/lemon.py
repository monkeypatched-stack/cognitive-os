"""Lemon — Observability Layer for AgentOS.

Persists all metrics to Elasticsearch for querying and dashboards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from introspection.tracing import Tracer, Trace, Span
from introspection.metrics import MetricsCollector, Metric
from introspection.structured_logging import StructuredLogger, LogEntry
from introspection.health import HealthMonitor, HealthCheck
from introspection.alerting import AlertManager, Alert, AlertRule, AlertSeverity


class Lemon:
    """Observability manager for AgentOS.

    Persists all metrics to Elasticsearch for querying.
    """

    def __init__(self, elasticsearch_url: str = "http://localhost:9200"):
        self.tracer = Tracer()
        self.metrics = MetricsCollector()
        self.logger = StructuredLogger()
        self.health = HealthMonitor()
        self.alerts = AlertManager()
        self._es_url = elasticsearch_url
        self._es_client = None
        self._persist_buffer: list[dict] = []

    async def connect_elasticsearch(self) -> None:
        """Connect to Elasticsearch for persistence."""
        try:
            from elasticsearch import AsyncElasticsearch

            self._es_client = AsyncElasticsearch([self._es_url])
            await self._es_client.ping()
        except Exception:
            self._es_client = None

    async def persist_metrics(self) -> dict[str, Any]:
        """Persist all metrics to Elasticsearch."""
        if not self._es_client:
            return {"status": "no_elasticsearch"}

        export = self.metrics.export()
        timestamp = datetime.now(timezone.utc).isoformat()

        doc = {
            "timestamp": timestamp,
            "counters": export.get("counters", {}),
            "gauges": export.get("gauges", {}),
            "histograms": export.get("histograms", {}),
        }

        try:
            await self._es_client.index(index="agentos-metrics", document=doc)
            return {"status": "persisted", "timestamp": timestamp}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def persist_traces(self) -> dict[str, Any]:
        """Persist recent traces to Elasticsearch."""
        if not self._es_client:
            return {"status": "no_elasticsearch"}

        traces = list(self.tracer._traces.values())[-100:]

        for trace in traces:
            doc = trace.to_dict()
            try:
                await self._es_client.index(index="agentos-traces", document=doc)
            except Exception:
                pass

        return {"status": "persisted", "count": len(traces)}

    async def persist_logs(self, limit: int = 100) -> dict[str, Any]:
        """Persist recent logs to Elasticsearch."""
        if not self._es_client:
            return {"status": "no_elasticsearch"}

        logs = self.logger.get_entries(limit=limit)

        for log in logs:
            doc = log.to_dict()
            try:
                await self._es_client.index(index="agentos-logs", document=doc)
            except Exception:
                pass

        return {"status": "persisted", "count": len(logs)}

    async def persist_all(self) -> dict[str, Any]:
        """Persist all observability data to Elasticsearch."""
        results = {}
        results["metrics"] = await self.persist_metrics()
        results["traces"] = await self.persist_traces()
        results["logs"] = await self.persist_logs()
        return results

    async def query_metrics(self, metric_name: str | None = None, size: int = 100) -> list[dict]:
        """Query metrics from Elasticsearch."""
        if not self._es_client:
            return []

        try:
            if metric_name:
                query = {"query": {"term": {"counters." + metric_name: {"exists": True}}}}
            else:
                query = {"query": {"match_all": {}}, "sort": [{"timestamp": "desc"}]}

            result = await self._es_client.search(index="agentos-metrics", body={"size": size, **query})
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

    async def query_traces(self, trace_id: str | None = None, size: int = 100) -> list[dict]:
        """Query traces from Elasticsearch."""
        if not self._es_client:
            return []

        try:
            if trace_id:
                query = {"query": {"term": {"trace_id": trace_id}}}
            else:
                query = {"query": {"match_all": {}}, "sort": [{"timestamp": "desc"}]}

            result = await self._es_client.search(index="agentos-traces", body={"size": size, **query})
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

    async def query_logs(
        self, severity: str | None = None, component: str | None = None, size: int = 100
    ) -> list[dict]:
        """Query logs from Elasticsearch."""
        if not self._es_client:
            return []

        try:
            must = []
            if severity:
                must.append({"term": {"severity": severity}})
            if component:
                must.append({"term": {"component": component}})

            query = {"query": {"bool": {"must": must or [{"match_all": {}}]}}}
            result = await self._es_client.search(
                index="agentos-logs",
                body={"size": size, "sort": [{"timestamp": "desc"}], **query},
            )
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

    async def get_dashboard(self) -> dict[str, Any]:
        """Get dashboard data for all metrics."""
        metrics = await self.query_metrics(size=1)
        traces = await self.query_traces(size=10)
        logs = await self.query_logs(size=10)

        return {
            "latest_metrics": metrics[0] if metrics else {},
            "recent_traces": len(traces),
            "recent_logs": len(logs),
            "health": self.health.summary(),
            "alerts": [a.to_dict() for a in self.alerts.get_active()],
        }

    # --- Tracing ---
    def start_trace(self, name: str = "", **metadata: Any) -> Trace:
        return self.tracer.start_trace(name, **metadata)

    def start_span(self, name: str, component: str = "", **attributes: Any) -> Span:
        return self.tracer.start_span(name, component, **attributes)

    def finish_span(self, status: str = "ok") -> None:
        self.tracer.finish_span(status)

    def finish_trace(self) -> None:
        self.tracer.finish_trace()

    # --- Metrics ---
    def record_metric(self, name: str, value: float, unit: str = "", **tags: str) -> Metric:
        return self.metrics.record(name, value, unit, **tags)

    def counter(self, name: str, increment: int = 1, **tags: str) -> None:
        self.metrics.counter(name, increment, **tags)

    def gauge(self, name: str, value: float, **tags: str) -> None:
        self.metrics.gauge(name, value, **tags)

    def histogram(self, name: str, value: float, **tags: str) -> None:
        self.metrics.histogram(name, value, **tags)

    # --- Logging ---
    def log(self, severity: str, message: str, component: str = "", **kwargs: Any) -> LogEntry:
        trace = self.tracer.get_current_trace()
        if trace:
            kwargs["trace_id"] = trace.trace_id
        return self.logger.log(severity, message, component=component, **kwargs)

    def info(self, message: str, component: str = "", **kwargs: Any) -> LogEntry:
        return self.logger.info(message, component=component, **kwargs)

    def warn(self, message: str, component: str = "", **kwargs: Any) -> LogEntry:
        return self.logger.warn(message, component=component, **kwargs)

    def error(self, message: str, component: str = "", **kwargs: Any) -> LogEntry:
        return self.logger.error(message, component=component, **kwargs)

    # --- Health ---
    def health_check(self, name: str, status: str = "healthy", **metadata: Any) -> HealthCheck:
        return self.health.check(name, status, **metadata)

    def overall_health(self) -> str:
        return self.health.overall_status()

    # --- Alerts ---
    def alert(
        self,
        name: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        **metadata: Any,
    ) -> Alert:
        return self.alerts.fire(name, message, severity, **metadata)

    def add_alert_rule(self, rule: AlertRule) -> None:
        self.alerts.add_rule(rule)

    # --- Summary ---
    def summary(self) -> dict[str, Any]:
        return {
            "tracing": self.tracer.summary(),
            "metrics": self.metrics.summary(),
            "logging": self.logger.summary(),
            "health": self.health.summary(),
            "alerts": self.alerts.summary(),
            "elasticsearch_connected": self._es_client is not None,
        }

    def export(self) -> dict[str, Any]:
        """Export all observability data."""
        return {
            "summary": self.summary(),
            "metrics": self.metrics.export(),
            "health": self.health.summary(),
            "active_alerts": [a.to_dict() for a in self.alerts.get_active()],
        }
