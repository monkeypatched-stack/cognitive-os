"""
Telemetry Exporters
===================

Pluggable backends for metrics and traces.

Built-in exporters
------------------
PrometheusExporter  – push metrics via prometheus_client (optional dep)
InfluxExporter      – write metrics to InfluxDB via influxdb-client
JaegerExporter      – send traces to Jaeger via opentelemetry (optional dep)
LoggingExporter     – fallback: log everything via stdlib logging

All exporters implement TelemetryExporter.  Pass your chosen exporter
to TelemetryManager at construction time.

Custom exporters
----------------
Subclass TelemetryExporter and implement export_metric / export_trace.
"""

import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric types
# ---------------------------------------------------------------------------


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------


class TelemetryExporter(ABC):
    """Abstract base for telemetry exporters."""

    @abstractmethod
    async def export_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        """Export a single metric data point."""

    @abstractmethod
    async def export_trace(
        self,
        span_name: str,
        duration: float,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        """Export a completed trace span."""


# ---------------------------------------------------------------------------
# Logging exporter (zero-dependency fallback)
# ---------------------------------------------------------------------------


class LoggingExporter(TelemetryExporter):
    """
    Fallback exporter that writes metrics/traces to stdlib logging.

    No external dependencies.  Useful for development and testing.
    """

    async def export_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        logger.debug("METRIC %s=%s type=%s tags=%s", name, value, metric_type.value, tags)

    async def export_trace(
        self,
        span_name: str,
        duration: float,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        logger.debug("TRACE span=%s duration=%.4fs tags=%s", span_name, duration, tags)


# ---------------------------------------------------------------------------
# Prometheus exporter
# ---------------------------------------------------------------------------


class PrometheusExporter(TelemetryExporter):
    """
    Export metrics to Prometheus via the prometheus_client library.

    Requires:  pip install prometheus_client

    Exposes a /metrics HTTP endpoint on the configured port.
    Metrics are registered lazily on first use.
    """

    def __init__(self, port: int = 8000, namespace: str = "adapter") -> None:
        self.port = port
        self.namespace = namespace
        self._gauges: Dict[str, Any] = {}
        self._counters: Dict[str, Any] = {}
        self._histograms: Dict[str, Any] = {}
        self._started = False

    def _ensure_server(self) -> None:
        """Start the Prometheus HTTP server on first use."""
        if self._started:
            return
        try:
            from prometheus_client import start_http_server

            start_http_server(self.port)
            self._started = True
            logger.info("Prometheus metrics server started on :%d", self.port)
        except ImportError:
            logger.warning(
                "prometheus_client not installed; Prometheus export disabled. "
                "Install with: pip install prometheus_client"
            )

    def _get_gauge(self, name: str, labelnames: list) -> Any:
        if name not in self._gauges:
            try:
                from prometheus_client import Gauge

                self._gauges[name] = Gauge(
                    name.replace(".", "_").replace("-", "_"),
                    name,
                    labelnames=labelnames,
                    namespace=self.namespace,
                )
            except ImportError:
                return None
        return self._gauges[name]

    def _get_counter(self, name: str, labelnames: list) -> Any:
        if name not in self._counters:
            try:
                from prometheus_client import Counter

                self._counters[name] = Counter(
                    name.replace(".", "_").replace("-", "_"),
                    name,
                    labelnames=labelnames,
                    namespace=self.namespace,
                )
            except ImportError:
                return None
        return self._counters[name]

    async def export_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        self._ensure_server()
        labelnames = list(tags.keys())
        labelvalues = list(tags.values())

        if metric_type == MetricType.COUNTER:
            metric = self._get_counter(name, labelnames)
            if metric is not None:
                metric.labels(*labelvalues).inc(value)
        else:
            metric = self._get_gauge(name, labelnames)
            if metric is not None:
                metric.labels(*labelvalues).set(value)

    async def export_trace(
        self,
        span_name: str,
        duration: float,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        # Export span duration as a gauge metric
        await self.export_metric(
            name=f"trace_{span_name}_duration_seconds",
            value=duration,
            metric_type=MetricType.GAUGE,
            tags=tags,
            timestamp=timestamp,
        )


# ---------------------------------------------------------------------------
# InfluxDB exporter
# ---------------------------------------------------------------------------


class InfluxExporter(TelemetryExporter):
    """
    Export metrics and traces to InfluxDB v2 via influxdb-client.

    Requires: influxdb-client (already in platform requirements.txt)

    Usage::

        exporter = InfluxExporter(
            url="http://influxdb:8086",
            token="${INFLUX_TOKEN}",
            org="my-org",
            bucket="adapter-metrics",
        )
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        measurement: str = "adapter_metrics",
    ) -> None:
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        self.measurement = measurement
        self._client: Optional[Any] = None
        self._write_api: Optional[Any] = None

    def _ensure_client(self) -> bool:
        """Lazily initialise the InfluxDB client."""
        if self._client is not None:
            return True
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS

            self._client = InfluxDBClient(url=self.url, token=self.token, org=self.org)
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
            return True
        except ImportError:
            logger.warning("influxdb-client not installed; InfluxDB export disabled.")
            return False

    async def export_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        if not self._ensure_client():
            return
        try:
            from influxdb_client import Point, WritePrecision

            point = Point(self.measurement).field(name, value).time(int(timestamp * 1e9), WritePrecision.NANOSECONDS)
            for tag_key, tag_value in tags.items():
                point = point.tag(tag_key, tag_value)
            point = point.tag("metric_type", metric_type.value)

            self._write_api.write(bucket=self.bucket, org=self.org, record=point)
        except Exception as exc:
            logger.warning("InfluxDB write failed: %s", exc)

    async def export_trace(
        self,
        span_name: str,
        duration: float,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        await self.export_metric(
            name=f"trace_{span_name}",
            value=duration,
            metric_type=MetricType.GAUGE,
            tags={**tags, "span": span_name},
            timestamp=timestamp,
        )


# ---------------------------------------------------------------------------
# Jaeger / OpenTelemetry exporter
# ---------------------------------------------------------------------------


class JaegerExporter(TelemetryExporter):
    """
    Export traces to Jaeger via the OpenTelemetry OTLP exporter.

    Jaeger 1.35+ accepts OTLP natively on port 4317 (gRPC) or 4318 (HTTP).
    The legacy opentelemetry-exporter-jaeger thrift transport is removed in
    opentelemetry-sdk >=1.20; use OTLP instead.

    Requires: opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc

    Usage::

        exporter = JaegerExporter(
            service_name="my-adapter",
            endpoint="http://jaeger:4317",   # Jaeger OTLP gRPC port
        )
    """

    def __init__(
        self,
        service_name: str = "monkeypatched-adapter",
        endpoint: str = "http://localhost:4317",
        # Legacy positional compat — ignored, Jaeger now receives OTLP
        agent_host: str = "",
        agent_port: int = 0,
    ) -> None:
        self.service_name = service_name
        self.endpoint = endpoint
        self._tracer: Optional[Any] = None

    def _ensure_tracer(self) -> bool:
        """Lazily initialise the OpenTelemetry tracer."""
        if self._tracer is not None:
            return True
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({SERVICE_NAME: self.service_name}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint)))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer(self.service_name)
            return True
        except ImportError:
            logger.warning(
                "OpenTelemetry OTLP packages not installed; Jaeger export disabled. "
                "Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc"
            )
            return False

    async def export_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        # Jaeger is trace-focused; forward metrics to logging
        logger.debug("METRIC (Jaeger) %s=%s tags=%s", name, value, tags)

    async def export_trace(
        self,
        span_name: str,
        duration: float,
        tags: Dict[str, str],
        timestamp: float,
    ) -> None:
        if not self._ensure_tracer():
            return
        try:
            with self._tracer.start_as_current_span(span_name) as span:
                for key, value in tags.items():
                    span.set_attribute(key, value)
                span.set_attribute("duration_ms", round(duration * 1000, 2))
        except Exception as exc:
            logger.warning("Jaeger trace export failed: %s", exc)
