"""Society Observability (Step 12.11) — makes the entire society observable.

Produce:
    actor timelines, interaction graphs, world evolution,
    context replay, coordination metrics, governance reports.

Support debugging at society scale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.society.context_stream import (
    ContextEvent,
    ContextEventType,
)


@dataclass(frozen=True)
class ActorTimelineEntry:
    """One entry in an actor's timeline."""

    actor_id: str = ""
    event_type: str = ""
    description: str = ""
    timestamp: float = 0.0
    version: int = 0


@dataclass(frozen=True)
class ActorTimeline:
    """Complete timeline for one actor."""

    actor_id: str = ""
    entries: tuple[ActorTimelineEntry, ...] = ()
    total_events: int = 0


@dataclass(frozen=True)
class InteractionEdge:
    """One edge in the interaction graph."""

    source_actor: str = ""
    target_actor: str = ""
    interaction_type: str = ""
    count: int = 0
    last_interaction: float = 0.0


@dataclass(frozen=True)
class InteractionGraph:
    """Graph of actor interactions."""

    edges: tuple[InteractionEdge, ...] = ()
    actor_count: int = 0
    total_interactions: int = 0


@dataclass(frozen=True)
class WorldEvolutionEntry:
    """One entry in the world evolution history."""

    version: int = 0
    timestamp: float = 0.0
    event_type: str = ""
    description: str = ""
    actor_id: str = ""


@dataclass(frozen=True)
class WorldEvolution:
    """History of world changes."""

    entries: tuple[WorldEvolutionEntry, ...] = ()
    total_versions: int = 0


@dataclass(frozen=True)
class CoordinationMetrics:
    """Metrics about society coordination."""

    total_ticks: int = 0
    total_interactions: int = 0
    active_actors: int = 0
    avg_interaction_latency_ms: float = 0.0
    coordination_efficiency: float = 0.0
    """0.0 = no coordination, 1.0 = perfectly coordinated."""


@dataclass(frozen=True)
class GovernanceReport:
    """Report on society governance."""

    total_policies: int = 0
    active_policies: int = 0
    compliance_rate: float = 0.0
    trust_average: float = 0.0
    safety_violations: int = 0
    audit_entries: int = 0


@dataclass(frozen=True)
class SocietyTrace:
    """Complete observability trace for the society."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    actor_timelines: tuple[ActorTimeline, ...] = ()
    interaction_graph: InteractionGraph = field(default_factory=InteractionGraph)
    world_evolution: WorldEvolution = field(default_factory=WorldEvolution)
    coordination_metrics: CoordinationMetrics = field(default_factory=CoordinationMetrics)
    governance_report: GovernanceReport = field(default_factory=GovernanceReport)
    narrative: str = ""
    created_at: float = field(default_factory=time.time)

    def explain(self) -> str:
        return self.narrative

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "actor_count": len(self.actor_timelines),
            "interaction_edges": len(self.interaction_graph.edges),
            "world_versions": self.world_evolution.total_versions,
            "narrative": self.narrative,
            "created_at": self.created_at,
        }


class SocietyObservability:
    """Produces observability artifacts for the society."""

    def build_actor_timelines(self, events: tuple[ContextEvent, ...]) -> tuple[ActorTimeline, ...]:
        actor_events: dict[str, list[ContextEvent]] = {}
        for event in events:
            if event.actor_id:
                actor_events.setdefault(event.actor_id, []).append(event)

        timelines: list[ActorTimeline] = []
        for actor_id, actor_evts in actor_events.items():
            entries = tuple(
                ActorTimelineEntry(
                    actor_id=actor_id,
                    event_type=event.event_type.value,
                    description=event.description,
                    timestamp=event.timestamp,
                    version=event.version,
                )
                for event in sorted(actor_evts, key=lambda e: e.timestamp)
            )
            timelines.append(
                ActorTimeline(
                    actor_id=actor_id,
                    entries=entries,
                    total_events=len(entries),
                )
            )
        return tuple(timelines)

    def build_interaction_graph(self, events: tuple[ContextEvent, ...]) -> InteractionGraph:
        edge_counts: dict[tuple[str, str], int] = {}
        edge_last: dict[tuple[str, str], float] = {}
        actors: set[str] = set()

        interaction_events = [e for e in events if e.event_type == ContextEventType.INTERACTION]
        for event in interaction_events:
            actors.add(event.actor_id)
            for other_id in self._extract_participants(event):
                actors.add(other_id)
                edge = (event.actor_id, other_id)
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
                edge_last[edge] = max(edge_last.get(edge, 0), event.timestamp)

        edges = tuple(
            InteractionEdge(
                source_actor=edge[0],
                target_actor=edge[1],
                interaction_type="interaction",
                count=count,
                last_interaction=edge_last[edge],
            )
            for edge, count in edge_counts.items()
        )
        return InteractionGraph(
            edges=edges,
            actor_count=len(actors),
            total_interactions=len(interaction_events),
        )

    def build_world_evolution(self, events: tuple[ContextEvent, ...]) -> WorldEvolution:
        world_events = [e for e in events if e.event_type == ContextEventType.WORLD_UPDATE]
        entries = tuple(
            WorldEvolutionEntry(
                version=e.version,
                timestamp=e.timestamp,
                event_type=e.event_type.value,
                description=e.description,
                actor_id=e.actor_id,
            )
            for e in sorted(world_events, key=lambda e: e.timestamp)
        )
        return WorldEvolution(entries=entries, total_versions=len(entries))

    def build_society_trace(
        self,
        events: tuple[ContextEvent, ...],
        *,
        total_ticks: int = 0,
        active_actors: int = 0,
        total_policies: int = 0,
        compliance_rate: float = 1.0,
        trust_average: float = 0.5,
    ) -> SocietyTrace:
        timelines = self.build_actor_timelines(events)
        graph = self.build_interaction_graph(events)
        evolution = self.build_world_evolution(events)

        metrics = CoordinationMetrics(
            total_ticks=total_ticks,
            total_interactions=graph.total_interactions,
            active_actors=active_actors,
            coordination_efficiency=min(1.0, graph.total_interactions / max(1, total_ticks)),
        )

        governance = GovernanceReport(
            total_policies=total_policies,
            active_policies=total_policies,
            compliance_rate=compliance_rate,
            trust_average=trust_average,
        )

        narrative = self._build_narrative(timelines, graph, evolution, metrics, governance)

        return SocietyTrace(
            actor_timelines=timelines,
            interaction_graph=graph,
            world_evolution=evolution,
            coordination_metrics=metrics,
            governance_report=governance,
            narrative=narrative,
        )

    def _extract_participants(self, event: ContextEvent) -> list[str]:
        payload = event.payload
        if isinstance(payload, dict):
            participants = payload.get("participants", [])
            if isinstance(participants, (list, tuple)):
                return [str(p) for p in participants if p != event.actor_id]
        return []

    def _build_narrative(self, timelines, graph, evolution, metrics, governance) -> str:
        lines = ["Society Trace", "    ↓"]
        lines.append(f"Actors: {len(timelines)}")
        for tl in timelines:
            lines.append(f"  {tl.actor_id}: {tl.total_events} events")
        lines.append("    ↓")
        lines.append(f"Interactions: {graph.total_interactions} across {len(graph.edges)} edges")
        lines.append("    ↓")
        lines.append(f"World Evolution: {evolution.total_versions} versions")
        lines.append("    ↓")
        lines.append(f"Coordination: {metrics.total_ticks} ticks, efficiency {metrics.coordination_efficiency:.2f}")
        lines.append("    ↓")
        lines.append(f"Governance: {governance.total_policies} policies, compliance {governance.compliance_rate:.0%}")
        return "\n".join(lines)
