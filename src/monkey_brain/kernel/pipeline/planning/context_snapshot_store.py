"""Persistence for the PlanningContext each tick actually builds — the
real evidence that grounded that tick's plan (Context Grounding).

Previously ContextConstructionEngine.build() (context_engine.py) produced
a rich PlanningContext every tick, and belief_runtime.py::_generate_plan
discarded almost all of it the moment planning finished (only
relevant_knowledge survived, as Belief Timeline records). This gives the
whole thing the same real, lazy Redis persistence kernel/pipeline/
prediction/persistence.py and kernel/pipeline/planning/
current_plan_store.py already established, keyed by execution_id — plus
a per-actor "latest" pointer so the NEXT tick can diff against what the
previous tick actually saw.

Read/write only, never re-fed into the live planner — RetrievedItem is a
frozen dataclass, so plain dataclasses.asdict() is enough for both
directions; there is no need to reconstruct real RetrievedItem instances
on load, only plain dicts for display.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("agentos.pipeline.planning.context_snapshot_store")

_SNAPSHOT_KEY_PREFIX = "monkeybrain:planning_context:"
_LATEST_KEY_PREFIX = "monkeybrain:planning_context:latest:"
_LATEST_BY_GOAL_KEY_PREFIX = "monkeybrain:planning_context:latest_goal:"
_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as
    prediction/persistence.py's own lazy backend."""
    global _client, _connect_attempted
    if _client is not None:
        return _client
    try:
        import redis

        client = redis.from_url(
            _redis_url(),
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
        )
        client.ping()
        _client = client
        _connect_attempted = True
    except Exception as exc:
        logger.warning("ContextSnapshot persistence: Redis unavailable (non-fatal): %s", exc)
        _client = None
    return _client


def _items_to_dicts(items: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(item) for item in items]


def _json_value(value: Any) -> Any:
    """Make runtime value objects safe and useful in the audit artifact."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if dataclasses.is_dataclass(value):
        return _json_value(dataclasses.asdict(value))
    if hasattr(value, "to_dict"):
        try:
            return _json_value(value.to_dict())
        except Exception:
            pass
    return str(value)


@dataclasses.dataclass
class ContextSnapshot:
    execution_id: str
    actor_id: str
    goal_key: str = ""
    """canonicalize_goal(name+description) of the goal this snapshot was
    actually built for — lets the skip-replan reuse path (belief_runtime.py)
    find the real prior grounding for THIS goal instead of falling back to
    load_latest_context_snapshot's actor-wide "most recent for any goal,"
    which was silently stamping an unrelated goal's retrieval onto this
    execution_id (e.g. a coffee purchase showing egg/milk experiences
    retrieved by an interleaved, unrelated grocery tick)."""
    created_at: float = dataclasses.field(default_factory=time.time)
    knowledge: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    relationships: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    context_events: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    experiences: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    conversations: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    executions: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    relevant_locations: list[str] = dataclasses.field(default_factory=list)
    relevant_objects: list[str] = dataclasses.field(default_factory=list)
    available_capabilities: list[str] = dataclasses.field(default_factory=list)
    affiliations: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    available_resources: list[Any] = dataclasses.field(default_factory=list)
    world_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    retrieval_sources: list[str] = dataclasses.field(default_factory=list)
    retrieval_latency_ms: dict[str, float] = dataclasses.field(default_factory=dict)
    planner_prompt: str = ""
    prompt_tokens: int = 0
    observed_facts: list[Any] = dataclasses.field(default_factory=list)
    final_planner_context: dict[str, Any] = dataclasses.field(default_factory=dict)
    summary: dict[str, int] = dataclasses.field(default_factory=dict)
    diff_from_previous: dict[str, Any] | None = None
    """Set at save time (see diff_snapshots below) — computed once,
    against whatever load_latest_context_snapshot(actor_id) returned
    BEFORE this snapshot overwrote the "latest" pointer, so
    GET .../context-diff never needs to recompute it."""

    @staticmethod
    def from_planning_context(
        execution_id: str,
        actor_id: str,
        planning_context: Any,
        goal_key: str = "",
    ) -> "ContextSnapshot":
        knowledge = _items_to_dicts(planning_context.relevant_knowledge)
        relationships = _items_to_dicts(planning_context.relevant_relationships)
        context_events = _items_to_dicts(planning_context.relevant_context_events)
        experiences = _items_to_dicts(planning_context.relevant_experiences)
        conversations = _items_to_dicts(planning_context.relevant_conversations)
        executions = _items_to_dicts(planning_context.relevant_executions)
        metadata = planning_context.metadata or {}
        legacy_belief = metadata.get("_legacy_belief")
        observed_facts = [_json_value(f) for f in (getattr(legacy_belief, "facts", ()) or ())]
        affiliations = list(metadata.get("active_memberships") or ())
        resources = [_json_value(r) for r in planning_context.available_resources]
        world_state = {
            "location": _json_value(planning_context.current_location),
            "beliefs": _json_value(planning_context.current_beliefs),
            "resources": resources,
        }
        source_names = sorted(
            {
                item.get("source", "")
                for group in (
                    knowledge,
                    relationships,
                    context_events,
                    experiences,
                    conversations,
                    executions,
                )
                for item in group
                if item.get("source")
            }
            | (
                {"world_state"}
                if resources or planning_context.relevant_locations or planning_context.relevant_objects
                else set()
            )
        )
        return ContextSnapshot(
            execution_id=execution_id,
            actor_id=actor_id,
            goal_key=goal_key,
            knowledge=knowledge,
            relationships=relationships,
            context_events=context_events,
            experiences=experiences,
            conversations=conversations,
            executions=executions,
            relevant_locations=list(planning_context.relevant_locations),
            relevant_objects=list(planning_context.relevant_objects),
            available_capabilities=list(planning_context.available_capabilities),
            affiliations=[_json_value(a) for a in affiliations],
            available_resources=resources,
            world_state=world_state,
            retrieval_sources=source_names,
            retrieval_latency_ms=dict(metadata.get("retrieval_latency_ms") or {}),
            planner_prompt=str(metadata.get("_planner_prompt", "") or ""),
            prompt_tokens=int(metadata.get("_prompt_tokens", 0) or 0),
            observed_facts=observed_facts,
            final_planner_context={
                "goal": _json_value(planning_context.goal),
                "observed_facts": observed_facts,
                "knowledge": knowledge,
                "relationships": relationships,
                "semantic_memory": experiences + conversations,
                "episodic_memory": experiences + conversations + executions,
                "context_events": context_events,
                "affiliations": [_json_value(a) for a in affiliations],
                "locations": list(planning_context.relevant_locations),
                "objects": list(planning_context.relevant_objects),
                "world_state": world_state,
            },
            summary={
                "entity_count": len(knowledge),
                "relationship_count": len(relationships),
                "context_event_count": len(context_events),
                "experience_count": len(experiences) + len(conversations) + len(executions),
                "semantic_memory_count": len(experiences) + len(conversations),
                "episodic_memory_count": len(experiences) + len(conversations) + len(executions),
                "affiliation_count": len(affiliations),
                "location_count": len(planning_context.relevant_locations),
                "object_count": len(planning_context.relevant_objects),
                "world_object_count": len(resources),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ContextSnapshot":
        return ContextSnapshot(
            execution_id=d.get("execution_id", ""),
            actor_id=d.get("actor_id", ""),
            goal_key=str(d.get("goal_key", "") or ""),
            created_at=float(d.get("created_at", time.time())),
            knowledge=list(d.get("knowledge", []) or []),
            relationships=list(d.get("relationships", []) or []),
            context_events=list(d.get("context_events", []) or []),
            experiences=list(d.get("experiences", []) or []),
            conversations=list(d.get("conversations", []) or []),
            executions=list(d.get("executions", []) or []),
            relevant_locations=list(d.get("relevant_locations", []) or []),
            relevant_objects=list(d.get("relevant_objects", []) or []),
            available_capabilities=list(d.get("available_capabilities", []) or []),
            affiliations=list(d.get("affiliations", []) or []),
            available_resources=list(d.get("available_resources", []) or []),
            world_state=dict(d.get("world_state", {}) or {}),
            retrieval_sources=list(d.get("retrieval_sources", []) or []),
            retrieval_latency_ms=dict(d.get("retrieval_latency_ms", {}) or {}),
            planner_prompt=str(d.get("planner_prompt", "") or ""),
            prompt_tokens=int(d.get("prompt_tokens", 0) or 0),
            observed_facts=list(d.get("observed_facts", []) or []),
            final_planner_context=dict(d.get("final_planner_context", {}) or {}),
            summary=dict(d.get("summary", {}) or {}),
            diff_from_previous=d.get("diff_from_previous"),
        )


def save_context_snapshot(snapshot: ContextSnapshot) -> bool:
    """Never raises — a dropped write here just means this one tick's
    grounding evidence is unrecoverable later, not lost cognition (the
    plan/decision/execution Timeline records are unaffected)."""
    client = _get_client()
    if client is None or not snapshot.execution_id:
        return False
    try:
        client.set(
            f"{_SNAPSHOT_KEY_PREFIX}{snapshot.execution_id}",
            json.dumps(snapshot.to_dict()),
        )
        if snapshot.actor_id:
            client.set(f"{_LATEST_KEY_PREFIX}{snapshot.actor_id}", snapshot.execution_id)
            if snapshot.goal_key:
                client.set(
                    f"{_LATEST_BY_GOAL_KEY_PREFIX}{snapshot.actor_id}:{snapshot.goal_key}",
                    snapshot.execution_id,
                )
        return True
    except Exception as exc:
        logger.warning(
            "save_context_snapshot(%s) failed: %s",
            snapshot.execution_id,
            exc,
            exc_info=True,
        )
        return False


def load_context_snapshot(execution_id: str) -> ContextSnapshot | None:
    client = _get_client()
    if client is None or not execution_id:
        return None
    try:
        raw = client.get(f"{_SNAPSHOT_KEY_PREFIX}{execution_id}")
        if not raw:
            return None
        return ContextSnapshot.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("load_context_snapshot(%s) failed: %s", execution_id, exc)
        return None


def load_latest_context_snapshot(actor_id: str) -> ContextSnapshot | None:
    """Called BEFORE the current tick's save_context_snapshot() — that's
    what makes this "the previous tick's snapshot" for diffing, not the
    one about to be written."""
    client = _get_client()
    if client is None or not actor_id:
        return None
    try:
        execution_id = client.get(f"{_LATEST_KEY_PREFIX}{actor_id}")
        if not execution_id:
            return None
        return load_context_snapshot(execution_id)
    except Exception as exc:
        logger.debug("load_latest_context_snapshot(%s) failed: %s", actor_id, exc)
        return None


def load_latest_context_snapshot_for_goal(actor_id: str, goal_key: str) -> ContextSnapshot | None:
    """Same as load_latest_context_snapshot, but scoped to the exact goal
    this tick is about to (re)plan for — the real fix for the skip-replan
    reuse path in belief_runtime.py::_generate_plan, which must copy
    forward the grounding that actually produced the CACHED PLAN it's
    reusing, not whatever this actor's most recent tick happened to be
    for an entirely different goal."""
    client = _get_client()
    if client is None or not actor_id or not goal_key:
        return None
    try:
        execution_id = client.get(f"{_LATEST_BY_GOAL_KEY_PREFIX}{actor_id}:{goal_key}")
        if not execution_id:
            return None
        return load_context_snapshot(execution_id)
    except Exception as exc:
        logger.debug(
            "load_latest_context_snapshot_for_goal(%s, %s) failed: %s",
            actor_id,
            goal_key,
            exc,
        )
        return None


_DIFF_SOURCES = (
    "knowledge",
    "relationships",
    "context_events",
    "experiences",
    "conversations",
    "executions",
)


def diff_snapshots(previous: ContextSnapshot | None, current: ContextSnapshot) -> dict[str, Any]:
    """Pure. Real set difference by each item's `content` string per
    source — no fabrication, no LLM summarization. previous is None on
    an actor's first-ever planning tick: everything in `current` is
    "added", nothing is "removed" — an honest bootstrap case, same
    shape kernel/pipeline/planning/plan_hysteresis.py::decide() uses for
    "no prior Current Plan"."""
    if previous is None:
        added = {source: [item.get("content", "") for item in getattr(current, source)] for source in _DIFF_SOURCES}
        return {
            "is_first_context": True,
            "added": added,
            "removed": {source: [] for source in _DIFF_SOURCES},
        }

    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    for source in _DIFF_SOURCES:
        prev_contents = {item.get("content", "") for item in getattr(previous, source)}
        curr_contents = {item.get("content", "") for item in getattr(current, source)}
        added[source] = sorted(curr_contents - prev_contents)
        removed[source] = sorted(prev_contents - curr_contents)
    return {"is_first_context": False, "added": added, "removed": removed}
