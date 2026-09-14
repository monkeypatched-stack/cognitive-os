"""Lemon — Observability Layer for AgentOS.

Lemon is the cognitive observability hub. It owns:
  - Classical telemetry: metrics (counters/gauges/histograms), logs, distributed traces
  - Semantic event store: typed cognitive-layer events per the chapter structure
      Intent → Goal → Pipeline → Capability → Provider
      Agent  → Reasoning → Reflection → Memory
      Pipeline Observability: execution graphs, latency, bottlenecks, failure propagation
      World Model: prediction vs actual, drift
      Governance: policy decisions, trust, provenance, audit trail
      Learning: Q-value convergence, exploration ratio, reward trends
  - OpenTelemetry bridge: exports all of the above via OTLP to any OTel Collector
  - Cognitive dashboards: structured JSON views of each layer
  - Elasticsearch persistence (optional)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.introspection.alerting import Alert, AlertManager, AlertRule, AlertSeverity
from src.introspection.analytical_triggers import AnalyticalTriggerEngine
from src.introspection.health import HealthCheck, HealthMonitor
from src.introspection.logging import LogEntry, StructuredLogger
from src.introspection.metrics import Metric, MetricsCollector
from src.introspection.otel_bridge import get_bridge
from src.introspection.semantic_trace import (
    AgentReasonEvent,
    AgentReflectEvent,
    GoalEvent,
    GovernanceEvent,
    IntentEvent,
    LearningEvent,
    MemoryAccessEvent,
    PipelineStepEvent,
    SemanticEventStore,
    WorldModelEvent,
)
from src.introspection.tracing import Span, Trace, Tracer

logger = logging.getLogger(__name__)

_lemon_instance: Lemon | None = None


def set_lemon(lemon: Lemon) -> None:
    global _lemon_instance
    _lemon_instance = lemon


def get_lemon() -> Lemon | None:
    return _lemon_instance


class Lemon:
    """Cognitive observability manager for AgentOS.

    Classical API (unchanged):
      counter(), gauge(), histogram(), info(), warn(), error()
      start_trace(), start_span(), finish_span(), finish_trace()
      health_check(), overall_health()
      alert(), add_alert_rule()
      summary(), export()
      on_outcome()          — OutcomeSubscriber

    Semantic API (new):
      observe_intent()      — raw text → classified intent + confidence
      observe_goal()        — goal lifecycle events
      observe_pipeline_step()— per-step pipeline telemetry + graph
      observe_agent_reasoning()— perceive→reason→act cycle
      observe_agent_reflection()— reflection / self-correction event
      observe_memory_access()— short-term / long-term / episodic access
      observe_world_model() — prediction vs actual, drift
      observe_governance()  — policy decision, trust, provenance, audit
      observe_learning()    — Q-value update, convergence, exploration

    Dashboard API:
      dashboard("intent"|"agent"|"pipeline"|"world_model"|"governance"|"learning"|"all")
    """

    def __init__(self, elasticsearch_url: str = "http://localhost:9200"):
        self.tracer = Tracer()
        self.metrics = MetricsCollector()
        self.logger = StructuredLogger()
        self.health = HealthMonitor()
        self.alerts = AlertManager()
        self._register_default_alert_rules()
        self.semantic = SemanticEventStore()
        self.otel = get_bridge()
        self.triggers = AnalyticalTriggerEngine()
        self._es_url = elasticsearch_url
        self._es_client = None
        self._persist_buffer: list[dict] = []
        # Lightweight rate-limit counters for trigger evaluation in hot paths
        self._trigger_step_count: int = 0
        self._trigger_world_count: int = 0
        self._trigger_learn_count: int = 0

    def _register_default_alert_rules(self) -> None:
        """Gate 11 (production readiness): AlertManager existed and was
        wired into Lemon, but nothing had ever registered a rule with it —
        confirmed live, GET /api/v1/agentos/observability showed
        "alerts": {"rules": 0}. The mechanism worked; it was simply
        unconfigured. These three are the ones this build's own findings
        actually motivate — not a generic starter set:
          - planetary tick duration, thresholded off the real per-actor
            SLA this session established (docs/adr/016-performance-gate9.md)
          - the same metric at a threshold close to the 300s auto-tick
            interval, i.e. the tick-pile-up risk that ADR-016/018 flagged
            and that was live-observed this session ("Previous planetary
            tick still running, skipping this cycle")
          - dependency health, since /health can report a real subsystem
            outage that nothing currently pages anyone about
        """
        self.alerts.add_rule(
            AlertRule(
                name="planetary_tick_slow",
                condition=lambda ctx: ctx.get("duration_ms", 0) > 60_000,
                severity=AlertSeverity.WARNING,
                message_template="Planetary cycle took {duration_ms:.0f}ms (>60s) for {actors_observed} actors",
            )
        )
        self.alerts.add_rule(
            AlertRule(
                name="planetary_tick_pileup_risk",
                condition=lambda ctx: ctx.get("duration_ms", 0) > 180_000,
                severity=AlertSeverity.CRITICAL,
                message_template=(
                    "Planetary cycle took {duration_ms:.0f}ms — over 60% of the "
                    "300s auto-tick interval; cycles will start piling up and "
                    "getting skipped at this actor count/latency"
                ),
            )
        )
        self.alerts.add_rule(
            AlertRule(
                name="dependency_unhealthy",
                condition=lambda ctx: ctx.get("health") not in (None, "healthy"),
                severity=AlertSeverity.CRITICAL,
                message_template="Overall health is {health} (checks: {checks})",
            )
        )

    async def connect_elasticsearch(self) -> None:
        """Connect to Elasticsearch for persistence."""
        try:
            from elasticsearch import AsyncElasticsearch

            self._es_client = AsyncElasticsearch([self._es_url])
            await self._es_client.ping()
        except Exception as e:
            logger.debug("Elasticsearch connection failed: %s", e)
            self._es_client = None

    async def persist_metrics(self) -> dict[str, Any]:
        """Persist all metrics to Elasticsearch."""
        if not self._es_client:
            return {"status": "no_elasticsearch"}

        export = self.metrics.export()
        timestamp = datetime.now(UTC).isoformat()

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
            except Exception as e:
                logger.debug("ES trace persist failed: %s", e)

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
            except Exception as e:
                logger.debug("ES log persist failed: %s", e)

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
        except Exception as e:
            logger.debug("ES metrics query failed: %s", e)
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
        except Exception as e:
            logger.debug("ES traces query failed: %s", e)
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
        except Exception as e:
            logger.debug("ES logs query failed: %s", e)
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
    def start_trace(self, name: str = "", trace_id: str | None = None, **metadata: Any) -> Trace:
        """`trace_id`: pass ExecutionContext.trace_id (or any external run
        identifier) here so Lemon's Tracer uses the SAME id instead of
        minting its own — previously these were two independent,
        unsynchronized trace_id spaces (the external one only ever showed
        up as free-form metadata, e.g. run_id=..., never as the trace's
        actual identity).
        """
        return self.tracer.start_trace(name, trace_id=trace_id, **metadata)

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
            "semantic": self.semantic.summary(),
            "triggers": self.triggers.summary(),
            "otel": {"available": self.otel.available},
            "elasticsearch_connected": self._es_client is not None,
        }

    def export(self) -> dict[str, Any]:
        """Export all observability data including cognitive dashboard."""
        recent_traces = [t.to_dict() for t in list(self.tracer._traces.values())[-10:]]
        recent_logs = [e.to_dict() for e in self.logger.get_entries(limit=50)]
        counters = dict(self.metrics._counters)
        gauges = dict(self.metrics._gauges)
        # See MetricsCollector.export()'s own docstring for why this
        # computes stats directly from self.metrics._histograms[k]'s
        # values rather than re-deriving a lookup key from k — the
        # previous split(":")[0]-then-relookup approach silently
        # reported {"count": 0} for every tagged histogram.
        histograms = {k: self.metrics._stats_for_values(v) for k, v in self.metrics._histograms.items()}
        return {
            "summary": self.summary(),
            "metrics": {
                "counters": counters,
                "gauges": gauges,
                "histograms": histograms,
                "total_defined": 138,
                "active_count": len(counters) + len(gauges) + len(histograms),
            },
            "health": self.health.summary(),
            "active_alerts": [a.to_dict() for a in self.alerts.get_active()],
            "dashboard": self.dashboard("all"),
            "traces": recent_traces,
            "logs": recent_logs,
        }

    # =========================================================================
    # Semantic observability — cognitive layer hooks
    # =========================================================================

    # --- Intent / Goal ---

    def observe_intent(
        self,
        raw_text: str,
        classified_intent: str,
        confidence: float,
        goal_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record a language → intent classification event."""
        event = IntentEvent(
            raw_text=raw_text,
            classified_intent=classified_intent,
            confidence=confidence,
            goal_id=goal_id,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.intents.append(event)
        self.counter("intent.classified", intent=classified_intent)
        self.histogram("intent.confidence", confidence)
        self.otel.emit_intent(classified_intent, confidence, goal_id, event.trace_id)

    def observe_goal(
        self,
        goal_id: str,
        goal_text: str,
        status: str,
        parent_goal_id: str = "",
        pipeline_id: str = "",
        latency_ms: float = 0.0,
        trace_id: str = "",
    ) -> None:
        """Record a goal lifecycle event (formed, routed, executing, completed, failed)."""
        event = GoalEvent(
            goal_id=goal_id,
            goal_text=goal_text,
            status=status,
            parent_goal_id=parent_goal_id,
            pipeline_id=pipeline_id,
            latency_ms=latency_ms,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.goals.append(event)
        self.counter(f"goal.{status}")
        if latency_ms:
            self.histogram("goal.latency_ms", latency_ms)

    # --- Pipeline ---

    def observe_pipeline_step(
        self,
        pipeline_id: str,
        step_name: str,
        capability: str,
        status: str = "ok",
        latency_ms: float = 0.0,
        agent_type: str = "",
        tokens_used: int = 0,
        error: str = "",
        trace_id: str = "",
    ) -> None:
        """Record a single pipeline step execution and update the execution graph."""
        event = PipelineStepEvent(
            pipeline_id=pipeline_id,
            step_name=step_name,
            capability=capability,
            agent_type=agent_type,
            status=status,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            error=error,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.steps.append(event)
        graph = self.semantic.graph_for(pipeline_id)
        graph.add_step(step_name, capability, status, latency_ms, tokens_used)
        self.counter(f"pipeline.step.{status}", capability=capability)
        self.histogram("pipeline.step.latency_ms", latency_ms, capability=capability)
        if tokens_used:
            self.counter("pipeline.tokens_used", increment=tokens_used)
        self.otel.emit_pipeline_step(pipeline_id, step_name, capability, latency_ms, status)
        # Shadow Capability check — evaluate every 5 pipeline steps
        self._trigger_step_count += 1
        if self._trigger_step_count % 5 == 0:
            self._run_triggers_and_alert()

    # --- Agent ---

    def observe_agent_reasoning(
        self,
        agent_type: str,
        perception: dict,
        decision: dict,
        latency_ms: float,
        goal_id: str = "",
        pipeline_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record a full perceive→reason→act cycle for an agent."""
        event = AgentReasonEvent(
            agent_type=agent_type,
            perception=perception,
            decision=decision,
            latency_ms=latency_ms,
            goal_id=goal_id,
            pipeline_id=pipeline_id,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.reasoning.append(event)
        self.counter("agent.reasoning_cycles", agent=agent_type)
        self.histogram("agent.reasoning_latency_ms", latency_ms, agent=agent_type)
        violations = decision.get("violations", [])
        if violations:
            self.counter("agent.violations", increment=len(violations), agent=agent_type)
        self.otel.emit_agent_reasoning(agent_type, latency_ms, violations, pipeline_id)

    def observe_agent_reflection(
        self,
        agent_type: str,
        reflection: str,
        triggered_by: str,
        goal_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record an agent self-reflection or correction event."""
        event = AgentReflectEvent(
            agent_type=agent_type,
            reflection=reflection,
            triggered_by=triggered_by,
            goal_id=goal_id,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.reflections.append(event)
        self.counter("agent.reflections", agent=agent_type, trigger=triggered_by)

    def observe_memory_access(
        self,
        agent_type: str,
        memory_type: str,
        operation: str,
        key: str,
        hit: bool = True,
        latency_ms: float = 0.0,
        trace_id: str = "",
    ) -> None:
        """Record a memory read, write, or evict operation."""
        event = MemoryAccessEvent(
            agent_type=agent_type,
            memory_type=memory_type,
            operation=operation,
            key=key,
            hit=hit,
            latency_ms=latency_ms,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.memory.append(event)
        self.counter(f"memory.{operation}", memory_type=memory_type)
        if operation == "read":
            self.counter("memory.hit" if hit else "memory.miss", memory_type=memory_type)

    # --- World Model ---

    def observe_world_model(
        self,
        simulation_id: str,
        entity: str,
        predicted: dict,
        actual: dict,
        accuracy: float,
        drift_score: float,
        trace_id: str = "",
    ) -> None:
        """Record a world model prediction vs actual comparison."""
        event = WorldModelEvent(
            simulation_id=simulation_id,
            entity=entity,
            predicted=predicted,
            actual=actual,
            accuracy=accuracy,
            drift_score=drift_score,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.world.append(event)
        self.histogram("world_model.accuracy", accuracy, entity=entity)
        self.histogram("world_model.drift", drift_score, entity=entity)
        if drift_score > 0.3:
            self.counter("world_model.high_drift", entity=entity)
        # Context Drift check — evaluate on every world model observation
        self._trigger_world_count += 1
        if self._trigger_world_count % 3 == 0:
            self._run_triggers_and_alert(topologies=["context_drift"])

    # --- Governance ---

    def observe_governance(
        self,
        policy_path: str,
        decision: bool,
        principal: str,
        obligations: list[str],
        trust_score: float,
        provenance_chain: list[str],
        audit_ref: str = "",
        pipeline_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record a policy evaluation decision with full provenance."""
        event = GovernanceEvent(
            policy_path=policy_path,
            decision=decision,
            principal=principal,
            obligations=obligations,
            trust_score=trust_score,
            provenance_chain=provenance_chain,
            audit_ref=audit_ref,
            pipeline_id=pipeline_id,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.governance.append(event)
        self.counter(f"governance.{'allow' if decision else 'deny'}", policy=policy_path)
        self.histogram("governance.trust_score", trust_score)
        if obligations:
            self.counter("governance.obligations", increment=len(obligations))
        self.otel.emit_governance(policy_path, decision, trust_score, pipeline_id)

    # --- Learning ---

    def observe_learning(
        self,
        capability: str,
        state_key: str,
        q_before: float,
        q_after: float,
        reward: float,
        exploration: bool = False,
        episode: int = 0,
        trace_id: str = "",
    ) -> None:
        """Record a Bellman Q-value update from the policy learning step."""
        td_error = q_after - q_before
        event = LearningEvent(
            capability=capability,
            state_key=state_key,
            q_before=q_before,
            q_after=q_after,
            reward=reward,
            td_error=td_error,
            exploration=exploration,
            episode=episode,
            trace_id=trace_id or self._current_trace_id(),
        )
        self.semantic.learning.append(event)
        self.histogram("learning.reward", reward, capability=capability)
        self.histogram("learning.td_error", abs(td_error), capability=capability)
        self.counter("learning.exploration" if exploration else "learning.exploitation")
        self.otel.emit_learning(capability, td_error, reward, exploration)
        # Convergence Delay check — evaluate every 10 learning updates
        self._trigger_learn_count += 1
        if self._trigger_learn_count % 10 == 0:
            self._run_triggers_and_alert(topologies=["convergence_delay"])

    # =========================================================================
    # Cognitive dashboards
    # =========================================================================

    def dashboard(self, panel: str = "all") -> dict:
        """Return a structured dashboard view for the requested cognitive layer.

        panel: "intent" | "agent" | "pipeline" | "world_model" | "governance" | "learning" | "all"
        """
        from src.introspection.cognitive_dashboards import (
            agent_dashboard,
            full_dashboard,
            governance_dashboard,
            intent_dashboard,
            learning_dashboard,
            pipeline_dashboard,
            world_model_dashboard,
        )

        _panels = {
            "intent": intent_dashboard,
            "agent": agent_dashboard,
            "pipeline": pipeline_dashboard,
            "world_model": world_model_dashboard,
            "governance": governance_dashboard,
            "learning": learning_dashboard,
        }
        if panel == "all":
            return full_dashboard(self)
        fn = _panels.get(panel)
        if fn is None:
            return {"error": f"unknown panel {panel!r}", "valid": list(_panels)}
        return fn(self)

    # =========================================================================
    # Trigger evaluation
    # =========================================================================

    def evaluate_triggers(self) -> list[dict]:
        """Explicitly evaluate all analytical triggers and return findings.

        Called by dashboard() so every panel snapshot includes fresh trigger state.
        Also usable from health-check endpoints or scheduled jobs.
        """
        findings = self.triggers.evaluate_all(self.semantic)
        for f in findings:
            self._fire_trigger_alert(f)
        return [f.to_dict() for f in findings]

    def _run_triggers_and_alert(self, topologies: list[str] | None = None) -> None:
        """Rate-limited internal trigger evaluation — fires alerts for findings."""
        try:
            findings = self.triggers.evaluate_all(self.semantic)
            for f in findings:
                if topologies and f.topology not in topologies:
                    continue
                self._fire_trigger_alert(f)
        except Exception as exc:
            import logging as _l

            _l.getLogger(__name__).debug("Trigger evaluation error (non-fatal): %s", exc)

    def _fire_trigger_alert(self, finding) -> None:
        """Convert a TriggerFinding into a Lemon alert."""
        try:
            from src.introspection.alerting import AlertSeverity as _AS

            sev_map = {
                "CRITICAL": _AS.CRITICAL,
                "HIGH": _AS.ERROR,
                "MEDIUM": _AS.WARNING,
                "LOW": _AS.INFO,
            }
            sev = sev_map.get(finding.severity, _AS.WARNING)
            self.alert(
                name=f"trigger.{finding.topology}",
                message=finding.description,
                severity=sev,
                topology=finding.topology,
                recommendation=finding.recommendation[:200],
            )
        except Exception as e:
            logger.debug("Cognitive alert failed: %s", e)

    def _current_trace_id(self) -> str:
        trace = self.tracer.get_current_trace()
        return trace.trace_id if trace else ""

    # =========================================================================
    # Execution Graph Observation
    # =========================================================================

    def observe_execution_graph(self, graph: Any) -> None:
        """Record the canonical ExecutionGraph at bootstrap.

        Tracks node/edge counts per type as gauges, and logs the full topology.
        This is the kernel's execution catalog — the single source of truth.
        """
        by_type: dict[str, int] = {}
        for node in graph._nodes.values():
            by_type[node.type] = by_type.get(node.type, 0) + 1

        for node_type, count in by_type.items():
            self.gauge(f"execution_graph.nodes.{node_type}", count)

        self.gauge("execution_graph.nodes.total", graph.node_count)
        self.gauge("execution_graph.edges.total", graph.edge_count)
        self.counter("execution_graph.built")

        self.info(
            f"ExecutionGraph: {graph.node_count} nodes, {graph.edge_count} edges — "
            f"agents={by_type.get('agent', 0)}, capabilities={by_type.get('capability', 0)}, "
            f"providers={by_type.get('provider', 0)}, workloads={by_type.get('workload', 0)}",
            component="execution_graph",
        )

    def observe_graph_change(self, change_type: str, node_type: str = "", node_id: str = "", **tags: str) -> None:
        """Record a runtime change to the ExecutionGraph.

        change_type: "node_added", "edge_added", "registration", "resolution"
        """
        self.counter(f"execution_graph.{change_type}", node_type=node_type, **tags)
        if node_id:
            self.info(
                f"Graph change: {change_type} {node_type}:{node_id}",
                component="execution_graph",
            )

    # =========================================================================
    # OutcomeSubscriber
    # =========================================================================

    async def on_outcome(self, outcome) -> None:
        """OutcomeSubscriber — record typed ExecutionOutcome as Lemon metrics + semantic events."""
        self.counter("outcome.total")
        self.counter("outcome.success" if outcome.result.success else "outcome.failure")
        self.histogram("outcome.reward", outcome.result.reward, capability=outcome.capability_name)
        self.histogram("outcome.latency_ms", outcome.latency_ms, capability=outcome.capability_name)
        if outcome.agent_type:
            self.counter("outcome.agent_step", agent=outcome.agent_type)
        for name, value in outcome.result.metrics.items():
            self.histogram(f"outcome.metric.{name}", value, capability=outcome.capability_name)
        if outcome.result.artifacts:
            self.counter("outcome.artifacts", increment=len(outcome.result.artifacts))

        # Also record as a semantic pipeline step event
        self.observe_pipeline_step(
            pipeline_id=outcome.workflow_id,
            step_name=outcome.capability_name,
            capability=outcome.capability_name,
            status="ok" if outcome.result.success else "error",
            latency_ms=outcome.latency_ms,
            agent_type=outcome.agent_type,
        )
