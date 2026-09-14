"""Cognitive dashboards — structured views of the observability event store.

Each dashboard function takes a Lemon instance and returns a JSON-serialisable
dict suitable for REST API responses, Grafana JSON datasource queries, or
NATS-published telemetry snapshots.

Dashboard panels:
  intent_dashboard(lemon)        — intent classification rates and confidence trends
  agent_dashboard(lemon)         — per-agent reasoning latency, violations, reflections
  pipeline_dashboard(lemon)      — execution graphs, step latency distribution, bottlenecks
  world_model_dashboard(lemon)   — prediction accuracy, drift trend, simulation fidelity
  governance_dashboard(lemon)    — policy decision rates, trust score distribution, provenance depth
  learning_dashboard(lemon)      — Q-value convergence, TD-error trend, exploration ratio
  full_dashboard(lemon)          — all six panels in one snapshot

Usage from an API route:
    from src.introspection.cognitive_dashboards import full_dashboard
    return full_dashboard(app.state.cognitive_runtime.lemon)
"""

from __future__ import annotations


import time as _time
from statistics import mean, median, stdev
from typing import Any, TYPE_CHECKING

_TIME_BUDGET_THRESHOLD = 300.0  # seconds without a real anchor before PROVISIONAL_WARN

if TYPE_CHECKING:
    from src.introspection.lemon import Lemon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_mean(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def _safe_median(values: list[float]) -> float:
    return round(median(values), 4) if values else 0.0


def _safe_stdev(values: list[float]) -> float:
    return round(stdev(values), 4) if len(values) >= 2 else 0.0


def _tail(seq, n: int = 50) -> list:
    items = list(seq)
    return items[-n:] if len(items) > n else items


# ---------------------------------------------------------------------------
# Intent dashboard
# ---------------------------------------------------------------------------


def intent_dashboard(lemon: "Lemon") -> dict[str, Any]:
    events = list(lemon.semantic.intents)
    if not events:
        return {"panel": "intent", "status": "no_data"}

    by_intent: dict[str, int] = {}
    confidences: list[float] = []
    for e in events:
        by_intent[e.classified_intent] = by_intent.get(e.classified_intent, 0) + 1
        confidences.append(e.confidence)

    recent = _tail(events, 10)
    return {
        "panel": "intent",
        "total_classified": len(events),
        "intent_distribution": dict(sorted(by_intent.items(), key=lambda x: -x[1])),
        "confidence": {
            "mean": _safe_mean(confidences),
            "median": _safe_median(confidences),
            "min": round(min(confidences), 4),
            "max": round(max(confidences), 4),
        },
        "recent": [e.to_dict() for e in recent],
    }


# ---------------------------------------------------------------------------
# Agent dashboard
# ---------------------------------------------------------------------------


def agent_dashboard(lemon: "Lemon") -> dict[str, Any]:
    reasoning = list(lemon.semantic.reasoning)
    reflections = list(lemon.semantic.reflections)
    memory = list(lemon.semantic.memory)

    by_agent: dict[str, dict[str, Any]] = {}
    for e in reasoning:
        a = e.agent_type
        if a not in by_agent:
            by_agent[a] = {"calls": 0, "violations": 0, "latencies": []}
        by_agent[a]["calls"] += 1
        by_agent[a]["violations"] += len(e.decision.get("violations", []))
        by_agent[a]["latencies"].append(e.latency_ms)

    agent_summary = {
        agent: {
            "calls": d["calls"],
            "violations": d["violations"],
            "avg_latency_ms": _safe_mean(d["latencies"]),
            "p95_latency_ms": round(
                (
                    sorted(d["latencies"])[int(len(d["latencies"]) * 0.95)]
                    if len(d["latencies"]) > 1
                    else (d["latencies"][0] if d["latencies"] else 0)
                ),
                2,
            ),
        }
        for agent, d in by_agent.items()
    }

    # Memory hit rates per memory_type
    mem_stats: dict[str, dict[str, int]] = {}
    for m in memory:
        k = m.memory_type
        if k not in mem_stats:
            mem_stats[k] = {"reads": 0, "hits": 0, "writes": 0}
        if m.operation == "read":
            mem_stats[k]["reads"] += 1
            if m.hit:
                mem_stats[k]["hits"] += 1
        elif m.operation == "write":
            mem_stats[k]["writes"] += 1

    hit_rates = {k: round(v["hits"] / v["reads"], 4) if v["reads"] else None for k, v in mem_stats.items()}

    triggered_by_counts: dict[str, int] = {}
    for r in reflections:
        triggered_by_counts[r.triggered_by] = triggered_by_counts.get(r.triggered_by, 0) + 1

    return {
        "panel": "agent",
        "agent_summary": agent_summary,
        "reflections_total": len(reflections),
        "reflections_by_trigger": triggered_by_counts,
        "memory_hit_rates": hit_rates,
        "recent_reasoning": [e.to_dict() for e in _tail(reasoning, 5)],
        "recent_reflections": [e.to_dict() for e in _tail(reflections, 5)],
    }


# ---------------------------------------------------------------------------
# Pipeline dashboard
# ---------------------------------------------------------------------------


def pipeline_dashboard(lemon: "Lemon") -> dict[str, Any]:
    steps = list(lemon.semantic.steps)
    graphs = lemon.semantic.graphs

    latencies: list[float] = [s.latency_ms for s in steps if s.latency_ms > 0]
    failures = [s for s in steps if s.status == "error"]

    cap_latencies: dict[str, list[float]] = {}
    for s in steps:
        cap_latencies.setdefault(s.capability, []).append(s.latency_ms)

    bottleneck_caps = sorted(
        [(k, _safe_mean(v)) for k, v in cap_latencies.items()],
        key=lambda x: -x[1],
    )[:5]

    recent_graphs = [g.to_dict() for g in list(graphs.values())[-10:]]

    # Shadow Capability findings for this panel
    trigger_findings = [f for f in lemon.triggers.history(50) if f["topology"] == "shadow_capability"]

    return {
        "panel": "pipeline",
        "total_steps": len(steps),
        "failed_steps": len(failures),
        "failure_rate": round(len(failures) / len(steps), 4) if steps else 0.0,
        "latency": {
            "mean_ms": _safe_mean(latencies),
            "median_ms": _safe_median(latencies),
            "p95_ms": (round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if len(latencies) > 1 else 0.0),
        },
        "slowest_capabilities": [{"capability": c, "avg_latency_ms": l} for c, l in bottleneck_caps],
        "failure_propagation": [s.to_dict() for s in failures[-10:]],
        "recent_graphs": recent_graphs,
        "analytical_triggers": trigger_findings[-5:],
    }


# ---------------------------------------------------------------------------
# World Model dashboard
# ---------------------------------------------------------------------------


def world_model_dashboard(lemon: "Lemon") -> dict[str, Any]:
    events = list(lemon.semantic.world)
    if not events:
        return {"panel": "world_model", "status": "no_data"}

    discounts = [e.calculate_regional_discount() for e in events]
    drifts = [e.drift_score for e in events]

    by_entity_drift: dict[str, list[float]] = {}
    by_entity_residual: dict[str, list[dict]] = {}
    for e in events:
        by_entity_drift.setdefault(e.entity, []).append(e.drift_score)
        by_entity_residual.setdefault(e.entity, []).append(e.residual_vector)

    high_drift = [e.to_dict() for e in events if e.drift_score > 0.3][-10:]

    # Staleness: flag entity regions without a recent real anchor
    now = _time.time()
    entity_staleness: dict[str, dict[str, Any]] = {}
    for e in events:
        age = now - e.last_real_trace_timestamp
        ent = e.entity
        prev = entity_staleness.get(
            ent,
            {
                "status": "ok",
                "forced_escalation_triggered": False,
                "max_anchor_age_s": 0.0,
            },
        )
        if age > _TIME_BUDGET_THRESHOLD:
            prev["status"] = "PROVISIONAL_WARN"
            prev["forced_escalation_triggered"] = True
        prev["max_anchor_age_s"] = max(prev["max_anchor_age_s"], round(age, 1))
        entity_staleness[ent] = prev

    context_drift_findings = [f for f in lemon.triggers.history(50) if f["topology"] == "context_drift"]

    # Aggregate residuals across entities
    def _mean_residual_component(events_list, key):
        vals = [rv.get(key, 0.0) for e in events_list for rv in [e.residual_vector]]
        return round(mean(vals), 4) if vals else 0.0

    return {
        "panel": "world_model",
        "observations": len(events),
        "regional_discount": {
            "mean": _safe_mean(discounts),
            "min": round(min(discounts), 4),
            "trend": ("degrading" if len(discounts) >= 2 and discounts[-1] < discounts[-10:][0] else "stable"),
        },
        "residual_summary": {
            "mean_latency_delta": _mean_residual_component(events, "latency_delta"),
            "mean_path_divergence": _mean_residual_component(events, "path_divergence"),
            "mean_token_error": _mean_residual_component(events, "token_error"),
        },
        "drift": {
            "mean": _safe_mean(drifts),
            "max": round(max(drifts), 4),
            "high_count": len([d for d in drifts if d > 0.3]),
        },
        "drift_by_entity": {k: _safe_mean(v) for k, v in by_entity_drift.items()},
        "entity_staleness": entity_staleness,
        "high_drift_events": high_drift,
        "recent": [e.to_dict() for e in _tail(events, 10)],
        "analytical_triggers": context_drift_findings[-5:],
    }


# ---------------------------------------------------------------------------
# Governance dashboard
# ---------------------------------------------------------------------------


def governance_dashboard(lemon: "Lemon") -> dict[str, Any]:
    events = list(lemon.semantic.governance)
    if not events:
        return {"panel": "governance", "status": "no_data"}

    allows = [e for e in events if e.decision]
    denies = [e for e in events if not e.decision]
    trusts = [e.trust_score for e in events]
    prov_depths = [len(e.provenance_chain) for e in events]
    by_policy: dict[str, dict[str, int]] = {}
    for e in events:
        p = e.policy_path
        if p not in by_policy:
            by_policy[p] = {"allow": 0, "deny": 0}
        by_policy[p]["allow" if e.decision else "deny"] += 1

    low_trust = [e.to_dict() for e in events if e.trust_score < 0.5][-10:]

    return {
        "panel": "governance",
        "total_decisions": len(events),
        "allows": len(allows),
        "denies": len(denies),
        "deny_rate": round(len(denies) / len(events), 4) if events else 0.0,
        "trust_score": {
            "mean": _safe_mean(trusts),
            "min": round(min(trusts), 4),
            "stdev": _safe_stdev(trusts),
        },
        "provenance_depth": {
            "mean": _safe_mean(prov_depths),
            "max": max(prov_depths),
        },
        "by_policy": by_policy,
        "low_trust_events": low_trust,
        "recent": [e.to_dict() for e in _tail(events, 10)],
    }


# ---------------------------------------------------------------------------
# Learning dashboard
# ---------------------------------------------------------------------------


def learning_dashboard(lemon: "Lemon") -> dict[str, Any]:
    events = list(lemon.semantic.learning)
    if not events:
        return {"panel": "learning", "status": "no_data"}

    td_errors = [abs(e.td_error) for e in events]
    rewards = [e.reward for e in events]
    explores = [e for e in events if e.exploration]
    exploits = [e for e in events if not e.exploration]
    convergent = [e for e in events if e.converging]

    by_cap: dict[str, dict[str, list[float]]] = {}
    for e in events:
        by_cap.setdefault(e.capability, {"td": [], "reward": []})
        by_cap[e.capability]["td"].append(abs(e.td_error))
        by_cap[e.capability]["reward"].append(e.reward)

    cap_summary = {
        c: {
            "mean_td_error": _safe_mean(d["td"]),
            "mean_reward": _safe_mean(d["reward"]),
            "converging": _safe_mean(d["td"]) < 0.01,
        }
        for c, d in by_cap.items()
    }

    # Drift correction: convergence rate over last 50 vs previous 50
    n = 50
    recent_50 = td_errors[-n:] if len(td_errors) >= n else td_errors
    previous_50 = td_errors[-2 * n : -n] if len(td_errors) >= 2 * n else []
    drift_trend = (
        "converging"
        if (previous_50 and _safe_mean(recent_50) < _safe_mean(previous_50))
        else "diverging"
        if previous_50
        else "insufficient_data"
    )

    return {
        "panel": "learning",
        "total_updates": len(events),
        "convergent_updates": len(convergent),
        "convergence_rate": round(len(convergent) / len(events), 4) if events else 0.0,
        "exploration_ratio": round(len(explores) / len(events), 4) if events else 0.0,
        "exploitation_ratio": round(len(exploits) / len(events), 4) if events else 0.0,
        "td_error": {
            "mean": _safe_mean(td_errors),
            "median": _safe_median(td_errors),
            "trend": drift_trend,
        },
        "reward": {
            "mean": _safe_mean(rewards),
            "trend": ("improving" if len(rewards) >= 2 and mean(rewards[-10:]) > mean(rewards[:10]) else "stable"),
        },
        "by_capability": cap_summary,
        "recent": [e.to_dict() for e in _tail(events, 10)],
        "analytical_triggers": [f for f in lemon.triggers.history(50) if f["topology"] == "convergence_delay"][-5:],
    }


# ---------------------------------------------------------------------------
# Full cognitive dashboard
# ---------------------------------------------------------------------------


def full_dashboard(lemon: "Lemon") -> dict[str, Any]:
    """Single-call snapshot of all six cognitive observability panels."""
    return {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "service": "monkeybrain",
        "otel_active": lemon.otel.available,
        "semantic_store": lemon.semantic.summary(),
        "panels": {
            "intent": intent_dashboard(lemon),
            "agent": agent_dashboard(lemon),
            "pipeline": pipeline_dashboard(lemon),
            "world_model": world_model_dashboard(lemon),
            "governance": governance_dashboard(lemon),
            "learning": learning_dashboard(lemon),
        },
    }
