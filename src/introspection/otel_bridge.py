"""OpenTelemetry bridge — exports Lemon semantic spans via OTLP.

When opentelemetry-sdk is installed, this module bridges Lemon's internal
event store to the OTel ecosystem:
  - Every semantic span becomes an OTel Span with cognitive attributes
  - Metrics are exported as OTel Gauge/Counter instruments
  - OTLP exporter sends to any OTel Collector (Grafana, Tempo, Jaeger, DataDog)

When OTel is not installed or OTEL_EXPORTER_OTLP_ENDPOINT is not set,
every method is a no-op — callers never need to guard against import errors.

Environment variables:
  OTEL_EXPORTER_OTLP_ENDPOINT   — http://localhost:4318  (OTLP/HTTP)
  OTEL_SERVICE_NAME             — monkeybrain (default)
  OTEL_RESOURCE_ATTRIBUTES      — extra resource attributes (k=v,k=v)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger("introspection.otel")


class TokenBlobStore:
    """In-process store for heavy OTel reasoning artifacts.

    OTel span attributes are capped at ~256 bytes per value. Pushing a raw
    perception tree or violation list through span.set_attribute causes silent
    truncation or export failures. Instead: serialise the artifact here, key it
    by a 16-char sha256 prefix, and pass only the key ('blob_pointer') through
    the span. The full artifact is retrievable via TokenBlobStore.get(key).
    """

    _store: dict[str, Any] = {}

    @classmethod
    def save(cls, artifact: Any) -> str:
        raw = json.dumps(artifact, default=str, sort_keys=True).encode()
        key = hashlib.sha256(raw).hexdigest()[:16]
        cls._store[key] = artifact
        return key

    @classmethod
    def get(cls, key: str) -> Any | None:
        return cls._store.get(key)

    @classmethod
    def size(cls) -> int:
        return len(cls._store)

    @classmethod
    def clear(cls) -> None:
        cls._store.clear()


_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
_SERVICE = os.getenv("OTEL_SERVICE_NAME", "monkeybrain")
_ENABLED = bool(_ENDPOINT)


class _NoOpSpan:
    """Returned when OTel is not available — silent no-op."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any, description: str = "") -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class OTelBridge:
    """OpenTelemetry bridge for Lemon semantic events.

    Obtain the singleton via get_bridge().
    """

    def __init__(self) -> None:
        self._tracer = None
        self._meter = None
        self._available = False
        if _ENABLED:
            self._setup()

    def _setup(self) -> None:
        try:
            from opentelemetry import trace, metrics
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create(
                {
                    "service.name": _SERVICE,
                    "service.namespace": "cognitive-os",
                }
            )

            # Traces
            tp = TracerProvider(resource=resource)
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_ENDPOINT)))
            trace.set_tracer_provider(tp)
            self._tracer = trace.get_tracer("lemon")

            # Metrics
            reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=_ENDPOINT), export_interval_millis=15000)
            mp = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(mp)
            self._meter = metrics.get_meter("lemon")

            self._available = True
            logger.info("OTel bridge active: endpoint=%s service=%s", _ENDPOINT, _SERVICE)
        except ImportError:
            logger.debug("opentelemetry-sdk not installed — OTel bridge is no-op")
        except Exception as exc:
            logger.warning("OTel bridge setup failed: %s", exc)

    # ------------------------------------------------------------------
    # Span helpers
    # ------------------------------------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        layer: str = "",
        **attributes: Any,
    ) -> Generator[Any, None, None]:
        """Context manager yielding an OTel span (or silent no-op)."""
        if not self._available or self._tracer is None:
            yield _NoOpSpan()
            return

        with self._tracer.start_as_current_span(name) as otel_span:
            otel_span.set_attribute("cognitive.layer", layer)
            otel_span.set_attribute("service.name", _SERVICE)
            for k, v in attributes.items():
                otel_span.set_attribute(k, str(v) if not isinstance(v, (bool, int, float, str)) else v)
            try:
                yield otel_span
            except Exception as exc:
                otel_span.record_exception(exc)
                from opentelemetry.trace import StatusCode

                otel_span.set_status(StatusCode.ERROR, str(exc))
                raise

    def emit_span(
        self,
        name: str,
        layer: str,
        duration_ms: float,
        status: str = "ok",
        **attributes: Any,
    ) -> None:
        """Emit a completed span from a pre-recorded Lemon event (non-context-manager form)."""
        if not self._available or self._tracer is None:
            return
        try:
            from opentelemetry.trace import SpanKind, StatusCode

            with self._tracer.start_as_current_span(
                name,
                kind=SpanKind.INTERNAL,
                end_on_exit=False,
            ) as otel_span:
                otel_span.set_attribute("cognitive.layer", layer)
                otel_span.set_attribute("cognitive.duration_ms", duration_ms)
                for k, v in attributes.items():
                    otel_span.set_attribute(k, str(v) if not isinstance(v, (bool, int, float, str)) else v)
                if status != "ok":
                    otel_span.set_status(StatusCode.ERROR, status)
                otel_span.end()
        except Exception as exc:
            logger.debug("OTel emit_span failed: %s", exc)

    # ------------------------------------------------------------------
    # Metric instruments
    # ------------------------------------------------------------------

    def emit_counter(self, name: str, value: int = 1, **labels: str) -> None:
        if not self._available or self._meter is None:
            return
        try:
            counter = self._meter.create_counter(name.replace(".", "_"))
            counter.add(value, dict(labels))
        except Exception as e:
            logger.debug("OTel counter emit failed: %s", e)

    def emit_histogram(self, name: str, value: float, **labels: str) -> None:
        if not self._available or self._meter is None:
            return
        try:
            hist = self._meter.create_histogram(name.replace(".", "_"), unit="ms")
            hist.record(value, dict(labels))
        except Exception as e:
            logger.debug("OTel histogram emit failed: %s", e)

    def emit_gauge(self, name: str, value: float, **labels: str) -> None:
        if not self._available or self._meter is None:
            return
        try:
            gauge = self._meter.create_gauge(name.replace(".", "_"))
            gauge.set(value, dict(labels))
        except Exception as e:
            logger.debug("OTel gauge emit failed: %s", e)

    # ------------------------------------------------------------------
    # Semantic event converters — called from Lemon.observe_*
    # ------------------------------------------------------------------

    def emit_intent(self, intent_text: str, confidence: float, goal_id: str, trace_id: str) -> None:
        self.emit_span(
            "cognitive.intent",
            layer="intent",
            duration_ms=0.0,
            intent=intent_text[:100],
            confidence=confidence,
            goal_id=goal_id,
            trace_id=trace_id,
        )

    def emit_agent_reasoning(
        self,
        agent_type: str,
        latency_ms: float,
        violations: list,
        pipeline_id: str,
        perception_tree: Any = None,
    ) -> None:
        """Emit agent reasoning span with flat scalar attributes only.

        Heavy artifacts (perception trees, full violation lists) go to
        TokenBlobStore; the span carries only the hash pointer.  This prevents
        OTel attribute-size explosions and silent truncation in exporters.
        """
        blob_pointer = ""
        if perception_tree is not None:
            blob_pointer = TokenBlobStore.save(perception_tree)
        elif violations:
            blob_pointer = TokenBlobStore.save({"violations": violations})

        self.emit_span(
            f"agent.{agent_type}.reason",
            layer="agent",
            duration_ms=latency_ms,
            agent_type=agent_type,
            violations_count=len(violations),  # scalar: OTel-safe
            pipeline_id=pipeline_id,
            blob_pointer=blob_pointer,  # hash ref, not raw data
        )

    def emit_pipeline_step(
        self,
        pipeline_id: str,
        step: str,
        capability: str,
        latency_ms: float,
        status: str,
    ) -> None:
        self.emit_span(
            f"pipeline.step.{step}",
            layer="pipeline",
            duration_ms=latency_ms,
            pipeline_id=pipeline_id,
            capability=capability,
            status=status,
        )
        self.emit_histogram("pipeline.step.latency_ms", latency_ms, capability=capability)

    def emit_governance(self, policy_path: str, decision: bool, trust_score: float, pipeline_id: str) -> None:
        self.emit_span(
            f"governance.{policy_path.replace('/', '.')}",
            layer="governance",
            duration_ms=0.0,
            policy_path=policy_path,
            decision="allow" if decision else "deny",
            trust_score=trust_score,
            pipeline_id=pipeline_id,
        )
        self.emit_counter(
            "governance.decision",
            outcome="allow" if decision else "deny",
        )

    def emit_learning(self, capability: str, td_error: float, reward: float, exploration: bool) -> None:
        self.emit_gauge("learning.td_error", abs(td_error), capability=capability)
        self.emit_histogram("learning.reward", reward, capability=capability)
        self.emit_counter("learning.exploration" if exploration else "learning.exploitation")

    @property
    def available(self) -> bool:
        return self._available


_bridge: OTelBridge | None = None


def get_bridge() -> OTelBridge:
    global _bridge
    if _bridge is None:
        _bridge = OTelBridge()
    return _bridge
