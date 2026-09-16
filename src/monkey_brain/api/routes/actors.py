"""Actor API — CRUD and cognitive operations for actors.

GET    /actors                — list all actors
POST   /actors                — register a new actor
GET    /actors/{id}           — get actor details
DELETE /actors/{id}           — unregister an actor
PATCH  /actors/{id}           — update actor metadata
GET    /actors/{id}/beliefs   — actor's beliefs
GET    /actors/{id}/memory    — actor's memory
GET    /actors/{id}/goals     — actor's goals
GET    /actors/{id}/capabilities — actor's capabilities
POST   /actors/{id}/ask       — ask this actor a natural-language question
GET    /actors/{id}/status    — actor status
POST   /actors/{id}/tick      — trigger one cognitive tick
POST   /actors/{id}/observe   — trigger observation only
POST   /actors/{id}/plan      — trigger planning only
POST   /actors/{id}/execute   — trigger execution only
GET    /actors/registry       — Actor Registry: every actor known, any node
GET    /actors/{id}/registry  — Actor Registry: one actor's durable record
POST   /actors/{id}/lifecycle — Actor Lifecycle Controller: set desired state
GET    /actors/{id}/lifecycle — Actor Lifecycle Controller: desired/observed/history
GET    /scheduler/nodes       — Actor Scheduler: every known execution node
GET    /actors/{id}/placement — Actor Scheduler: this actor's desired/observed node
POST   /actors/{id}/migrate   — Actor Scheduler: deliberate rescheduling
POST   /actors/apply          — cogctl apply: declarative ActorSpecification (create-or-update)
POST   /actors/{id}/restart   — cogctl restart: suspend then resume, same actor_id
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.monkey_brain.api.audit_decorator import audited
from src.monkey_brain.api.dependencies import (
    require_permission,
    require_self_or_permission,
)
from services.common.opa import require_opa
from src.monkey_brain.api.gateway_models import (
    ActorCreateRequest,
    ActorUpdateRequest,
    ActorResponse,
    ActorBeliefsResponse,
    ActorMemoryResponse,
    ActorGoalsResponse,
    ActorAddGoalRequest,
    ActorAffiliationsResponse,
    ActorAffiliationCreateRequest,
    ActorAffiliationUpdateRequest,
    ActorIntentResponse,
    ActorPlansResponse,
    ActorDecisionsResponse,
    ActorExecutionHistoryResponse,
    ActorMemoryCategoryResponse,
    ActorCognitiveStateResponse,
    ActorCapabilitiesResponse,
    ActorStatusResponse,
    ActorTickRequest,
    ActorTickResponse,
    ExperienceRecordRequest,
    ExperienceRecordResponse,
    ActorRelationshipCreateRequest,
    ActorAddressCreateRequest,
    AskActorRequest,
    AskActorResponse,
    ActorChatRequest,
    ActorChatResponse,
    ActorChatWebResult,
    GoalDraft,
    GoalDraftRequest,
    GoalDraftResponse,
    WebSearchChatRequest,
    WebSearchChatSource,
    WebSearchChatResponse,
    ExecutionChatRequest,
    ExecutionChatResponse,
    ExecutionChatEvidence,
    TransactionRequest,
    TransactionResponse,
    TransactionStepResponse,
    ActorMoveRequest,
    TeamCreateRequest,
    TeamMemberAddRequest,
    serialize_beliefs,
)
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
    ActorCapability,
    ActorAddress,
)
from src.monkey_brain.kernel.affiliations.relationship_bridge import (
    make_relationship_affiliation,
    is_relationship_affiliation,
    affiliation_to_relationship_dict,
)
from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
from src.shared.api_protocols import TickResultProtocol

logger = logging.getLogger("agentos.gateway.actors")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _find_actor_state(pr: Any, actor_id: str) -> tuple[Any, Any] | None:
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is not None:
            return sr, state
    return None


def _gate_on_world_validation(pr: Any, actor_id: str | None = None) -> None:
    """Gate 3 (ADR-010) — before execute: block a real action from running
    against a structurally broken world. Off switch (WORLD_VALIDATION_GATE_
    EXECUTE=false) exists for perf-sensitive deployments once scale testing
    (Gate 7) has a real number for the cost of a full world scan per call;
    default is ON because correctness matters more than that cost today.

    actor_id scopes an actor-scoped violation (presence_consistency/
    membership_consistency) belonging to some OTHER actor out of the
    pass/fail decision — same reasoning validate_world()'s own docstring
    already documents, and the same param prompt.py's equivalent gate
    call already passes (validate_world(planetary_runtime, actor_id=
    user_id)). Without this, run_actor_tick() (used by both this route's
    POST /actors/{id}/execute and actor_runtime.py's per-Pod POST
    /execute) blocked EVERY actor's execution on ANY unrelated actor's
    pre-existing violation anywhere in the whole shared world, not just
    a violation about the actor actually being executed — confirmed live
    with a second, unrelated debug actor's own presence gap. NOTE: this
    does NOT exempt a violation about actor_id itself (validate_world's
    own filter keeps `v["actor_id"] == actor_id` in `blocking` on
    purpose) — an actor whose OWN presence/membership record is broken
    still correctly blocks on its own execution either way."""
    import os

    if os.getenv("WORLD_VALIDATION_GATE_EXECUTE", "true").strip().lower() == "false":
        return
    from src.monkey_brain.kernel.validation.world_validator import validate_world

    report = validate_world(pr, actor_id=actor_id)
    if not report["ok"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "world validation failed — refusing to execute against a structurally inconsistent world",
                "violation_count": report["violation_count"],
                "categories": report["categories"],
            },
        )


def _find_team(pr: Any, team_id: str) -> tuple[Any, Any] | None:
    """Team is SocietyRuntime-owned and globally-unique-ID'd (uuid4), so
    finding it means searching across societies — same pattern as
    _find_actor_state above."""
    for sr in pr.all_societies():
        team = sr.get_team(team_id)
        if team is not None:
            return sr, team
    return None


def affiliation_to_api_dict(affiliation: Affiliation) -> dict[str, Any]:
    return {
        "affiliation_id": affiliation.affiliation_id,
        "affiliation_type": affiliation.affiliation_type,
        "target_id": affiliation.target_id,
        "target_name": affiliation.target_name,
        "trust_level": affiliation.trust_level,
        "category": affiliation.category,
        "valid_from": affiliation.valid_from,
        "valid_until": affiliation.valid_until,
        "permissions": list(affiliation.permissions),
        "policies": list(affiliation.policies),
        "priority": affiliation.priority,
        "metadata": affiliation.metadata,
    }


# ── Cognitive State helpers (Promote Cognitive State to a First-Class
# Runtime Model) — each backs both its own granular route below and the
# /cognitive-state aggregate, so nothing is computed twice.


def _timeline_current(actor_id: str, kind: Any) -> dict[str, Any] | None:
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    entry = TimelineStore().current(actor_id, kind)
    return entry.to_dict() if entry is not None else None


def _timeline_history(actor_id: str, kind: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first, capped — TimelineStore.query() itself returns
    oldest-first (see its own docstring)."""
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    entries = TimelineStore().query(actor_id, kind)
    return [e.to_dict() for e in reversed(entries[-limit:])]


def _get_intent(actor_id: str) -> dict[str, Any] | None:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    return _timeline_current(actor_id, TimelineKind.INTENT)


def _get_plans(actor_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    return _timeline_history(actor_id, TimelineKind.PLAN, limit)


def _latest_meaningful_plan(plans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ "Current Plan" should be the actor's latest real plan, not
    whichever PlanRecord happens to be newest — an autonomous tick with
    no explicit goal (goal="", steps=[]) still writes one every cycle
    (see cognitive_actor.py::_record_cognitive_artifacts) and would
    otherwise silently clobber a real, meaningful plan from moments
    earlier every time the planetary auto-tick fires. plans is already
    newest-first (_timeline_history); this just skips the empty ones on
    the way to picking "current," it doesn't hide them — plan_history
    (the full, unfiltered list) still includes them."""
    return next((p for p in plans if p.get("steps")), plans[0] if plans else None)


def _active_goals(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ "Goals" should be the actor's current standing intentions, not a
    log of every prompt it was ever given (see the Quality Pass review:
    duplicate near-identical goal phrasings and completed goals were all
    showing up together, forever). goals is already newest-first
    (_timeline_history); for each normalized goal name, keep only its
    newest record, and only if that newest record's status is "active" —
    a later "completed" record for the same normalized name correctly
    drops it from this view without deleting it (goal_history keeps the
    full raw log). Also applied at read time so legacy pre-fix duplicate
    records don't need a migration."""
    from src.monkey_brain.kernel.pipeline.belief_state import _normalize_goal_key

    seen: set[str] = set()
    active: list[dict[str, Any]] = []
    for g in goals:
        name = (g.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_goal_key(name)
        if key in seen:
            continue
        seen.add(key)
        if g.get("status", "active") == "active":
            active.append(g)
    return active


def _goal_executions(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round 3 of the Quality Pass review (issue 1): goal_history reads
    as a log of every REQUEST ("Buy 1L milk." repeated N times), not a
    stable set of goals — because each real prompt writes its own
    GoalRecord transition. The actual persistent concept is the
    normalized goal name; each individual request is really an
    EXECUTION of that goal. plans (PlanRecord history, already
    newest-first) already carries the real verbatim requested text
    (plan.goal) and the real per-request outcome (status/result) —
    grouping by normalized goal name turns already-real data into
    exactly this split, with zero new persistence."""
    from src.monkey_brain.kernel.pipeline.belief_state import _normalize_goal_key

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for plan in plans:
        goal_text = (plan.get("goal") or "").strip()
        if not goal_text:
            continue
        key = _normalize_goal_key(goal_text)
        if key not in grouped:
            grouped[key] = {"goal": goal_text, "executions": []}
            order.append(key)
        grouped[key]["executions"].append(
            {
                "requested": goal_text,
                "outcome": plan.get("status", ""),
                "timestamp": plan.get("start_time", 0),
                "result": plan.get("result", ""),
            }
        )
    return [grouped[k] for k in order]


def _get_decisions(actor_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    return _timeline_history(actor_id, TimelineKind.DECISION, limit)


def _get_current_plan(actor_id: str, plans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The real, persisted Current Plan (plan hysteresis — kernel/
    pipeline/planning/current_plan_store.py) when one exists; falls back
    to the Timeline-derived _latest_meaningful_plan heuristic for actors
    that predate this feature, or when Redis is unavailable — same
    resilience shape load_transition_model already established.

    Bug fix: CurrentPlanRecord.to_dict() hardcodes status="current" and
    completed_nodes=0 (real values for the Current Plan concept itself,
    which is a persistent SPECIFICATION, not a single tick's outcome).
    The frontend Domain Plan section reuses those same two fields to show
    per-tick PROGRESS, though — so a Current Plan that had genuinely
    completed 2/2 steps on its most recent real execution (a "replace" or
    "keep" tick — kernel/pipeline/comparison/integration.py::_run_decide)
    kept showing "0 / 2 completed" / status "current" forever, looking
    permanently stuck even while real orders/payments succeeded every
    tick. plans (already fetched, newest-first) already has a real
    PlanRecord per tick sharing this same plan_id (see
    cognitive_actor.py's correlated_plan_id) with the real status/
    completed_nodes/result from that specific execution — surface those
    instead of the placeholder."""
    from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
        load_current_plan,
    )
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

    # (actor_id, goal_key) is now the Current Plan's real key — display the
    # plan for whatever goal the actor is CURRENTLY pursuing, i.e. the most
    # recent plan/execution's own goal (plans is already newest-first).
    latest_goal = next((p.get("goal", "") for p in plans if p.get("goal")), "")
    if not latest_goal:
        return _latest_meaningful_plan(plans)
    record = load_current_plan(actor_id, canonicalize_goal(latest_goal))
    if record is None:
        return _latest_meaningful_plan(plans)
    d = record.to_dict()
    d["age_seconds"] = time.time() - record.created_at
    latest_execution = next(
        (p for p in plans if p.get("plan_id") == record.plan_id and p.get("status") != "superseded"),
        None,
    )
    if latest_execution is not None:
        d["status"] = latest_execution.get("status", d["status"])
        d["completed_nodes"] = latest_execution.get("completed_nodes", d["completed_nodes"])
        d["result"] = latest_execution.get("result", d["result"])
    return d


def _get_plan_decision(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """decisions is newest-first and mixes two DecisionRecord "flavors"
    (see cognitive_actor.py::_record_cognitive_artifacts) — filters to
    the plan-hysteresis one specifically."""
    return next(
        (d for d in decisions if (d.get("metadata") or {}).get("decision_kind") == "plan_hysteresis"),
        None,
    )


def _get_execution_history(pr: Any, actor_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Real ExecutionRecord timeline entries, each enriched — read-time
    only, no pipeline write — with reward/actor_loss/status/actions from
    the nearest cognitive_tick memory entry for the same actor (written
    moments later in the same tick by CognitiveActor._cognitive_tick,
    see kernel/compile/cognitive_actor.py). Never fabricates: a record
    with no match within tolerance just doesn't get those fields."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    records = list(reversed(TimelineStore().query(actor_id, TimelineKind.EXECUTION)[-limit:]))
    memory_entries: list[dict[str, Any]] = []
    found = _find_actor_state(pr, actor_id)
    if found is not None and found[1].actor_runtime is not None:
        memory_entries = [
            m
            for m in found[1].actor_runtime.memory_snapshot(500)
            if isinstance(m, dict) and m.get("type") == "cognitive_tick"
        ]

    tolerance_seconds = 5.0
    results = []
    for record in records:
        d = record.to_dict()
        best_match, best_delta = None, tolerance_seconds
        for m in memory_entries:
            ts = m.get("timestamp")
            if ts is None:
                continue
            delta = abs(ts - record.start_time)
            if delta <= best_delta:
                best_delta, best_match = delta, m
        if best_match is not None:
            d["reward"] = best_match.get("reward")
            d["actor_loss"] = best_match.get("actor_loss")
            d["actions_executed"] = best_match.get("actions")
        results.append(d)
    return results


_BELIEF_ENTITY_RE = re.compile(r"^(.*?) \((\w+),\s*(.*)\)$")
_BELIEF_RELATIONSHIP_RE = re.compile(r"^(.*?) <-> (.*)$")

# entity_type (from _explore_knowledge's f"{type.value}" — see
# kernel/pipeline/planning/context_engine.py) -> the real, defensible
# inferred claim retrieval into this actor's candidate pool actually
# supports. "asset" entities are drawn from open_products()/its
# equivalents, which already exclude closed stores/unstocked items —
# "available" is what retrieval into that pool means, not an unverified
# guess. "organization" (stores) means reachable/open by the same logic.
# Anything else (a plain entity with no typed claim to make) stays
# "known" — honest, not overclaimed.
_BELIEF_INFERRED_STATE = {"asset": "available", "organization": "reachable"}


def _parse_belief_content(content: str) -> tuple[str, str] | None:
    """Round 2 of the Quality Pass review: raw KG entity dumps ('1L Milk
    (asset, id=..., price=$2.5)') read as observations, not beliefs — a
    belief should read as an inferred current-state claim ('1L Milk —
    available'), with the id/price moved into evidence where it
    belongs. Pure string reshaping of the same real retrieved content
    (kernel/pipeline/planning/context_engine.py::_explore_knowledge's
    two content formats) — the underlying RetrievedItem/BeliefRecord and
    the text the LLM planner itself reads are untouched; only how this
    route displays it changes.

    Cognitive Loop Verification (round 2): this used to also derive the
    belief's SUBJECT from this same string — correct only for the one
    belief-persist loop this format was written for (cognitive_actor.py's
    KG-retrieval loop, where subject and value are literally the same
    string). BeliefFusion._record_belief_history() (kernel/society/
    belief.py, real, independent, pre-existing) sets a genuinely
    different real subject (entity name) and value (attributes dict) —
    parsing the value there produced garbage. Callers now get the real
    subject from record["subject"] directly; this function only ever
    reshapes the value, and returns None (not a guessed "known") when
    the content doesn't match either KG format at all, so a caller can
    fall back to rendering the real value honestly instead of forcing a
    guess onto content this format was never designed for. Returns
    (inferred_value, evidence_detail)."""
    m = _BELIEF_ENTITY_RE.match(content)
    if m:
        entity_type, detail = m.group(2), m.group(3)
        return _BELIEF_INFERRED_STATE.get(entity_type, "known"), detail
    m = _BELIEF_RELATIONSHIP_RE.match(content)
    if m:
        return f"related to {m.group(2)}", ""
    return None


def _readable_belief_value(value: Any) -> str:
    """Honest fallback for a real belief value this route has no
    KG-specific reshaping for (e.g. BeliefFusion's real attribute dicts,
    {"open": True}) — render it plainly instead of guessing an inferred
    state the data doesn't actually support."""
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items()) if value else "known"
    if value is None or value == "":
        return "known"
    return str(value)


_BELIEF_SOURCE_LABELS = {"knowledge_graph": "Knowledge Graph"}


def _format_belief_evidence(source: str, detail: str) -> list[str]:
    """Round 3 of the Quality Pass review (issues 5/7): raw evidence
    ('knowledge_graph', 'id=product_9d87..., price=$2.5') reads as
    internal plumbing, not a fact a person would cite. Same real detail
    string _parse_belief_content already extracts from the entity's KG
    attributes — just phrased as the actual facts the actor relied on
    ('Price = $2.50') instead of a raw key=value dump. An id isn't
    meaningful to a reader on its own; its presence just confirms the
    entity is a real, resolvable catalog record."""
    evidence = []
    label = _BELIEF_SOURCE_LABELS.get(source, source.replace("_", " ").title()) if source else ""
    if label:
        evidence.append(label)
    for pair in (p.strip() for p in detail.split(",") if p.strip()):
        key, _, value = pair.partition("=")
        key, value = key.strip(), value.strip()
        if key == "id":
            evidence.append("Confirmed in catalog")
        elif key == "price":
            evidence.append(f"Price = {value}")
        elif key and value:
            evidence.append(f"{key.capitalize()} = {value}")
    return evidence


def _grouped_beliefs(actor_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """One row per real subject — this actor's CURRENT understanding —
    instead of one row per tick that happened to re-retrieve the same
    fact (review: 'duplicate beliefs... every execution appends
    beliefs'). _record_cognitive_artifacts (cognitive_actor.py) still
    writes the full, real per-tick history to the append-only Timeline
    unchanged (needed for audit/goal_history-style full logs); this is
    a read-time dedup + reshape, same pattern _active_goals() already
    established for goals. Newest-first input (_timeline_history) means
    the first record seen per subject is already the latest."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in _timeline_history(actor_id, TimelineKind.BELIEF, limit):
        subject = record.get("subject") or ""
        if not subject:
            continue
        raw_value = record.get("value")
        parsed = _parse_belief_content(raw_value) if isinstance(raw_value, str) else None
        if parsed is not None:
            value, detail = parsed
        else:
            value, detail = _readable_belief_value(raw_value), ""
        if subject not in grouped:
            grouped[subject] = {
                "entry_id": record.get("entry_id", ""),
                "actor_id": actor_id,
                "subject": subject,
                "predicate": "status",
                "value": value,
                "confidence": record.get("confidence", 0.0),
                "source": record.get("source", ""),
                "start_time": record.get("start_time", 0),
                "end_time": None,
                "metadata": {
                    "evidence": _format_belief_evidence(record.get("source", ""), detail),
                    "previous_value": None,
                    "reason": (record.get("metadata") or {}).get("reason", ""),
                },
                "_count": 0,
            }
            order.append(subject)
        grouped[subject]["_count"] += 1
    results = []
    for subject in order:
        bucket = grouped[subject]
        count = bucket.pop("_count")
        bucket["metadata"]["observation_count"] = count
        bucket["metadata"]["evidence_count"] = count
        results.append(bucket)
    return results


def _get_semantic_memory(pr: Any, actor_id: str, threshold: float = 0.7) -> list[dict[str, Any]]:
    """Settled, long-term knowledge — deliberately DISTINCT from Beliefs
    (review, round 2, issue 3: "Beliefs = current mental state, Semantic
    Memory = long-term knowledge... those are different concepts"). Two
    real, disjoint sources, merged:
    1. BeliefFusion.confident_beliefs() (kernel/society/belief.py) —
       geography/presence/SharedWorld facts, unrelated to the KG.
    2. This actor's own _grouped_beliefs() facts that have RECURRED
       across 2+ ticks (observation_count >= 2) — a real, principled
       definition of "durable" grounded in actual repetition, not
       fabricated: a fact retrieved only once this session is still
       just this tick's belief, not yet settled long-term knowledge."""
    items: list[dict[str, Any]] = []
    found = _find_actor_state(pr, actor_id)
    belief_state = getattr(found[1], "belief_state", None) if found is not None else None
    if belief_state is not None:
        items.extend(entry.to_dict() for entry in belief_state.confident_beliefs(threshold))

    for belief in _grouped_beliefs(actor_id, 200):
        if belief["confidence"] < threshold or belief["metadata"]["observation_count"] < 2:
            continue
        items.append(
            {
                "subject": belief["subject"],
                "hypotheses": [
                    {
                        "hypothesis_id": belief["entry_id"],
                        "subject": belief["subject"],
                        "predicate": belief["predicate"],
                        "object_value": belief["value"],
                        "confidence": belief["confidence"],
                        "evidence_count": belief["metadata"]["evidence_count"],
                        "sources": belief["metadata"]["evidence"],
                        "last_updated": belief["start_time"],
                    }
                ],
                "last_observation_time": belief["start_time"],
                "staleness": 0.0,
            }
        )
    return items


def _get_episodic_memory(pr: Any, actor_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """This actor's own recorded experiences: real MemoryManager nodes
    (kernel/learn/memory/manager.py, written today from membership-event
    experiences — see kernel/society/membership.py) merged with episodes
    synthesized from this actor's own real, completed/failed PlanRecords
    (a finished task IS an episode — "execution history" and "episodic
    memory" are related but not identical views of the same real data,
    per the Quality Pass review). Plans with no real goal/steps (empty
    autonomous ticks) are skipped, same filter _latest_meaningful_plan
    already uses. No new persistence — purely a read-time merge."""
    memory_manager = getattr(pr, "memory_manager", None)
    nodes = memory_manager.recent_for_actor(actor_id, limit) if memory_manager is not None else []
    episodes = [
        {
            "node_id": node.node_id,
            "kind": node.payload.get("kind", ""),
            "text": node.payload.get("text", ""),
            "timestamp": node.last_accessed,
            "metadata": {k: v for k, v in node.payload.items() if k not in ("actor_id", "kind", "text")},
        }
        for node in nodes
    ]
    for plan in _get_plans(actor_id, limit):
        if not plan.get("steps") or plan.get("status") not in (
            "completed",
            "failed",
            "partial",
        ):
            continue
        episodes.append(
            {
                "node_id": plan.get("plan_id", ""),
                # Cognitive Loop Verification (round 2): PlanRecord.status
                # now genuinely distinguishes "partial" from "completed"
                # (cognitive_actor.py) — this was a binary fallback that
                # collapsed a partial success into "task_failed", contradicting
                # Execution History for the same tick.
                "kind": (
                    "task_completed"
                    if plan.get("status") == "completed"
                    else ("task_partial" if plan.get("status") == "partial" else "task_failed")
                ),
                "text": f"{plan.get('goal', '')} — {plan.get('status', '')}",
                "timestamp": plan.get("start_time", 0),
                "metadata": {
                    "result": plan.get("result", ""),
                    "steps": plan.get("step_descriptions", []),
                },
            }
        )
    episodes.sort(key=lambda e: e.get("timestamp") or 0, reverse=True)
    return episodes[:limit]


def _get_conversation_memory(pr: Any, actor_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Real INTERACTION Context Stream events this actor sent or was a
    named participant in, across every Society it's currently affiliated
    with (permanent or temporary — PlanetaryRuntime.effective_societies).
    No new persistence — same mechanism the Planetary Cycle Animation /
    Society Visualization work already reads for interaction events."""
    society_ids = pr.effective_societies(actor_id)
    seen: set[str] = set()
    events: list[Any] = []
    for society_id in society_ids:
        sr = pr.get_society_runtime(society_id)
        if sr is None:
            continue
        for event in sr.context_stream.events(limit=5000):
            if event.event_type.value != "interaction":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            participants = payload.get("participants") or []
            if event.actor_id != actor_id and actor_id not in participants:
                continue
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            events.append(event)
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return [e.to_dict() for e in events[:limit]]


_TYPE_MAP = {
    "human": ActorType.HUMAN,
    "ai_agent": ActorType.AI_AGENT,
    "robot": ActorType.ROBOT,
    "enterprise": ActorType.ENTERPRISE,
    "government": ActorType.GOVERNMENT,
    "community": ActorType.COMMUNITY,
    "digital_service": ActorType.DIGITAL_SERVICE,
    "device": ActorType.DEVICE,
}


@router.get("/actors", response_model=list[ActorResponse], tags=["Actors"])
async def list_actors(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="list", resource="actor")),
) -> list[ActorResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    results = []
    seen: set[str] = set()
    for sr in pr.all_societies():
        for state in sr.all_actors():
            if state.actor_id in seen:
                continue
            seen.add(state.actor_id)
            societies = list(pr.societies_for_actor(state.actor_id))
            results.append(
                ActorResponse(
                    actor_id=state.actor_id,
                    name=state.profile.identity.name,
                    actor_type=state.profile.identity.actor_type.value,
                    description=state.profile.identity.description,
                    status=state.status.value,
                    cycle_count=state.cycle_count,
                    is_active=state.is_active,
                    societies=societies,
                    goals=list(state.profile.goals),
                    policies=list(state.profile.policies),
                    trust_level=state.profile.trust_level,
                    ownership=state.profile.ownership,
                )
            )
    return results


@router.get("/actors/registry", tags=["Actors"])
async def list_actor_registry(
    request: Request, user_id: str = Depends(require_permission("perm-view-actors"))
) -> list[dict]:
    """Every Actor this PlanetaryRuntime's durable registry knows about,
    across every society and regardless of which node last wrote each
    record — the Control Plane visibility primitive (Deployment
    Architecture, Sections 5-7): "any Execution Node can discover any
    Actor." Distinct from GET /actors above, which only lists actors
    resident in THIS process's memory; this reads the shared Redis
    registry directly, so it also surfaces actors another node registered.
    Registered ahead of GET /actors/{actor_id} below so "registry" is
    never matched as a path parameter."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return [
        {
            "actor_id": e.actor_id,
            "actor_type": e.actor_type,
            "name": e.name,
            "society_id": e.society_id,
            "status": e.status,
            "node_id": e.node_id,
            "updated_at": e.updated_at,
        }
        for e in pr.list_registry()
    ]


@router.post("/actors", response_model=ActorResponse, tags=["Actors"])
@idempotent("actors.create_actor")
async def create_actor(
    body: ActorCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="register", resource="actor")),
) -> ActorResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    actor_type = _TYPE_MAP.get(body.actor_type, ActorType.AI_AGENT)
    caps = tuple(ActorCapability(name=c.get("name", "")) for c in body.capabilities)

    # Get policies from PCP if not specified
    policies = tuple(body.policies)
    if not policies:
        try:
            pcp = getattr(request.app.state, "_pcp", None)
            if pcp:
                roles = await pcp.list_roles()
                # Get all unique permissions from roles as policies
                all_policies = set()
                for role in roles:
                    perms = role.get("permissions", [])
                    for p in perms:
                        if isinstance(p, str):
                            all_policies.add(p)
                policies = tuple(all_policies)
        except Exception:
            logger.debug("PCP role lookup failed, falling back to body.policies", exc_info=True)

    # Get capabilities from execution graph if not specified
    if not caps:
        try:
            execution_graph = getattr(request.app.state, "_execution_graph", None)
            if execution_graph:
                # Extract capability names from execution graph nodes
                nodes = execution_graph.get("nodes", [])
                for node in nodes:
                    cap_name = node.get("capability_name", "") or node.get("name", "")
                    if cap_name:
                        caps = caps + (ActorCapability(name=cap_name),)
        except Exception:
            logger.debug("create_actor: suppressed exception", exc_info=True)

    # If still no capabilities, get from runtime capabilities
    if not caps:
        try:
            runtime = getattr(request.app.state, "runtime", None)
            if runtime and hasattr(runtime, "_capabilities"):
                # Get all registered capabilities (up to 20)
                for cap_name in list(runtime._capabilities.keys())[:20]:
                    caps = caps + (ActorCapability(name=cap_name),)
        except Exception:
            logger.debug("create_actor: suppressed exception", exc_info=True)

    identity = ActorIdentity(name=body.name, actor_type=actor_type, description=body.description)
    profile = ActorProfile(
        identity=identity,
        capabilities=caps,
        goals=tuple(body.goals),
        policies=policies,
        trust_level=body.trust_level,
        ownership=body.ownership,
        objective=body.objective,
        metadata=dict(body.metadata),
    )

    # Identity, not name, is the lookup key: `identity` above already minted
    # a real unique actor_id (uuid4().hex, ActorIdentity's own default
    # factory) before this point ever runs. This used to short-circuit
    # POST /actors on a name+type text match and hand back an unrelated
    # EXISTING actor's state instead of creating the one just requested —
    # two genuinely different actors sharing a display name (e.g. two
    # "Alexandra Rodrigues") would silently collapse into one, and every
    # other route in this file (_find_actor_state) already resolves
    # exclusively by actor_id, never by name, so this was the one path
    # that didn't. There is no client-supplied idempotency key in
    # ActorCreateRequest to legitimately dedupe against, so every call
    # here now really creates a new actor with its own unique id. The
    # society-exists validation stays — only the name-match short-circuit
    # is gone.
    if body.society_id:
        sr = pr.get_society_runtime(body.society_id)
        if sr is None:
            raise HTTPException(status_code=404, detail=f"Society {body.society_id} not found")

    # Registration Entry Points: every path that creates a new Actor must
    # go through PlanetaryRuntime.register_actor() — the single canonical
    # registration workflow that enforces the world invariants (target
    # Society exists and is associated with a Space, Actor has exactly one
    # current Space) — never SocietyRuntime.register_actor() directly,
    # which has no geography/PresenceTimeline awareness at all.
    #
    # World Graph Builder / Cognitive Reasoning separation: an actor
    # created through this REST endpoint must be able to genuinely REASON
    # over the commerce graph other REST endpoints (/products, /orders,
    # ...) build — not simulate every action. Wires the same real,
    # business-capable engine MB-3060 proved works (domains/
    # vertical_router.py::build_runtime_engine() — real capability_bus +
    # LLMPlanner — plus a ContextConstructionEngine so the planning stage
    # actually populates available_capabilities), pointed at THIS
    # PlanetaryRuntime's own knowledge_graph, by default, for every actor
    # — not a one-off test construction. Grounding fix: context_factory now
    # receives this tick's real triggering/goal text from CognitiveActor.
    # _cognitive_tick and relays it into context["question"] — confirmed
    # live this was previously always "", so ProductSelectionCapability
    # (and anything else parsing context["question"]) silently no-opped on
    # every autonomous/reactive tick, not just under-grounded for display.
    try:
        from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
        from src.monkey_brain.kernel.domains import (
            grocery as _grocery_vertical,
        )  # noqa: F401 -- registers "grocery" on import
        from src.monkey_brain.kernel.domains.vertical_router import build_runtime_engine
        from src.monkey_brain.kernel.pipeline.planning.context_engine import (
            ContextConstructionEngine,
        )

        actor_id = identity.actor_id
        # Persist the learned TransitionModel: previously this always
        # started from zero every restart (build_comparison_integrated_
        # runtime's own transition_model=None default) — a real prior
        # model, if one exists for this actor_id, now carries forward.
        # The companion "actor_id -> name" record is what makes the
        # persisted model genuinely inspectable/attributable, since
        # nothing below this layer (CognitiveActor, the pipeline Actor,
        # CognitiveState) otherwise carries a name at all.
        from src.monkey_brain.kernel.pipeline.prediction.persistence import (
            load_transition_model,
            save_actor_meta,
        )

        prior_transition_model = load_transition_model(actor_id)
        # No eager Current Plan preload — see kernel/society/runtime.py's
        # identical registration path for why: Current Plans are now
        # goal-scoped (kernel/pipeline/planning/current_plan_store.py) and
        # lazily loaded per goal_key by _run_decide on first tick, not
        # guessed/preloaded as a single actor-wide record here.
        save_actor_meta(actor_id, body.name)

        engine = build_runtime_engine(
            None,
            name="grocery",
            context_stream=pr.context_stream,
            transition_model=prior_transition_model,
            connectivity_check=getattr(pr, "_connectivity_check", None),
            edge_governance=getattr(pr, "_local_governance", None),
        )
        engine._context_engine = ContextConstructionEngine(
            planetary_runtime=pr,
            knowledge_graph=pr.knowledge_graph,
            memory_manager=pr.memory_manager,
        )
        actor_role = (
            f"{body.name}, whose responsibilities include: {', '.join(body.goals)}" if body.goals else body.name
        )
        wired_actor = CognitiveActor(
            entity_id=actor_id,
            engine=engine,
            name=body.name,
            context_factory=lambda question: {
                "knowledge_graph": pr.knowledge_graph,
                "actor_id": actor_id,
                # Actor Cell Architecture (docs/ACTOR_CELL_ARCHITECTURE.md):
                # same additive key as kernel/society/runtime.py's own
                # context_factory — `wired_actor` is resolved at CALL time,
                # after the assignment below has completed.
                "actor_local_knowledge_graph": wired_actor._knowledge_graph,
                # HeartbeatCapability (kernel/domains/robot.py) reads this
                # -- None here (this route has no robot-deployment
                # concept); `state` is resolved at CALL time, same as
                # `wired_actor` above.
                "ros_adapter": (state.cell.ros_adapter if getattr(state, "cell", None) is not None else None),
                "planetary_runtime": pr,
                # Cognitive Loop Verification: the Observe stage
                # (WorldPollingProvider) needs context["world"] — same
                # real SharedWorld every other real path now supplies
                # (kernel/society/runtime.py's own context_factory).
                "world": pr.world,
                # AskActorCapability's own read-only registry lookups
                # (resolving a target actor NAME to an id) only —
                # never used to invoke another actor's tick in-process,
                # which would risk _tick_lock reentrancy/deadlock (see
                # AskActorCapability's docstring).
                "planetary_runtime": pr,
                "actor_role": actor_role,
                "question": question,
            },
        )
        state = pr.register_actor(profile, actor=wired_actor, society_id=body.society_id or None)
        # Actor Cell (docs/ACTOR_CELL_ARCHITECTURE.md Section B/O): this
        # route passes an already-constructed `actor`, so SocietyRuntime.
        # register_actor's own ActorCell-construction (its `if actor is
        # None` branch) never runs for it -- build one here too, same
        # best-effort, non-fatal pattern.
        try:
            from src.monkey_brain.kernel.actor_identity import mint_actor_cell_identity
            from src.monkey_brain.kernel.society.actor_cell import ActorCell
            from src.monkey_brain.kernel.trusted_auth import get_trusted_auth

            issuer = get_trusted_auth().principal_id or "planetary-runtime"
            credential = mint_actor_cell_identity(actor_id, issuer=issuer)
            state.cell = ActorCell(
                actor_id=actor_id,
                identity=credential,
                actor=wired_actor,
                runtime_state=state,
            )
        except Exception:
            logger.debug(
                "create_actor: ActorCell construction skipped for %s (non-fatal)",
                actor_id,
                exc_info=True,
            )
        # Multi-Actor Execution Handoff: the real per-actor NATS inbox
        # subscription (kernel/domains/grocery.py::subscribe_actor_inbox)
        # is wired centrally in PlanetaryRuntime.register_actor() itself
        # (kernel/society/integration.py) — not duplicated here — since
        # that's the one place that also covers the boot-time actor
        # reload path (every persisted actor, every restart), which
        # never goes through this REST route at all.
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ActorResponse(
        actor_id=state.actor_id,
        name=state.profile.identity.name,
        actor_type=state.profile.identity.actor_type.value,
        description=state.profile.identity.description,
        status=state.status.value,
        societies=[body.society_id] if body.society_id else [],
        goals=list(state.profile.goals),
        policies=list(state.profile.policies),
        trust_level=state.profile.trust_level,
        ownership=state.profile.ownership,
        objective=state.profile.objective,
    )


@router.get("/actors/{actor_id}", response_model=ActorResponse, tags=["Actors"])
async def get_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="get", resource="actor")),
) -> ActorResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    society_ids = list(pr.societies_for_actor(actor_id))
    return ActorResponse(
        actor_id=state.actor_id,
        name=state.profile.identity.name,
        actor_type=state.profile.identity.actor_type.value,
        description=state.profile.identity.description,
        status=state.status.value,
        cycle_count=state.cycle_count,
        is_active=state.is_active,
        societies=society_ids,
        goals=list(state.profile.goals),
        policies=list(state.profile.policies),
        trust_level=state.profile.trust_level,
        ownership=state.profile.ownership,
        objective=state.profile.objective,
    )


@router.get("/actors/{actor_id}/registry", tags=["Actors"])
async def get_actor_registry_entry(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict:
    """Actor Registry lookup (Deployment Architecture, Section 7/8): does
    `actor_id` exist, what lifecycle status was it last recorded in, and
    which node last owned it — without reconstructing the actor's
    cognition. This is the durable-registry read path a scheduler or a
    cross-process AskActor resolver would use; unlike GET /actors/{id}
    above (which requires the actor resident in THIS process's memory),
    this also answers correctly for an actor another node registered."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    entry = pr.locate_actor(actor_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found in registry")
    return {
        "actor_id": entry.actor_id,
        "actor_type": entry.actor_type,
        "name": entry.name,
        "society_id": entry.society_id,
        "status": entry.status,
        "node_id": entry.node_id,
        "updated_at": entry.updated_at,
    }


class ActorLifecycleRequest(BaseModel):
    desired_state: str
    """One of "running" | "suspended" | "terminated" — see
    kernel/society/actor_lifecycle.py::ActorDesiredState."""
    reason: str = ""


@router.post("/actors/{actor_id}/lifecycle", tags=["Actors"])
@idempotent("actors.set_actor_lifecycle")
async def set_actor_lifecycle(
    actor_id: str,
    body: ActorLifecycleRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="lifecycle", resource="actor")),
) -> dict:
    """Actor Lifecycle Controller (Deployment Architecture, Section 5):
    durably records the desired state and immediately attempts one
    reconciliation pass, so a caller sees a best-effort synchronous result
    rather than only "queued for the next background sweep" — but the
    background reconciliation loop (PlanetaryRuntime.start_actor_lifecycle_
    reconciliation) is what actually guarantees eventual consistency if
    this immediate attempt can't complete (e.g. another node currently
    holds the actor's lease, or the actor lives on a different node
    entirely). This route only changes DEPLOYMENT lifecycle — it never
    grants authority; the actor still passes through governance/
    TransitionGate/capability authorization on every real action exactly
    as before (Section 14)."""
    from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    try:
        desired = ActorDesiredState(body.desired_state.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid desired_state {body.desired_state!r} — must be one of "
            f"{[s.value for s in ActorDesiredState]}",
        )
    pr.lifecycle.set_desired_state(actor_id, desired, reason=body.reason)
    result = pr.lifecycle.reconcile(actor_id)
    return {
        "actor_id": actor_id,
        "desired_state": desired.value,
        "action_taken": result.action,
        "succeeded": result.succeeded,
        "observed_before": result.observed_before,
        "reason": result.reason,
    }


@router.get("/actors/{actor_id}/lifecycle", tags=["Actors"])
async def get_actor_lifecycle(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict:
    """Desired vs. observed state (the Kubernetes `kubectl describe pod`
    analog) plus recent lifecycle history — answers "why is it not
    running," "when did it last transition," and "is it recovering"
    (Section 22)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    desired = pr.lifecycle.get_desired_state(actor_id)
    observed = pr.lifecycle.observe(actor_id)
    history = pr.lifecycle.lifecycle_history(actor_id, limit=20)
    return {
        "actor_id": actor_id,
        "desired_state": desired.value,
        "observed": {
            "exists": observed.exists,
            "status": observed.status,
            "node_id": observed.node_id,
            "updated_at": observed.updated_at,
            "is_stale": observed.is_stale,
            "resident_here": observed.resident_here,
            "lease_held": observed.lease_held,
            "desired_node_id": observed.desired_node_id,
        },
        "history": history,
    }


@router.get("/scheduler/nodes", tags=["Scheduler"])
async def list_scheduler_nodes(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict:
    """Every execution node the Actor Scheduler currently knows about
    (health recomputed for heartbeat staleness at read time — the
    `kubectl get nodes` analog). An empty list means this deployment is
    running in unmanaged single-node mode: the Scheduler leaves every
    actor's placement unconstrained rather than declaring them
    unschedulable."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return {"nodes": [n.to_dict() for n in pr.list_nodes()]}


@router.get("/actors/{actor_id}/placement", tags=["Scheduler"])
async def get_actor_placement(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict:
    """This actor's current Scheduler placement: desired node
    (what the Scheduler decided) vs. observed node (where the registry
    last saw it actually running) — the placement analog of GET
    /actors/{id}/lifecycle's desired-vs-observed state split."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    observed = pr.lifecycle.observe(actor_id)
    if not observed.exists:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found in registry")
    requirements = pr.get_actor_placement_requirements(actor_id)
    return {
        "actor_id": actor_id,
        "desired_node_id": observed.desired_node_id,
        "observed_node_id": observed.node_id,
        "requirements": requirements.to_dict(),
    }


class ActorMigrateRequest(BaseModel):
    target_node_id: str | None = None
    """Explicit target node, or omitted to let the Scheduler pick a
    fresh placement (kernel/society/actor_scheduler.py::ActorScheduler.
    migrate_actor)."""


@router.post("/actors/{actor_id}/migrate", tags=["Scheduler"])
@idempotent("actors.migrate_actor")
async def migrate_actor(
    actor_id: str,
    body: ActorMigrateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="lifecycle", resource="actor")),
) -> dict:
    """Deliberate rescheduling (Actor Scheduler spec, Section 14) — safe
    checkpoint-and-restart, never live migration. Only takes local effect
    (checkpoint + suspend) if this actor happens to be resident on the
    node handling this request; otherwise it durably records the new
    desired placement and the actor's own hosting node picks up the
    evacuation on its next reconciliation pass."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    decision = pr.scheduler.migrate_actor(actor_id, target_node_id=body.target_node_id)
    return {
        "actor_id": actor_id,
        "scheduled": decision.scheduled,
        "node_id": decision.node_id,
        "reason": decision.reason,
    }


@router.get("/actors/{actor_id}/societies", tags=["Actors"])
async def get_actor_societies(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[str]:
    """Every society actor_id is a member of (Society as Organizational
    Context refactor) — the real, single source of truth via
    PlanetaryRuntime.societies_for_actor(), replacing the ad-hoc per-route
    scans this file used before real multi-membership existed."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    return list(pr.societies_for_actor(actor_id))


# ── Planning support (Membership as a First-Class Runtime Resource
#    refactor) — governance information for the Context Construction
#    Engine/Contextual Planner (kernel/pipeline/planning/). ────────────────


@router.get("/actors/{actor_id}/active-memberships", tags=["Actors"])
async def get_active_memberships(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    memberships = pr.membership_registry.memberships_for_actor(actor_id)
    return [
        {
            "membership_id": m.membership_id,
            "society_id": m.society_id,
            "team_id": m.team_id,
            "roles": list(m.roles),
            "status": m.status,
            "trust_score": m.trust_score,
            "start_time": m.start_time,
            "end_time": m.end_time,
        }
        for m in memberships
        if m.is_active()
    ]


@router.get("/actors/{actor_id}/effective-policies", tags=["Actors"])
async def get_actor_effective_policies(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Effective policies across every active membership — union of each
    membership's society's resolved policies (kernel/society/membership.py::
    SocietyMembershipRegistry.resolve_policies)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    from src.monkey_brain.api.routes.memberships import dataclasses_to_dict

    policies = []
    for m in pr.membership_registry.memberships_for_actor(actor_id):
        if not m.is_active():
            continue
        governance = pr.governance_for(m.society_id)
        policies.extend(pr.membership_registry.resolve_policies(m.membership_id, governance=governance))
    return [dataclasses_to_dict(p) for p in policies]


@router.get("/actors/{actor_id}/effective-permissions", tags=["Actors"])
async def get_actor_effective_permissions(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[str]:
    """Effective permissions across every active membership, deduped."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    permissions: list[str] = []
    for m in pr.membership_registry.memberships_for_actor(actor_id):
        if not m.is_active():
            continue
        governance = pr.governance_for(m.society_id)
        permissions.extend(pr.membership_registry.resolve_permissions(m.membership_id, governance=governance))
    return list(dict.fromkeys(permissions))


@router.get("/actors/{actor_id}/planning-context/memberships", tags=["Actors"])
async def get_actor_planning_context_memberships(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Membership context for planning — supports the Context Construction
    Engine/Contextual Planner (kernel/pipeline/planning/context_engine.py),
    same information it reads internally via PlanningContext.metadata[
    "active_memberships"], exposed here for direct inspection/debugging."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return {"actor_id": actor_id, "memberships": []}
    memberships = [m for m in pr.membership_registry.memberships_for_actor(actor_id) if m.is_active()]
    result = []
    for m in memberships:
        governance = pr.governance_for(m.society_id)
        result.append(
            {
                "membership_id": m.membership_id,
                "society_id": m.society_id,
                "team_id": m.team_id,
                "roles": list(m.roles),
                "trust_score": m.trust_score,
                "permissions": list(pr.membership_registry.resolve_permissions(m.membership_id, governance=governance)),
            }
        )
    return {"actor_id": actor_id, "memberships": result}


# ── Timeline (Temporal Presence & Actor Timeline Model refactor) ─────────
# Every actor's location/membership/goal/belief/execution/relationship/
# activity history, queryable by time range — append-only, nothing
# overwritten (see kernel/timeline/). ?from=&to= accept epoch seconds or
# an ISO 8601 timestamp.


def _parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        logger.debug("_parse_timestamp: suppressed exception", exc_info=True)
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid timestamp: {value!r}")


@router.get("/actors/{actor_id}/timeline", tags=["Actors"])
async def get_actor_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Replay every timeline entry (all 7 kinds) for actor_id, merged and
    time-ordered — "replay Alice's day."."""
    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    from src.monkey_brain.kernel.timeline.query import TimelineQueryEngine

    entries = TimelineQueryEngine().replay(actor_id, since=from_ts, until=to_ts)
    return [e.to_dict() for e in entries]


@router.post("/actors/{actor_id}/move", tags=["Actors"])
@idempotent("actors.move_actor")
async def move_actor(
    actor_id: str,
    body: ActorMoveRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Record a Presence change. The write path PresenceTimeline enforces
    (no overlap, exactly one open interval) is on
    PlanetaryRuntime.move_actor()/PresenceTimeline itself, not this
    route."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    ok = pr.move_actor(
        actor_id,
        body.space_id,
        activity=body.activity,
        confidence=body.confidence,
        source=body.source,
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"space_id {body.space_id} is not a valid Space")
    current = pr.presence.current(actor_id)
    return current.to_dict() if current else {}


@router.get("/actors/{actor_id}/presence", tags=["Actors"])
async def get_actor_presence(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Presence history, or (with ?at=) the Presence valid at a single
    point in time — "where was Alice yesterday at 3pm."."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    presence = pr.presence
    at_ts = _parse_timestamp(request.query_params.get("at"))
    if at_ts is not None:
        result = presence.at(actor_id, at_ts)
        return result.to_dict() if result else None
    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [p.to_dict() for p in presence.history(actor_id, since=from_ts, until=to_ts)]


@router.get("/actors/{actor_id}/fraud-status", tags=["Actors"])
async def get_actor_fraud_status(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Qualification Gap Closure, Phase 6: a real, read-only view of the
    SAME fraud-risk assessment PaymentConfirmation/Payment already run
    (kernel/domains/finance.py::assess_transaction_risk) — never a
    separate policy, so this can never disagree with what an actual
    checkout attempt would see. Exists so a caller (a real user, or a
    test/qualification harness) can distinguish "the system is broken"
    from "this specific transaction is correctly held by security policy"
    BEFORE attempting a purchase, and — if held on the velocity signal —
    see velocity_cooldown_until, the real timestamp (derived from this
    actor's own persisted order history, not separately tracked state
    that could drift from it) after which legitimate rapid activity
    naturally stops being flagged. ?total= previews a specific amount
    (catches the amount-anomaly signal too); omitted, it reports this
    actor's baseline velocity-only status.
    """
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    kg = getattr(pr, "knowledge_graph", None)
    if kg is None:
        raise HTTPException(status_code=503, detail="no knowledge graph available")

    total_param = request.query_params.get("total")
    try:
        total = float(total_param) if total_param is not None else 0.0
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid total: {total_param!r}")

    from src.monkey_brain.kernel.domains.finance import assess_transaction_risk

    risk = assess_transaction_risk(kg, actor_id, total)
    return {"actor_id": actor_id, **risk}


@router.get("/actors/{actor_id}/goals_timeline", tags=["Actors"])
async def get_actor_goal_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Named goals_timeline (not /goals, already taken by the existing
    current-goals route below) — full GoalRecord history."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [g.to_dict() for g in TimelineStore().query(actor_id, TimelineKind.GOAL, from_ts, to_ts)]


@router.get("/actors/{actor_id}/beliefs_timeline", tags=["Actors"])
async def get_actor_belief_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Named beliefs_timeline (not /beliefs, already taken by the existing
    current-belief-state route below) — full BeliefRecord history."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [b.to_dict() for b in TimelineStore().query(actor_id, TimelineKind.BELIEF, from_ts, to_ts)]


@router.get("/actors/{actor_id}/executions", tags=["Actors"])
async def get_actor_execution_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [e.to_dict() for e in TimelineStore().query(actor_id, TimelineKind.EXECUTION, from_ts, to_ts)]


# ── Cognitive State (Promote Cognitive State to a First-Class Runtime
# Model) ───────────────────────────────────────────────────────────────


@router.get("/actors/{actor_id}/intent", response_model=ActorIntentResponse, tags=["Actors"])
async def get_actor_intent(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorIntentResponse:
    """This actor's most recently classified Intent — real, persisted per
    tick (kernel/compile/cognitive_actor.py), not the transient
    per-request classification other routes discard after use."""
    return ActorIntentResponse(actor_id=actor_id, intent=_get_intent(actor_id))


@router.get("/actors/{actor_id}/plans", response_model=ActorPlansResponse, tags=["Actors"])
async def get_actor_plans(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorPlansResponse:
    """Real Plan history, newest first — plans[0] is the current plan;
    kernel/pipeline/belief_state.py::Plan used to be overwritten every
    tick with nothing kept."""
    limit = int(request.query_params.get("limit", "20"))
    return ActorPlansResponse(actor_id=actor_id, plans=_get_plans(actor_id, limit))


@router.get(
    "/actors/{actor_id}/decisions",
    response_model=ActorDecisionsResponse,
    tags=["Actors"],
)
async def get_actor_decisions(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorDecisionsResponse:
    """Real negotiation/coordination decisions, each with the candidate
    futures actually evaluated (kernel/society/integration.py::
    _build_negotiation_trace) — empty for an actor that hasn't gone
    through that coordination path yet, which is honest, not a gap."""
    limit = int(request.query_params.get("limit", "20"))
    return ActorDecisionsResponse(actor_id=actor_id, decisions=_get_decisions(actor_id, limit))


@router.get(
    "/actors/{actor_id}/execution-history",
    response_model=ActorExecutionHistoryResponse,
    tags=["Actors"],
)
async def get_actor_execution_history(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorExecutionHistoryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    limit = int(request.query_params.get("limit", "50"))
    return ActorExecutionHistoryResponse(actor_id=actor_id, executions=_get_execution_history(pr, actor_id, limit))


# ── Planetary Narrative (compose already-real, already-persisted evidence
# for one tick — never a second source of truth, never LLM-generated) ───


def _find_by_execution_id(actor_id: str, kind: Any, execution_id: str) -> dict[str, Any] | None:
    """The one correlation lookup every narrative endpoint below starts
    with. Scans this actor's own Timeline history for `kind` (newest-first,
    same _timeline_history everything else already uses) for the record
    whose metadata.execution_id matches — no new index, no new store."""
    for record in _timeline_history(actor_id, kind, limit=200):
        if (record.get("metadata") or {}).get("execution_id") == execution_id:
            return record
    return None


def _resolve_execution(actor_id: str, execution_id: str) -> dict[str, Any]:
    """Every narrative route needs the same anchor: the real EXECUTION
    record this execution_id names. 404s the same way for all four
    routes if it's not this actor's."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    execution = _find_by_execution_id(actor_id, TimelineKind.EXECUTION, execution_id)
    if execution is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution {execution_id!r} found for actor {actor_id!r}",
        )
    return execution


def _negotiation_record(actor_id: str, execution_id: str) -> dict[str, Any] | None:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    for record in _timeline_history(actor_id, TimelineKind.DECISION, limit=200):
        meta = record.get("metadata") or {}
        if meta.get("execution_id") == execution_id and meta.get("decision_kind") == "negotiation":
            return record
    return None


_NO_NEGOTIATION_REASON = "No negotiation was required. All participating actors had aligned objectives."

_NO_SCENARIOS_REASON = (
    "No scenario recommendation was recorded for this execution "
    "(empty plan, or the Predict stage produced no candidates)."
)


def _scenario_record(actor_id: str, execution_id: str) -> dict[str, Any] | None:
    """Same shape/precedent as _negotiation_record above -- the real,
    already-durably-persisted TimelineKind.DECISION record every tick's
    Predict stage writes (kernel/compile/cognitive_actor.py, metadata.
    decision_kind == "scenario_recommendation"), not a new persistence
    path."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    for record in _timeline_history(actor_id, TimelineKind.DECISION, limit=200):
        meta = record.get("metadata") or {}
        if meta.get("execution_id") == execution_id and meta.get("decision_kind") == "scenario_recommendation":
            return record
    return None


@router.get("/actors/{actor_id}/executions/{execution_id}/conversation", tags=["Actors"])
async def get_execution_conversation(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Every message this actor's tick actually exchanged (AskActor/
    RespondToInquiry/RecordAgreement — the only capabilities that publish
    an INTERACTION ContextEvent, kernel/domains/grocery.py), filtered to
    the real time window this specific execution ran in. Reuses the exact
    field semantics living-world-explorer/src/components/
    ConversationTimelinePanel.tsx already parses client-side — this just
    scopes it server-side to one execution instead of "all interaction
    events across all societies." Empty, not an error, when nothing was
    exchanged — most executions."""
    from src.monkey_brain.kernel.compile import _obs
    from src.monkey_brain.kernel.society.context_stream import ContextEventType
    import time as _time

    started = _time.monotonic()
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    execution = _resolve_execution(actor_id, execution_id)
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, _ = found

    duration_s = float((execution.get("metadata") or {}).get("duration_ms", 0.0) or 0.0) / 1000.0
    window_start = float(execution.get("start_time") or 0.0) - 2.0
    window_end = float(execution.get("start_time") or 0.0) + duration_s + 2.0

    events = sr.context_stream.replay(event_type=ContextEventType.INTERACTION, actor_id=actor_id)
    messages = [e.to_dict() for e in events if window_start <= e.timestamp <= window_end]

    _obs.counter("narrative.requests", endpoint="conversation")
    _obs.counter("narrative.messages_exchanged", increment=len(messages))
    _obs.gauge("narrative.generation_latency_ms", (_time.monotonic() - started) * 1000)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "message_count": len(messages),
        "messages": messages,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/negotiation", tags=["Actors"])
async def get_execution_negotiation(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The real negotiation trace for this execution
    (kernel/society/integration.py::_build_negotiation_trace), when this
    tick actually invoked EvaluateStrategy/CompeteForResource/AskActor/
    NegotiatePrice/NegotiateTerms/RecordAgreement. Most executions didn't
    — this explicitly says so rather than returning an empty-looking
    negotiation."""
    from src.monkey_brain.kernel.compile import _obs
    import time as _time

    started = _time.monotonic()
    _resolve_execution(actor_id, execution_id)  # 404s if this execution isn't real/isn't this actor's
    record = _negotiation_record(actor_id, execution_id)

    _obs.counter("narrative.requests", endpoint="negotiation")
    _obs.gauge("narrative.generation_latency_ms", (_time.monotonic() - started) * 1000)
    if record is None:
        return {
            "actor_id": actor_id,
            "execution_id": execution_id,
            "negotiation_required": False,
            "reason": _NO_NEGOTIATION_REASON,
        }

    meta = record.get("metadata") or {}
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "negotiation_required": True,
        "candidate_strategies": meta.get("candidate_strategies") or [],
        "utility_evaluation": record.get("candidates") or [],
        "chosen_strategy": record.get("selected_strategy") or "",
        "negotiation_outcome": meta.get("negotiation_outcome"),
        "is_competitive": bool(meta.get("is_competitive")),
        "is_cooperative": bool(meta.get("is_cooperative")),
        "agreement_recorded": bool(meta.get("agreement_recorded")),
        "colleagues_involved": list(record.get("evidence") or ()),
        "reason": record.get("reason") or "",
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/scenarios", tags=["Actors"])
async def get_execution_scenarios(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The real Predict-stage scenario recommendation for this execution
    (kernel/pipeline/prediction/ -- TransitionModel-based simulation,
    counterfactual branching, scenario evaluation), including which
    scenario sources (Baseline + every registered CounterfactualAssumption)
    actually participated (kernel/pipeline/prediction/scenarios.py::
    ScenarioParticipation) -- not just the winning candidate. Every real
    execution has one (the Predict stage runs every tick); this is
    honestly empty only when the plan itself was empty."""
    from src.monkey_brain.kernel.compile import _obs
    import time as _time

    started = _time.monotonic()
    _resolve_execution(actor_id, execution_id)  # 404s if this execution isn't real/isn't this actor's
    record = _scenario_record(actor_id, execution_id)

    _obs.counter("narrative.requests", endpoint="scenarios")
    _obs.gauge("narrative.generation_latency_ms", (_time.monotonic() - started) * 1000)
    if record is None:
        return {
            "actor_id": actor_id,
            "execution_id": execution_id,
            "prediction_available": False,
            "reason": _NO_SCENARIOS_REASON,
        }

    meta = record.get("metadata") or {}
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "prediction_available": True,
        "prediction_id": meta.get("prediction_id") or "",
        "recommendation": record.get("selected_strategy") or "",
        "reason": record.get("reason") or "",
        "confidence": record.get("confidence", 0.0),
        "candidates": record.get("candidates") or [],
        "scenario_participation": meta.get("scenario_participation"),
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/audit-timeline", tags=["Actors"])
async def get_execution_audit_timeline(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Durable audit trail (Production Hardening — durable auditability):
    every PLAN/EXECUTION/DECISION TimelineStore entry whose correlation_id
    is this execution_id, merged chronologically — goal -> plan ->
    validation (generated/invalidated/replaced) -> execution -> capability
    outcomes -> decisions (idempotency replay/conflict, payment
    completion), reconstructed from Redis-backed durable state rather
    than an in-memory/live-only source, and unaffected by process
    restart. Unlike /conversation's window-heuristic match (ContextEvent
    has no execution_id field), this is an exact correlation_id match —
    kernel/pipeline/audit_trail.py is the sole writer for the PLAN/
    DECISION entries this surfaces alongside the EXECUTION entry every
    tick already wrote (belief_runtime.py)."""
    from src.monkey_brain.kernel.compile import _obs
    from src.monkey_brain.kernel.pipeline.audit_trail import query_audit_timeline
    import time as _time

    started = _time.monotonic()
    execution = _resolve_execution(actor_id, execution_id)  # 404s if this execution isn't real/isn't this actor's
    timeline = query_audit_timeline(actor_id, execution_id)

    _obs.counter("narrative.requests", endpoint="audit_timeline")
    _obs.gauge("narrative.generation_latency_ms", (_time.monotonic() - started) * 1000)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "goal": execution.get("goal", ""),
        "outcome": execution.get("outcome", ""),
        "event_count": len(timeline),
        "events": timeline,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/game-theory", tags=["Actors"])
async def get_execution_game_theory(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Reshapes the same real negotiation record /negotiation returns —
    strategies considered, per-participant utilities, and a real (not
    LLM-guessed) equilibrium description built by mapping the record's
    own is_competitive/is_cooperative/agreement_recorded/
    negotiation_outcome fields to plain English, never inventing an
    equilibrium concept the record doesn't actually support."""
    from src.monkey_brain.kernel.compile import _obs
    import time as _time

    started = _time.monotonic()
    _resolve_execution(actor_id, execution_id)
    record = _negotiation_record(actor_id, execution_id)
    _obs.counter("narrative.requests", endpoint="game-theory")
    _obs.gauge("narrative.generation_latency_ms", (_time.monotonic() - started) * 1000)

    if record is None:
        return {
            "actor_id": actor_id,
            "execution_id": execution_id,
            "negotiation_required": False,
            "reason": _NO_NEGOTIATION_REASON,
            "strategies": [],
            "utilities": [],
            "equilibrium": _NO_NEGOTIATION_REASON,
        }

    meta = record.get("metadata") or {}
    utility_evaluation = record.get("candidates") or []
    utilities = [{"participant": c.get("name", ""), "utility": c.get("utility", 0.0)} for c in utility_evaluation]
    if meta.get("agreement_recorded"):
        equilibrium_kind = "cooperative_bargaining"
        equilibrium = "Cooperative bargaining: an agreement was reached and recorded."
    elif meta.get("is_competitive") and meta.get("negotiation_outcome") == "won":
        equilibrium_kind = "competitive"
        equilibrium = "Competitive: this actor's strategy won a resource contention."
    elif meta.get("is_competitive") and meta.get("negotiation_outcome") == "lost":
        equilibrium_kind = "competitive"
        equilibrium = "Competitive: this actor's strategy lost a resource contention to another participant."
    elif meta.get("is_cooperative") and meta.get("negotiation_outcome") == "no_deal":
        equilibrium_kind = "no_deal"
        equilibrium = "No equilibrium reached: cooperative negotiation ended without an agreement."
    elif utility_evaluation:
        equilibrium_kind = "highest_utility"
        equilibrium = (
            "The selected strategy maximized this actor's own expected utility among the candidates evaluated."
        )
    else:
        equilibrium_kind = "unknown"
        equilibrium = (
            "A negotiation-flavored decision was recorded but carries no strategy/outcome data to characterize."
        )

    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "negotiation_required": True,
        "strategies": meta.get("candidate_strategies") or [],
        "utilities": utilities,
        "chosen_strategy": record.get("selected_strategy") or "",
        "equilibrium_kind": equilibrium_kind,
        "equilibrium": equilibrium,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/narrative", tags=["Actors"])
async def get_execution_narrative(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The complete planetary narrative for one execution — every section
    is a direct read of an already-real, already-persisted field (Intent/
    Plan/Decision/Execution Timeline records, plus /conversation and
    /negotiation above); nothing here is LLM-generated. society_coordination
    is honestly reported unavailable for past executions: execution_scope/
    coordination_trace (kernel/society/integration.py::execute_actor_request)
    are computed AFTER this actor's own tick already wrote its Timeline
    records, so they only ever exist in that one live HTTP response, not
    retroactively — see this endpoint's own note field."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.compile import _obs
    import time as _time

    started = _time.monotonic()
    execution = _resolve_execution(actor_id, execution_id)
    plan = _find_by_execution_id(actor_id, TimelineKind.PLAN, execution_id)
    intent = _find_by_execution_id(actor_id, TimelineKind.INTENT, execution_id)
    scenario_decision = None
    for record in _timeline_history(actor_id, TimelineKind.DECISION, limit=200):
        meta = record.get("metadata") or {}
        if meta.get("execution_id") == execution_id and meta.get("decision_kind") == "scenario_recommendation":
            scenario_decision = record
            break

    conversation = await get_execution_conversation(actor_id, execution_id, request, user_id)
    negotiation = await get_execution_negotiation(actor_id, execution_id, request, user_id)

    exec_meta = execution.get("metadata") or {}
    world_changes = exec_meta.get("world_changes") or []
    identity_found = _find_actor_state(_get_planetary_runtime(request), actor_id)
    actor_name = identity_found[1].profile.identity.name if identity_found else actor_id

    what_happened = (
        f'{actor_name} pursued goal "{execution.get("goal", "")}" — outcome: {execution.get("outcome", "")}.'
        + (
            f" World changes: {'; '.join(world_changes)}."
            if world_changes
            else " No persistent world change was recorded."
        )
    )
    why = (
        f"Selected strategy: {scenario_decision.get('selected_strategy', '')}. {scenario_decision.get('reason', '')}"
        if scenario_decision
        else "No scenario/candidate-future evaluation was recorded for this execution."
    )
    who_did_what = {
        "actor": actor_name,
        "capabilities_used": execution.get("capabilities_used") or [],
        "colleagues_involved": (
            negotiation.get("colleagues_involved", []) if negotiation.get("negotiation_required") else []
        ),
    }
    pipeline_stages_run = [
        "Observe",
        "Believe",
        "Intent",
        "Goal",
        "Plan",
        "Predict",
        "Decide",
        "Execute",
        "Observe Result",
        "Compare",
        "Learn",
        "Compile Φ",
        "Commit",
    ]

    _obs.counter("narrative.requests", endpoint="narrative")
    _obs.gauge(
        "narrative.actors_participating",
        float(1 + len(who_did_what["colleagues_involved"])),
    )
    _obs.counter("narrative.world_mutations_explained", increment=len(world_changes))
    latency_ms = (_time.monotonic() - started) * 1000
    _obs.gauge("narrative.generation_latency_ms", latency_ms)

    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "what_happened": what_happened,
        "why": why,
        "who_did_what": who_did_what,
        "how": {"pipeline_stages": pipeline_stages_run, "intent": intent, "plan": plan},
        "conversation": conversation,
        "negotiation": negotiation,
        "society_coordination": {
            "available": False,
            "reason": (
                "Society coordination scope (societies/actors coordinated, "
                "propagation) is computed after this actor's own tick "
                "completes and is only present in that tick's live "
                "/prompt HTTP response (actor_execution.execution_scope), "
                "not retroactively persisted — this is an honest gap, not "
                "a fabricated zero."
            ),
        },
        "world_changes": world_changes,
        "learning": {
            "learned": bool(exec_meta.get("world_changes")),
            "duration_ms": exec_meta.get("duration_ms"),
        },
        "generation_latency_ms": round(latency_ms, 2),
    }


# ── Context Grounding (what the planner actually knew, and where it came
# from — kernel/pipeline/planning/context_snapshot_store.py) ────────────


def _resolve_snapshot(actor_id: str, execution_id: str) -> Any:
    """Every grounding route below starts here. Real, scoped to this
    actor — a snapshot that exists but belongs to a different actor 404s
    the same as one that doesn't exist at all, since execution_id alone
    isn't guaranteed globally unique across actors in principle (it is in
    practice, uuid4, but this endpoint shouldn't rely on that)."""
    from src.monkey_brain.kernel.pipeline.planning.context_snapshot_store import (
        load_context_snapshot,
    )

    snapshot = load_context_snapshot(execution_id)
    if snapshot is None or snapshot.actor_id != actor_id:
        raise HTTPException(
            status_code=404,
            detail=f"No planning context {execution_id!r} found for actor {actor_id!r}",
        )
    return snapshot


@router.get("/actors/{actor_id}/executions/{execution_id}/planning-context", tags=["Actors"])
async def get_execution_planning_context(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The complete real PlanningContext this tick's plan was grounded
    in — kernel/pipeline/planning/context_engine.py::
    ContextConstructionEngine.build()'s actual output, persisted verbatim
    (kernel/pipeline/belief_runtime.py::_generate_plan), not
    reconstructed or summarized by an LLM."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    return snapshot.to_dict()


@router.get("/actors/{actor_id}/executions/{execution_id}/grounding", tags=["Actors"])
async def get_execution_grounding(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The same snapshot, regrouped under the spec's named grounding
    sections — no new retrieval, just a different view of one real
    object."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "summary": snapshot.summary,
        "knowledge_graph": {
            "entities": snapshot.knowledge,
            "relationships": snapshot.relationships,
        },
        "context_stream": snapshot.context_events,
        "semantic_memory": {
            "experiences": snapshot.experiences,
            "conversations": snapshot.conversations,
        },
        "world_state": {
            "locations": snapshot.relevant_locations,
            "objects": snapshot.relevant_objects,
        },
        "available_capabilities": snapshot.available_capabilities,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/context-stream", tags=["Actors"])
async def get_execution_context_stream(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Every real ContextEvent that grounded this tick's planning — the
    fix for this codebase's own previously-acknowledged gap (build()
    never queried ContextStream); see ContextConstructionEngine.
    _retrieve_context_stream()."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "events": snapshot.context_events,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/knowledge-graph", tags=["Actors"])
async def get_execution_knowledge_graph(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """The real KG entities/relationships the planner retrieved and
    reasoned over for this tick (relevant_knowledge/relevant_relationships)."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "entities": snapshot.knowledge,
        "relationships": snapshot.relationships,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/semantic-memory", tags=["Actors"])
async def get_execution_semantic_memory(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Two real memory sources, named for what they actually are (this
    codebase has no "SittingFace semantic memory" of customer
    preferences — confirmed by search): (1) experiences/conversations
    retrieved by this tick's episodic search (kernel/learn/memory
    CognitiveMemory, per-execution — what was actually retrieved for
    THIS plan), and (2) this actor's current durable beliefs — facts
    observed 2+ times (kernel/api/routes/actors.py::_grouped_beliefs,
    the real analog of "settled long-term knowledge" — current state,
    not scoped to one execution)."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    durable_beliefs = [
        b
        for b in _grouped_beliefs(actor_id, 200)
        if int((b.get("metadata") or {}).get("observation_count", 0) or 0) >= 2
    ]
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "retrieved_this_execution": {
            "experiences": snapshot.experiences,
            "conversations": snapshot.conversations,
        },
        "durable_beliefs": durable_beliefs,
    }


def _format_execution_chat_context(context: dict[str, Any]) -> str:
    """Renders the same context blob the Execution Debugger UI is
    already displaying into a compact, labeled text block for the LLM.
    Every line carries a [ref=...] tag pulled straight from the item's
    own evidence_ids/subject/id — the model is instructed to cite these
    verbatim, so a citation the frontend renders as a clickable chip
    always points at something that was genuinely fed in, never a
    fabricated reference."""

    def fmt_items(items: Any) -> list[str]:
        lines = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            refs = it.get("evidence_ids") or []
            ref = refs[0] if refs else ""
            extra = [f"{k}={it[k]}" for k in ("source", "reason", "outcome") if it.get(k)]
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"- [ref={ref or 'n/a'}] {it.get('content', '')}{suffix}")
        return lines

    sections: list[str] = []
    knowledge = context.get("knowledge") or []
    if knowledge:
        sections.append(f"KNOWLEDGE GRAPH ENTITIES ({len(knowledge)}):\n" + "\n".join(fmt_items(knowledge)))
    relationships = context.get("relationships") or []
    if relationships:
        sections.append(f"RELATIONSHIPS TRAVERSED ({len(relationships)}):\n" + "\n".join(fmt_items(relationships)))
    beliefs = [b for b in (context.get("durable_beliefs") or []) if isinstance(b, dict)]
    if beliefs:
        lines = [
            f"- [ref={b.get('subject', '')}] {b.get('subject', '')}: {b.get('value', '')} "
            f"(confidence {b.get('confidence', 0)}, source {b.get('source', 'unknown')})"
            for b in beliefs
        ]
        sections.append(f"SEMANTIC MEMORY / DURABLE BELIEFS ({len(beliefs)}):\n" + "\n".join(lines))
    experiences = context.get("experiences") or []
    if experiences:
        sections.append(f"PRIOR EXPERIENCES ({len(experiences)}):\n" + "\n".join(fmt_items(experiences)))
    conversations = [c for c in (context.get("conversations") or []) if isinstance(c, dict)]
    if conversations:
        lines = [f"- {c.get('source', '?')}: {c.get('content', '')}" for c in conversations]
        sections.append(f"CONVERSATIONS ({len(conversations)}):\n" + "\n".join(lines))
    executions = context.get("executions") or []
    if executions:
        sections.append(f"PRIOR EXECUTIONS REFERENCED ({len(executions)}):\n" + "\n".join(fmt_items(executions)))
    events = context.get("context_events") or []
    if events:
        sections.append(f"CONTEXT EVENTS ({len(events)}):\n" + "\n".join(fmt_items(events)))
    locations = context.get("relevant_locations") or []
    objects = context.get("relevant_objects") or []
    if locations or objects:
        sections.append(
            "WORLD STATE:\nLocations: "
            + (", ".join(locations) or "none")
            + "\nObjects: "
            + (", ".join(objects) or "none")
        )
    affiliations = [
        a for a in (context.get("affiliation_chain") or context.get("affiliations") or []) if isinstance(a, dict)
    ]
    if affiliations:
        lines = [
            (
                f"- [ref={a.get('id', '')}] {a.get('name', '')} ({a.get('entityType', '')})"
                if "name" in a
                else f"- {a.get('society_name', a.get('society_id', ''))}"
            )
            for a in affiliations
        ]
        sections.append(f"AFFILIATIONS ({len(lines)}):\n" + "\n".join(lines))
    diff = context.get("diff_from_previous")
    if isinstance(diff, dict) and not diff.get("is_first_context"):
        added, removed = diff.get("added") or {}, diff.get("removed") or {}
        parts = [
            f"{key}: +{len(added.get(key) or [])}/-{len(removed.get(key) or [])}"
            for key in (
                "knowledge",
                "relationships",
                "context_events",
                "experiences",
                "conversations",
                "executions",
            )
            if (added.get(key) or removed.get(key))
        ]
        if parts:
            sections.append("CONTEXT DIFF vs previous planning cycle:\n" + ", ".join(parts))
    causal = [c for c in (context.get("causal_chain") or []) if isinstance(c, dict)]
    if causal:
        sections.append(
            "CAUSAL CHAIN (world change -> execution):\n"
            + "\n".join(f"- {c.get('label', '')}: {c.get('detail', '')}" for c in causal)
        )
    return "\n\n".join(sections) if sections else "(no grounding data was retrieved for this execution)"


@router.post(
    "/actors/{actor_id}/executions/{execution_id}/chat",
    response_model=ExecutionChatResponse,
    tags=["Actors"],
)
@idempotent("actors.execution_chat")
async def execution_chat(
    actor_id: str,
    execution_id: str,
    body: ExecutionChatRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ExecutionChatResponse:
    """The Execution Debugger's copilot. One real LLM call
    (get_backend().complete() — the same call AnswerQuestionCapability
    in kernel/domains/grocery.py uses, never a template) reasoning ONLY
    over the grounding context the frontend already fetched and is
    rendering. `body.context` is that same context, sent as-is rather
    than re-derived server-side by execution_id — this both guarantees
    the chat can never disagree with what's on screen, and lets the
    client-side-only demo execution (no real backend record) use the
    identical endpoint."""
    from src.monkey_brain.kernel.execute.provider.model_backend import get_backend
    import json as _json

    context_text = _format_execution_chat_context(body.context)
    history_text = (
        "\n".join(f"{m.role}: {m.content}" for m in body.history[-8:])
        if body.history
        else "(no prior messages this session)"
    )
    selected_text = (
        f"\nThe operator currently has this selected in the debugger: {body.selected_context}\n"
        if body.selected_context
        else ""
    )

    system = (
        "You are the CognitiveOS Execution Debugger's assistant — a forensic copilot "
        "explaining ONE specific execution to an operator.\n\n"
        "RULE: if the operator's message is just a greeting or short remark with no "
        'real question in it (examples: "hi", "hello", "thanks", "are you '
        'there") — reply with ONE short, natural sentence back (e.g. a greeting), '
        "nothing else. Do not mention the execution, do not pull in grounding facts, "
        "do not explain that it was a greeting — just reply naturally, the same way "
        "any assistant would.\n\n"
        "Otherwise (a real question): answer using ONLY the GROUNDING CONTEXT below; "
        "if something isn't in it, say so explicitly rather than guessing or "
        "inventing a fact. Do not default to summarizing the execution's outcome just "
        "because that's what the context contains — answer what was actually asked. "
        "Be concise (1-5 sentences). No markdown headers or bullet points.\n\n"
        "Never state or explain which of the two cases above you picked — just give "
        "the final reply.\n\n"
        "After your reply, on a new line, output exactly one block starting with "
        "'EVIDENCE:' followed by a JSON array of the specific context items you "
        "actually relied on (empty for a greeting/remark), each shaped as "
        '{"type": one of knowledge|relationship|experience|conversation|execution|'
        'context_event|belief|affiliation|world_state, "label": a short human label, '
        '"ref": the ref value from that item\'s [ref=...] tag, or "" if it had none}. '
        "If you used nothing from the context, output EVIDENCE: []."
    )
    prompt = (
        f"EXECUTION: {body.execution_id}\nACTOR: {body.actor_name or body.actor_id}\n"
        f"GOAL: {body.goal or 'not recorded'}\nOUTCOME: {body.status or 'unknown'}\n"
        f"{selected_text}\n"
        f"GROUNDING CONTEXT:\n{context_text}\n\n"
        f"CONVERSATION SO FAR:\n{history_text}\n\n"
        f'Operator asks: "{body.question}"\n\nYour answer:'
    )

    raw = (await get_backend().complete(prompt, system=system)).strip()

    answer = raw
    evidence: list[ExecutionChatEvidence] = []
    marker = raw.rfind("EVIDENCE:")
    if marker != -1:
        answer = raw[:marker].strip()
        tail = raw[marker + len("EVIDENCE:") :].strip().strip("`").strip()
        if tail.lower().startswith("json"):
            tail = tail[4:].strip()
        try:
            parsed = _json.loads(tail)
            if isinstance(parsed, list):
                for e in parsed:
                    if isinstance(e, dict) and e.get("type") and e.get("label"):
                        evidence.append(
                            ExecutionChatEvidence(
                                type=str(e["type"]),
                                label=str(e["label"]),
                                ref=str(e.get("ref", "")),
                            )
                        )
        except (ValueError, TypeError):
            pass  # model didn't follow the EVIDENCE: convention exactly — degrade to answer-only, don't crash
    if not answer:
        answer = raw

    # Real gap this closes: the Assistant's own Q&A was never written
    # anywhere — only in this one request/response pair and whatever the
    # frontend kept in React state, gone on refresh, invisible to future
    # grounding. Persisted as kind="conversation" (the same bucket
    # ask_actor already uses, and _bucket_memory_nodes already surfaces
    # as "Conversations", never "Durable Beliefs"/"Experiences") — never
    # promoted to a durable belief on its own; that stays a separate,
    # real learning decision elsewhere in the pipeline, not something
    # this endpoint invents. Best-effort and actor-scoped: the demo
    # execution's fictional actor_id has no real PlanetaryRuntime actor
    # to persist against, so this silently no-ops for it rather than
    # writing an orphan record no real grounding will ever read.
    pr = _get_planetary_runtime(request)
    if pr is not None and _find_actor_state(pr, actor_id) is not None:
        try:
            timestamp = time.time()
            pr.memory_manager.record_experience(
                actor_id,
                kind="conversation",
                text=body.question,
                metadata={
                    "timestamp": timestamp,
                    "speaker": "operator",
                    "execution_id": execution_id,
                },
            )
            pr.memory_manager.record_experience(
                actor_id,
                kind="conversation",
                text=answer,
                metadata={
                    "timestamp": timestamp,
                    "speaker": "CognitiveOS Assistant",
                    "execution_id": execution_id,
                },
            )
        except Exception:
            logger.exception(
                "execution_chat: record_experience(kind=conversation) failed for actor %s — the answer above already succeeded",
                actor_id,
            )

    return ExecutionChatResponse(answer=answer, evidence=evidence)


@router.get("/actors/{actor_id}/executions/{execution_id}/external-events", tags=["Actors"])
async def get_execution_external_events(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """Real, operator-reported world perturbations (POST /planet/
    perturbations -> ReportWorldPerturbationCapability) that were part of
    this tick's grounding — genuinely empty, not a fabricated placeholder,
    when none were ever reported in this actor's context window."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    external = [e for e in snapshot.context_events if e.get("item_type") == "external_perturbation"]
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "event_count": len(external),
        "events": external,
    }


@router.get("/actors/{actor_id}/executions/{execution_id}/context-diff", tags=["Actors"])
async def get_execution_context_diff(
    actor_id: str,
    execution_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """What changed in the planner's grounding since this actor's
    previous planning cycle — computed once, at save time
    (kernel/pipeline/planning/context_snapshot_store.py::diff_snapshots),
    not recomputed here."""
    snapshot = _resolve_snapshot(actor_id, execution_id)
    return {
        "actor_id": actor_id,
        "execution_id": execution_id,
        "diff": snapshot.diff_from_previous or {"is_first_context": True, "added": {}, "removed": {}},
    }


@router.get(
    "/actors/{actor_id}/memory/semantic",
    response_model=ActorMemoryCategoryResponse,
    tags=["Actors"],
)
async def get_actor_semantic_memory(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorMemoryCategoryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return ActorMemoryCategoryResponse(
        actor_id=actor_id,
        category="semantic",
        items=_get_semantic_memory(pr, actor_id),
    )


@router.get(
    "/actors/{actor_id}/memory/episodic",
    response_model=ActorMemoryCategoryResponse,
    tags=["Actors"],
)
async def get_actor_episodic_memory(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorMemoryCategoryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    limit = int(request.query_params.get("limit", "50"))
    return ActorMemoryCategoryResponse(
        actor_id=actor_id,
        category="episodic",
        items=_get_episodic_memory(pr, actor_id, limit),
    )


@router.get(
    "/actors/{actor_id}/memory/conversation",
    response_model=ActorMemoryCategoryResponse,
    tags=["Actors"],
)
async def get_actor_conversation_memory(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorMemoryCategoryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    limit = int(request.query_params.get("limit", "50"))
    return ActorMemoryCategoryResponse(
        actor_id=actor_id,
        category="conversation",
        items=_get_conversation_memory(pr, actor_id, limit),
    )


@router.get(
    "/actors/{actor_id}/cognitive-state",
    response_model=ActorCognitiveStateResponse,
    tags=["Actors"],
)
async def get_actor_cognitive_state(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
) -> ActorCognitiveStateResponse:
    """The sprint's own 'New Actor State Model' document structure in one
    response — composes the exact same helpers every granular route above
    already uses, so the Living World Explorer's Actor Inspector can
    render the whole cognitive debugger from a single fetch instead of
    ~10. Every section degrades to an empty/None value (never fabricated)
    when this actor genuinely has nothing there yet."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found

    identity = {
        "actor_id": actor_id,
        "name": state.profile.identity.name,
        "actor_type": state.profile.identity.actor_type.value,
        "description": state.profile.identity.description,
        "status": state.status.value,
        "is_active": state.is_active,
        "cycle_count": state.cycle_count,
    }
    presence = pr.presence.current(actor_id)
    affiliations = state.actor_runtime.affiliations if state.actor_runtime is not None else None
    affiliations_out = [
        {
            "affiliation_id": a.affiliation_id,
            "affiliation_type": a.affiliation_type,
            "target_id": a.target_id,
            "target_name": a.target_name,
            "trust_level": a.trust_level,
            "category": a.category,
        }
        for a in (affiliations.all() if affiliations is not None else [])
    ]
    current_society = {"society_id": sr.society.society_id, "name": sr.society.name} if sr is not None else None

    from src.monkey_brain.kernel.timeline.entry import TimelineKind

    goals = _timeline_history(actor_id, TimelineKind.GOAL, 20)
    beliefs = _grouped_beliefs(actor_id, 200)

    plans = _get_plans(actor_id, 20)
    decisions = _get_decisions(actor_id, 20)

    return ActorCognitiveStateResponse(
        actor_id=actor_id,
        identity=identity,
        presence=presence.to_dict() if presence else None,
        affiliations=affiliations_out,
        current_society=current_society,
        intent=_get_intent(actor_id),
        goals=_active_goals(goals),
        goal_history=goals,
        goal_executions=_goal_executions(plans),
        beliefs=beliefs,
        current_plan=_get_current_plan(actor_id, plans),
        plan_history=plans,
        decision=decisions[0] if decisions else None,
        decision_history=decisions,
        plan_decision=_get_plan_decision(decisions),
        memory_semantic=_get_semantic_memory(pr, actor_id),
        memory_episodic=_get_episodic_memory(pr, actor_id, 50),
        memory_conversation=_get_conversation_memory(pr, actor_id, 50),
        execution_history=_get_execution_history(pr, actor_id, 50),
    )


@router.get("/actors/{actor_id}/activities", tags=["Actors"])
async def get_actor_activity_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [a.to_dict() for a in TimelineStore().query(actor_id, TimelineKind.ACTIVITY, from_ts, to_ts)]


@router.get("/actors/{actor_id}/memberships_timeline", tags=["Actors"])
async def get_actor_membership_timeline(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Named memberships_timeline (not /memberships, a separate router in
    api/routes/memberships.py) — full MembershipRecord history, including
    closed/past memberships (kernel/society/membership.py::
    SocietyMembershipRegistry.history_for_actor)."""
    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    from_ts = _parse_timestamp(request.query_params.get("from"))
    to_ts = _parse_timestamp(request.query_params.get("to"))
    return [m.to_dict() for m in TimelineStore().query(actor_id, TimelineKind.MEMBERSHIP, from_ts, to_ts)]


@router.post("/actors/{actor_id}/delegations", tags=["Actors"])
@idempotent("actors.create_delegation")
async def create_delegation(
    actor_id: str,
    body: dict[str, Any],
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Grants body.delegate_id real authority to act on actor_id's own
    behalf — "buy milk on behalf of Priya" only ever succeeds because a
    real, active grant like THIS one exists (DelegationCheckCapability,
    kernel/domains/grocery.py). Real gap this closes: kernel/domains/
    domain_security.py::grant_delegation had zero callers anywhere in
    src/ or tests/ — no real path ever created the grant that capability
    checks for, so an "on behalf of X" request was denied unconditionally,
    every time. Written into the SAME Neo4j tenant partition
    check_delegation itself reads from (its own docstring: "written into
    the DELEGATOR's own tenant partition") — writing into the shared
    commerce KG instead would be invisible to that lookup."""
    delegate_id = body.get("delegate_id")
    if not delegate_id:
        raise HTTPException(status_code=400, detail="body.delegate_id is required")
    try:
        hours = float(body.get("hours", 24))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="body.hours must be a real number")
    if hours <= 0:
        raise HTTPException(status_code=400, detail="body.hours must be positive")

    import time
    from src.monkey_brain.kernel.knowledge_graph_neo4j import (
        Neo4jBackedKnowledgeGraph,
        resolve_household_id,
    )
    from src.monkey_brain.kernel.domains.domain_security import grant_delegation

    delegator_kg = Neo4jBackedKnowledgeGraph(person_id=actor_id, household_id=resolve_household_id(actor_id))
    delegation_id = grant_delegation(delegator_kg, actor_id, delegate_id, expires_at=time.time() + hours * 3600)
    return {
        "success": True,
        "delegation_id": delegation_id,
        "delegator_id": actor_id,
        "delegate_id": delegate_id,
        "expires_in_hours": hours,
    }


@router.delete("/actors/{actor_id}/delegations/{delegate_id}", tags=["Actors"])
@idempotent("actors.revoke_delegation_route")
async def revoke_delegation_route(
    actor_id: str,
    delegate_id: str,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Real, immediate revocation — kernel/domains/domain_security.py::
    revoke_delegation, same Neo4j tenant partition create_delegation
    above writes into. A later check_delegation call sees this
    immediately (kg.refresh()), not just at the end of whatever request
    happened to already be in flight."""
    from src.monkey_brain.kernel.knowledge_graph_neo4j import (
        Neo4jBackedKnowledgeGraph,
        resolve_household_id,
    )
    from src.monkey_brain.kernel.domains.domain_security import revoke_delegation

    delegator_kg = Neo4jBackedKnowledgeGraph(person_id=actor_id, household_id=resolve_household_id(actor_id))
    revoked = revoke_delegation(delegator_kg, actor_id, delegate_id)
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail=f"no active delegation from {actor_id!r} to {delegate_id!r}",
        )
    return {"success": True, "delegator_id": actor_id, "delegate_id": delegate_id}


@router.delete("/actors/{actor_id}", tags=["Actors"])
@audited("actors.delete")
@idempotent("actors.delete_actor")
async def delete_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    # Full deletion (unlike leave_society/DELETE /memberships, which only
    # detach one organizational link): unregister cognition from wherever
    # it lives, then clean up every remaining organizational membership so
    # no dangling SocietyMembershipRegistry record points at a deleted
    # actor. PlanetaryRuntime.unregister_actor() now does the "search
    # every managed society" work this route used to do inline (the same
    # fix also gave it a checkpoint-before-terminate step this route never
    # had — belief was previously discarded unflushed on every DELETE).
    memberships = pr.societies_for_actor(actor_id)
    deleted = pr.unregister_actor(actor_id)
    for society_id in memberships:
        pr.leave_society(actor_id, society_id)
    if deleted:
        return {"status": "deleted", "actor_id": actor_id}
    raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")


@router.patch("/actors/{actor_id}", response_model=ActorResponse, tags=["Actors"])
@idempotent("actors.update_actor")
async def update_actor(
    actor_id: str,
    body: ActorUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> ActorResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    p = state.profile
    if body.name is not None:
        new_identity = dataclasses.replace(p.identity, name=body.name)
        p = dataclasses.replace(p, identity=new_identity)
    if body.actor_type is not None:
        if body.actor_type not in _TYPE_MAP:
            raise HTTPException(status_code=400, detail=f"invalid actor_type: {body.actor_type!r}")
        new_identity = dataclasses.replace(p.identity, actor_type=_TYPE_MAP[body.actor_type])
        p = dataclasses.replace(p, identity=new_identity)
    if body.description is not None:
        new_identity = dataclasses.replace(p.identity, description=body.description)
        p = dataclasses.replace(p, identity=new_identity)
    if body.capabilities is not None:
        caps = tuple(ActorCapability(name=c.get("name", "")) for c in body.capabilities)
        p = dataclasses.replace(p, capabilities=caps)
    if body.goals is not None:
        p = dataclasses.replace(p, goals=tuple(body.goals))
        if state.actor_runtime is not None and body.goals:
            state.actor_runtime.set_goal(body.goals[0])
    if body.policies is not None:
        p = dataclasses.replace(p, policies=tuple(body.policies))
    if body.trust_level is not None:
        p = dataclasses.replace(p, trust_level=body.trust_level)
    if body.ownership is not None:
        p = dataclasses.replace(p, ownership=body.ownership)
    if body.objective is not None:
        p = dataclasses.replace(p, objective=body.objective)
        if state.actor_runtime is not None:
            state.actor_runtime.set_objective(body.objective)
    if body.metadata is not None:
        merged_metadata = {**p.metadata, **body.metadata}
        p = dataclasses.replace(p, metadata=merged_metadata)
    state.profile = p
    pr._save_actors()
    return ActorResponse(
        actor_id=state.actor_id,
        name=state.profile.identity.name,
        actor_type=state.profile.identity.actor_type.value,
        description=state.profile.identity.description,
        status=state.status.value,
        cycle_count=state.cycle_count,
        is_active=state.is_active,
        goals=list(state.profile.goals),
        policies=list(state.profile.policies),
        trust_level=state.profile.trust_level,
        ownership=state.profile.ownership,
    )


@router.get("/actors/{actor_id}/beliefs", response_model=ActorBeliefsResponse, tags=["Actors"])
async def get_actor_beliefs(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorBeliefsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    # kernel/pipeline/belief_state.py::BeliefState is canonical -- the
    # representation CognitiveRuntime.tick() (the real /prompt path)
    # actually reads/writes on every real request, held live on
    # state.actor (the CognitiveActor instance) via .pipeline_belief().
    # state.belief_state (ActorRuntimeState's own field, the OLDER
    # kernel/society/belief.py::BeliefState) is only ever written by
    # POST /actors/{id}/observe and SocietyRuntime's own coordinated-tick
    # path -- /prompt never touches it, so it silently never reflected a
    # real tick's actual belief content. Fall back to it only for the
    # rare non-CognitiveActor-family actor (see ActorRuntime.__init__'s
    # own "non-CognitiveActor-family `actor`" comment in
    # kernel/society/runtime.py), which has no pipeline_belief() at all.
    pipeline_belief = getattr(state.actor, "pipeline_belief", None)
    if callable(pipeline_belief):
        beliefs = pipeline_belief().to_dict()
    else:
        beliefs = serialize_beliefs(state.belief_state)
    return ActorBeliefsResponse(actor_id=actor_id, beliefs=beliefs)


@router.get("/actors/{actor_id}/memory", response_model=ActorMemoryResponse, tags=["Actors"])
async def get_actor_memory(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorMemoryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    memory = []
    if state.actor_runtime is not None:
        memory = state.actor_runtime.memory_snapshot(100)
    return ActorMemoryResponse(actor_id=actor_id, memory=memory)


@router.get("/actors/{actor_id}/goals", response_model=ActorGoalsResponse, tags=["Actors"])
async def get_actor_goals(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorGoalsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    goals = list(state.profile.goals) if state.profile.goals else []
    return ActorGoalsResponse(actor_id=actor_id, goals=goals)


@router.post("/actors/{actor_id}/goals", response_model=ActorGoalsResponse, tags=["Actors"])
@idempotent("actors.add_actor_goal")
async def add_actor_goal(
    actor_id: str,
    body: ActorAddGoalRequest,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> ActorGoalsResponse:
    """Queues a real, persistent goal via CognitiveActor.add_goal()
    (kernel/compile/cognitive_actor.py) — a correct, priority-ordered
    goal queue that already existed but had zero real callers anywhere
    in this codebase before this route (confirmed via grep): every
    goal an actor pursues came only from its initial seed profile or
    from PATCH /actors/{id} replacing the whole goal list wholesale.
    add_goal() itself is the real mechanism (dedup by goal text,
    re-selects _current_goal immediately so a higher-priority goal
    genuinely preempts on the very next tick) — this route is the
    thin, permission-gated adapter exposing it, mirroring PATCH's own
    goals-plus-_save_actors persistence pattern above so a newly added
    goal survives a server restart, not just this process's lifetime.

    Also calls SocietyRuntime.tick_one_actor() right after add_goal(),
    exactly like VoiceCommandRuntime._handle_transcript() does for a
    spoken command (kernel/edge/voice_command_runtime.py) — confirmed
    live that without this, a goal queued here just sits there: nothing
    else in this deployment ticks a drone actor on its own cadence, so
    add_goal() alone queues a mission that never actually flies until
    something else happens to tick that actor."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    goal_text = body.goal.strip()
    existing_goals = list(state.profile.goals) if state.profile.goals else []
    changed = False
    replace_text = body.replace_goal.strip() if body.replace_goal else None
    if replace_text and replace_text != goal_text:
        if state.actor_runtime is not None:
            state.actor_runtime.remove_goal(replace_text)
        if replace_text in existing_goals:
            existing_goals.remove(replace_text)
            changed = True
    if state.actor_runtime is not None:
        state.actor_runtime.add_goal(goal_text, priority=body.priority)
    if goal_text not in existing_goals:
        existing_goals.append(goal_text)
        changed = True
    if changed:
        state.profile = dataclasses.replace(state.profile, goals=tuple(existing_goals))
        pr._save_actors()
    if state.actor_runtime is not None:
        await sr.tick_one_actor(actor_id)
    return ActorGoalsResponse(actor_id=actor_id, goals=existing_goals)


class ActorPromptRequest(BaseModel):
    question: str


@router.post("/actors/{actor_id}/prompt", tags=["Actors"])
@idempotent("actors.prompt_actor")
async def prompt_actor(
    actor_id: str,
    body: ActorPromptRequest,
    user_id: str = Depends(require_self_or_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> dict[str, Any]:
    """Sends a fresh natural-language mission straight to actor_id's own
    dedicated actor Pod (actor_runtime.py's own POST /prompt,
    kernel/edge/actor_prompt_forwarder.py) — the real front door for "give
    this actor a new mission right now", same call
    VoiceCommandRuntime._handle_transcript() now makes for a spoken
    command (kernel/edge/voice_command_runtime.py), just reachable with
    typed/edited text. Deliberately NOT the same mechanism POST
    /actors/{id}/goals above uses (add_goal()+tick_one_actor() against
    THIS process's own PlanetaryRuntime) — for a robot-class actor like a
    drone, that process is a separate dedicated Pod with its own
    ROS_ADAPTER_KIND/ROS_BRIDGE_URL bindings this shared control-plane
    process doesn't have, so only that Pod's own PlanetaryRuntime can
    actually fly it (see actor_prompt_forwarder.py's own module
    docstring)."""
    from src.monkey_brain.kernel.edge.actor_prompt_forwarder import (
        ActorPromptForwardError,
        forward_prompt_to_actor_pod,
    )

    try:
        result = await forward_prompt_to_actor_pod(actor_id, body.question)
    except ActorPromptForwardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "actor_id": actor_id,
        "question": body.question,
        "goal_achieved": result.get("goal_achieved"),
        "actions": result.get("actions") or [],
        "plan": result.get("plan"),
    }


@router.get(
    "/actors/{actor_id}/affiliations",
    response_model=ActorAffiliationsResponse,
    tags=["Actors"],
)
async def get_actor_affiliations(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorAffiliationsResponse:
    """Every real Affiliation this actor holds — unlike GET .../relationships
    (which filters down to is_relationship_affiliation only), this
    returns the full set: employment, education, family, extended, and
    relationship-type alike."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    affiliations = state.actor_runtime.affiliations if state.actor_runtime is not None else None
    entries = [
        {
            "affiliation_id": a.affiliation_id,
            "affiliation_type": a.affiliation_type,
            "target_id": a.target_id,
            "target_name": a.target_name,
            "trust_level": a.trust_level,
            "category": a.category,
            "valid_from": a.valid_from,
            "valid_until": a.valid_until,
        }
        for a in (affiliations.all() if affiliations is not None else [])
    ]
    return ActorAffiliationsResponse(actor_id=actor_id, affiliations=entries)


def _walk_affiliation_chain(pr: Any, start_actor_id: str, max_depth: int = 5) -> list[dict[str, Any]]:
    """A real, traversed walk over kernel/affiliations — the SAME graph
    AffiliationManager already maintains per actor (family, employment,
    customer/supplier, member_of, etc.; member_of edges are synced 1:1
    from the real SocietyMembershipRegistry — see AffiliationType.
    MEMBER_OF's docstring). At each actor node, follows the single
    highest-trust unvisited edge (kept linear to match the debugger's
    chain UI, not a branching tree). A hop that lands on a society
    (no actor state of its own) falls back to that society's real
    roster (memberships_for_society) and continues from the
    highest-trust active co-member. Stops the moment no further real
    edge exists — never pads the chain to a target length."""
    found = _find_actor_state(pr, start_actor_id)
    if found is None:
        return []
    _, start_state = found
    chain: list[dict[str, Any]] = [
        {
            "id": start_actor_id,
            "name": start_state.profile.identity.name,
            "entityType": start_state.profile.identity.actor_type,
            "edgeLabel": None,
        }
    ]
    visited = {start_actor_id}
    current_kind, current_id = "actor", start_actor_id

    for _ in range(max_depth - 1):
        if current_kind == "actor":
            found = _find_actor_state(pr, current_id)
            if found is None:
                break
            _, cur_state = found
            affiliations = cur_state.actor_runtime.affiliations if cur_state.actor_runtime is not None else None
            edges = [a for a in (affiliations.all() if affiliations is not None else []) if a.target_id not in visited]
            if not edges:
                break
            edges.sort(key=lambda a: -a.trust_level)
            edge = edges[0]
            target_found = _find_actor_state(pr, edge.target_id)
            if target_found is not None:
                _, target_state = target_found
                chain.append(
                    {
                        "id": edge.target_id,
                        "name": target_state.profile.identity.name,
                        "entityType": target_state.profile.identity.actor_type,
                        "edgeLabel": edge.affiliation_type,
                    }
                )
                visited.add(edge.target_id)
                current_kind, current_id = "actor", edge.target_id
                continue
            sr = pr.get_society_runtime(edge.target_id)
            society_members = pr.membership_registry.memberships_for_society(edge.target_id)
            if sr is None and not society_members:
                # A real edge whose target is neither a known actor nor a
                # known society — still a genuine terminal node (its real
                # name/type from the affiliation edge itself), not fabricated.
                chain.append(
                    {
                        "id": edge.target_id,
                        "name": edge.target_name,
                        "entityType": edge.category or "organization",
                        "edgeLabel": edge.affiliation_type,
                    }
                )
                visited.add(edge.target_id)
                break
            chain.append(
                {
                    "id": edge.target_id,
                    "name": sr.society.name if sr is not None else edge.target_name,
                    "entityType": "society",
                    "edgeLabel": edge.affiliation_type,
                }
            )
            visited.add(edge.target_id)
            current_kind, current_id = "society", edge.target_id
        else:  # current_kind == "society"
            members = [
                m
                for m in pr.membership_registry.memberships_for_society(current_id)
                if m.status == "active" and m.actor_id not in visited
            ]
            if not members:
                break
            members.sort(key=lambda m: -m.trust_score)
            member_found = None
            for member in members:
                member_found = _find_actor_state(pr, member.actor_id)
                if member_found is not None:
                    break
            if member_found is None:
                break
            _, member_state = member_found
            chain.append(
                {
                    "id": member.actor_id,
                    "name": member_state.profile.identity.name,
                    "entityType": member_state.profile.identity.actor_type,
                    "edgeLabel": "co-member",
                }
            )
            visited.add(member.actor_id)
            current_kind, current_id = "actor", member.actor_id

    return chain


@router.get("/actors/{actor_id}/affiliation-chain", tags=["Actors"])
async def get_actor_affiliation_chain(
    actor_id: str,
    request: Request,
    max_depth: int = 5,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> dict[str, Any]:
    """A real, traversed chain through this actor's affiliation graph —
    backs the Execution Debugger's multi-hop Affiliation Graph card with
    real data instead of the demo fixture's hand-authored one. See
    _walk_affiliation_chain for the real traversal rules; the chain is
    whatever length the actual graph supports (often 1-3 real hops),
    never padded."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    chain = _walk_affiliation_chain(pr, actor_id, max_depth=max_depth)
    if not chain:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    return {"actor_id": actor_id, "chain": chain}


@router.post("/actors/{actor_id}/affiliations", tags=["Actors"])
@idempotent("actors.create_actor_affiliation")
async def create_actor_affiliation(
    actor_id: str,
    body: ActorAffiliationCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None or found[1].actor_runtime is None or found[1].actor_runtime.affiliations is None:
        raise HTTPException(
            status_code=404,
            detail=f"Actor {actor_id} not found or has no affiliation store",
        )
    target = _find_actor_state(pr, body.target_id)
    target_name = body.target_name or (target[1].profile.identity.name if target else body.target_id)
    affiliation = Affiliation(
        affiliation_id=f"aff:{actor_id}:{__import__('uuid').uuid4().hex}",
        affiliation_type=body.affiliation_type,
        target_id=body.target_id,
        target_name=target_name,
        trust_level=body.trust_level,
        permissions=tuple(body.permissions),
        policies=tuple(body.policies),
        priority=body.priority,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        metadata=body.metadata,
    )
    found[1].actor_runtime.affiliations.add(affiliation)
    pr._save_actors()
    return {"actor_id": actor_id, "affiliation": affiliation_to_api_dict(affiliation)}


@router.patch("/actors/{actor_id}/affiliations/{affiliation_id}", tags=["Actors"])
@idempotent("actors.update_actor_affiliation")
async def update_actor_affiliation(
    actor_id: str,
    affiliation_id: str,
    body: ActorAffiliationUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    found = _find_actor_state(pr, actor_id) if pr is not None else None
    affiliations = found[1].actor_runtime.affiliations if found and found[1].actor_runtime is not None else None
    if affiliations is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    changes = body.model_dump(exclude_unset=True)
    for key in ("permissions", "policies"):
        if key in changes and changes[key] is not None:
            changes[key] = tuple(changes[key])
    updated = affiliations.update(affiliation_id, **changes)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Affiliation {affiliation_id} not found")
    pr._save_actors()
    return {"actor_id": actor_id, "affiliation": affiliation_to_api_dict(updated)}


@router.delete("/actors/{actor_id}/affiliations/{affiliation_id}", tags=["Actors"])
@idempotent("actors.delete_actor_affiliation")
async def delete_actor_affiliation(
    actor_id: str,
    affiliation_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    found = _find_actor_state(pr, actor_id) if pr is not None else None
    affiliations = found[1].actor_runtime.affiliations if found and found[1].actor_runtime is not None else None
    if affiliations is None or not affiliations.remove(affiliation_id):
        raise HTTPException(status_code=404, detail=f"Affiliation {affiliation_id} not found")
    pr._save_actors()
    return {"status": "deleted", "actor_id": actor_id, "affiliation_id": affiliation_id}


@router.post("/actors/{actor_id}/ask", response_model=AskActorResponse, tags=["Actors"])
@idempotent("actors.ask_actor")
async def ask_actor(
    actor_id: str,
    body: AskActorRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> AskActorResponse:
    """Natural Language Actor-to-Actor Communication: this actor
    independently reasons over real world state (AnswerQuestionCapability
    — real KG facts + a real LLM call, never a template) and replies in
    natural language, not a structured action result. Ask another actor
    the SAME way any client would: pass its answer as the next actor's
    `question` — there is no hidden relay inside the runtime, the caller
    (a demo script, another actor's own next turn) does that."""
    from src.monkey_brain.kernel.domains.grocery import AnswerQuestionCapability
    from src.monkey_brain.kernel.society.context_stream import (
        ContextEvent,
        ContextEventType,
    )
    from src.monkey_brain.common.correlation import new_correlation_id

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    name = state.profile.identity.name
    goals = list(state.profile.goals) if state.profile.goals else []
    actor_role = f"{name}, whose responsibilities include: {', '.join(goals)}" if goals else name

    # Same no-upstream-id situation as AskActorCapability (kernel/domains/
    # grocery.py) — this HTTP request is its own logical operation, mint
    # once and thread through resolve -> the resulting ContextEvent.
    correlation_id = new_correlation_id()
    causation_id = ""
    if body.from_actor_id:
        decision = pr.resolve_communication(body.from_actor_id, actor_id, correlation_id=correlation_id)
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason)
        causation_id = decision.decision_id

    result = await AnswerQuestionCapability().handle(
        {
            "context": {
                "knowledge_graph": pr.knowledge_graph,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "question": body.question,
                "planetary_runtime": pr,
            }
        }
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "no answer produced"))

    timestamp = time.time()
    pr.context_stream.publish(
        ContextEvent(
            event_type=ContextEventType.INTERACTION,
            actor_id=actor_id,
            description=f"{body.from_actor_name or body.from_actor_id or 'someone'} asked {name}: {body.question}",
            payload={
                "from_actor_id": body.from_actor_id,
                "from_actor_name": body.from_actor_name,
                "to_actor_id": actor_id,
                "to_actor_name": name,
                "society_id": sr.society.society_id,
                "society_name": sr.society.name,
                "question": body.question,
                "answer": result["answer"],
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
    )

    # Real gap this closes: nothing anywhere in the kernel ever wrote a
    # kind="conversation" memory node (grepped the whole tree — the only
    # real record_experience() callers were membership.py's join/leave
    # events), so every actor's Conversations retrieval was permanently
    # empty. Recorded under BOTH participants — search_episodic() filters
    # strictly by actor_id, so an entry recorded under only one side
    # would never be retrievable by the other. `speaker` in metadata is
    # what _bucket_memory_nodes now surfaces as the real speaker name
    # instead of the generic "cognitive_memory" stage label.
    asker_name = body.from_actor_name or body.from_actor_id or "unknown"
    for participant_id in (actor_id, body.from_actor_id):
        if not participant_id:
            continue
        try:
            pr.memory_manager.record_experience(
                participant_id,
                kind="conversation",
                text=body.question,
                metadata={
                    "timestamp": timestamp,
                    "speaker": asker_name,
                    "correlation_id": correlation_id,
                },
            )
            pr.memory_manager.record_experience(
                participant_id,
                kind="conversation",
                text=result["answer"],
                metadata={
                    "timestamp": timestamp,
                    "speaker": name,
                    "correlation_id": correlation_id,
                },
            )
        except Exception:
            logger.exception(
                "record_experience(kind=conversation) failed for participant %s — the ask/answer above already succeeded",
                participant_id,
            )

    return AskActorResponse(
        question=body.question,
        answer=result["answer"],
        actor_id=actor_id,
        actor_name=name,
        society_id=sr.society.society_id,
        society_name=sr.society.name,
        from_actor_id=body.from_actor_id,
        from_actor_name=body.from_actor_name,
        timestamp=timestamp,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


@router.post("/actors/{actor_id}/chat", response_model=ActorChatResponse, tags=["Actors"])
@idempotent("actors.actor_chat")
async def actor_chat(
    actor_id: str,
    body: ActorChatRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> ActorChatResponse:
    """A human operator's direct chat with an actor — distinct from
    /ask (actor-to-actor, used by the real AskActor plan capability too;
    left untouched so this never changes autonomous planning behavior).
    Three real tiers, tried in order, never faked:
      1. RAG: this actor's own real KG facts (AnswerQuestionCapability.
         _gather_facts — the exact same keyword-matched lookup /ask
         already uses), if any match the message.
      2. Web search (Tavily, kernel/execute/provider/web_search.py),
         only reached when tier 1 found nothing — honestly returns []
         (not an error) when no key is configured or nothing is found.
      3. Direct LLM: get_backend().complete() with no grounding at all,
         reached only when both above are empty — the answer is still
         real, just labeled "general_knowledge" so the caller can show
         the user it isn't backed by a specific fact.
    One real LLM call either way; the tier only changes what's IN the
    prompt as grounding, never fabricates a fact to fill it."""
    from src.monkey_brain.kernel.domains.grocery import AnswerQuestionCapability
    from src.monkey_brain.kernel.execute.provider.model_backend import get_backend
    from src.monkey_brain.kernel.execute.provider.web_search import tavily_search

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    name = state.profile.identity.name
    message = body.message.strip()

    facts = AnswerQuestionCapability._gather_facts(pr.knowledge_graph, actor_id, message)
    web_results: list[dict[str, str]] = []
    if facts:
        source = "knowledge_graph"
        grounding_text = "\n".join(f"- {f}" for f in facts)
    else:
        web_results = await tavily_search(message)
        if web_results:
            source = "web_search"
            grounding_text = "\n".join(f"- {r['title']}: {r['content']}" for r in web_results)
        else:
            source = "general_knowledge"
            grounding_text = ""

    if source == "general_knowledge":
        system = (
            f"You are {name}, replying to a direct message from your operator. "
            "You have no specific grounded facts or search results for this — answer "
            "from your own general knowledge, briefly and honestly. If you're not "
            "confident, say so rather than guessing with false certainty."
        )
        prompt = f'Your operator says: "{message}"\n\nYour reply:'
    else:
        system = (
            f"You are {name}, replying to a direct message from your operator. "
            "Answer using the real information below — if it doesn't fully answer "
            "the question, say what's missing rather than inventing the rest. "
            "Reply in 1-4 short, plain, conversational sentences. No markdown."
        )
        prompt = (
            f"{'Facts you know' if source == 'knowledge_graph' else 'Web search results'}:\n{grounding_text}\n\n"
            f'Your operator says: "{message}"\n\nYour reply:'
        )

    answer = (await get_backend().complete(prompt, system=system)).strip()

    return ActorChatResponse(
        answer=answer,
        source=source,
        facts_used=facts,
        web_results=[ActorChatWebResult(title=r["title"], url=r["url"]) for r in web_results],
    )


@router.post(
    "/actors/{actor_id}/web-search-chat",
    response_model=WebSearchChatResponse,
    tags=["Actors"],
)
@idempotent("actors.web_search_chat")
async def web_search_chat(
    actor_id: str,
    body: WebSearchChatRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> WebSearchChatResponse:
    """CognitiveOS Assistant — Web Search mode: explicitly external/
    current information, never CognitiveOS's own KG/memory (that's LLM/
    RAG mode's job — see execution_chat below; the two modes must never
    silently mix, per the Assistant's own design). Same real
    tavily_search() primitive actor_chat's tier-2 fallback uses, called
    directly here since the user explicitly chose this mode rather than
    it being a fallback — one real search, one real summarization call,
    real source citations, no fabricated results."""
    from src.monkey_brain.kernel.execute.provider.model_backend import get_backend
    from src.monkey_brain.kernel.execute.provider.web_search import tavily_search

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")

    def _record_conversation(reply: str) -> None:
        # Same real gap/fix as execution_chat above — kind="conversation",
        # never promoted to a durable belief by this endpoint itself.
        try:
            timestamp = time.time()
            pr.memory_manager.record_experience(
                actor_id,
                kind="conversation",
                text=query,
                metadata={
                    "timestamp": timestamp,
                    "speaker": "operator",
                    "source": "web_search",
                },
            )
            pr.memory_manager.record_experience(
                actor_id,
                kind="conversation",
                text=reply,
                metadata={
                    "timestamp": timestamp,
                    "speaker": "CognitiveOS Assistant",
                    "source": "web_search",
                },
            )
        except Exception:
            logger.exception(
                "web_search_chat: record_experience(kind=conversation) failed for actor %s — the answer above already succeeded",
                actor_id,
            )

    query = body.query.strip()
    web_results = await tavily_search(query)
    if not web_results:
        no_results_answer = "I searched the web but couldn't find any current results for that."
        _record_conversation(no_results_answer)
        return WebSearchChatResponse(answer=no_results_answer, sources=[])

    grounding_text = "\n".join(f"- {r['title']}: {r['content']} ({r['url']})" for r in web_results)
    system = (
        "You are CognitiveOS's web search assistant. Summarize the CURRENT, EXTERNAL "
        "information below to directly answer the query — use ONLY what's in the "
        "results, never your own prior knowledge, since the whole point of this mode "
        "is fresh, current information. Be concise (1-3 sentences). No markdown."
    )
    prompt = f'Search results:\n{grounding_text}\n\nQuery: "{query}"\n\nYour answer:'
    answer = (await get_backend().complete(prompt, system=system)).strip()
    _record_conversation(answer)

    return WebSearchChatResponse(
        answer=answer,
        sources=[WebSearchChatSource(title=r["title"], url=r["url"]) for r in web_results],
    )


@router.post("/actors/{actor_id}/goal-draft", response_model=GoalDraftResponse, tags=["Actors"])
@idempotent("actors.draft_actor_goal")
async def draft_actor_goal(
    actor_id: str,
    body: GoalDraftRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> GoalDraftResponse:
    """CognitiveOS Assistant — Goal mode: conversationally builds a
    structured goal (objective/actor/constraints/preferences/success
    conditions) via one real LLM extraction call per message. Never
    persists anything and never triggers a tick — Goal mode's whole job
    is defining/refining the goal; only the separate Create/Update Goal
    actions (frontend) turn a draft into a real one, by calling the
    already-real, already-persisting POST /actors/{id}/goals
    (add_actor_goal, add_goal() above) with a string synthesized from
    the finished draft."""
    import json as _json

    from src.monkey_brain.kernel.execute.provider.model_backend import get_backend

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    name = state.profile.identity.name

    current = body.current_draft or GoalDraft(actor=name)
    system = (
        "You extract a structured CognitiveOS goal from a conversation. You are given "
        "the CURRENT DRAFT (JSON) and the operator's new message. Return the UPDATED "
        "draft — only change fields the new message actually implies; leave every "
        "other field exactly as given in the current draft, do not invent values for "
        "fields the conversation hasn't addressed. Respond with ONLY a JSON object of "
        'this exact shape, no other text: {"draft": {"objective": "...", "actor": "...", '
        '"constraints": ["..."], "preferences": ["..."], "success_conditions": ["..."]}, '
        '"update_summary": "one short line describing what just changed, e.g. '
        "'Constraint added: Budget ≤ $10' or 'Objective set: Purchase 2L of milk'\"}."
    )
    prompt = (
        f"CURRENT DRAFT:\n{current.model_dump_json()}\n\n"
        f'Operator\'s new message: "{body.message.strip()}"\n\nYour JSON response:'
    )
    raw = (await get_backend().complete(prompt, system=system)).strip()
    try:
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        parsed = _json.loads(raw)
        draft = GoalDraft(**parsed["draft"])
        update_summary = str(parsed.get("update_summary", ""))
    except Exception:
        logger.warning("draft_actor_goal: could not parse LLM response as JSON: %r", raw[:200])
        draft = current
        update_summary = "Could not parse an update from that message."

    return GoalDraftResponse(draft=draft, update_summary=update_summary)


@router.post(
    "/actors/{actor_id}/transactions",
    response_model=TransactionResponse,
    tags=["Actors"],
)
@idempotent("actors.execute_transaction")
async def execute_transaction(
    actor_id: str,
    body: TransactionRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> TransactionResponse:
    """Required Transaction Execution Logic: actor_id is the originating
    actor, driving an LLM-decided negotiation with its affiliates via
    PlanetaryRuntime.execute_transaction() (kernel/society/transaction.py
    — never a hardcoded workflow here or in the runtime). Live progress
    for the returned transaction_id streams to
    WS /ws/transactions/{transaction_id} and NATS subject
    monkeybrain.transaction.{transaction_id} while this call is in
    flight; subscribe before POSTing to see it as it happens, since this
    response only arrives once the transaction reaches a terminal
    state."""
    import dataclasses

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if _find_actor_state(pr, actor_id) is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")

    result = await pr.execute_transaction(actor_id, body.objective, max_steps=body.max_steps)

    steps = [
        TransactionStepResponse(
            step_number=step.step_number,
            target_actor_id=step.target_actor_id,
            message=step.message,
            trace=dataclasses.asdict(step.trace) if step.trace is not None else None,
            next_action=step.next_action,
            next_action_reason=step.next_action_reason,
            strategic_context=step.strategic_context,
            timestamp=step.timestamp,
        )
        for step in result.steps
    ]
    return TransactionResponse(
        transaction_id=result.transaction_id,
        originating_actor_id=result.originating_actor_id,
        objective=result.objective,
        status=result.status.value,
        steps=steps,
        societies_involved=list(result.societies_involved),
        affiliates_contacted=list(result.affiliates_contacted),
        duration_ms=result.duration_ms,
        final_outcome=result.final_outcome,
        timestamp=result.timestamp,
        stream_url=f"/ws/transactions/{result.transaction_id}",
    )


@router.get(
    "/actors/{actor_id}/capabilities",
    response_model=ActorCapabilitiesResponse,
    tags=["Actors"],
)
async def get_actor_capabilities(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorCapabilitiesResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    caps = [{"name": c.name, "level": c.level.value} for c in state.profile.capabilities]
    return ActorCapabilitiesResponse(actor_id=actor_id, capabilities=caps)


@router.get("/actors/{actor_id}/status", response_model=ActorStatusResponse, tags=["Actors"])
async def get_actor_status(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> ActorStatusResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    return ActorStatusResponse(
        actor_id=actor_id,
        status=state.status.value,
        cycle_count=state.cycle_count,
        tick_count=state.cycle_count,
        enabled=state.is_active,
    )


@router.post("/actors/{actor_id}/tick", response_model=ActorTickResponse, tags=["Actors"])
@idempotent("actors.tick_actor")
async def tick_actor(
    actor_id: str,
    body: ActorTickRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> ActorTickResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found

    if state.actor_runtime is not None:
        try:
            # Route through SocietyRuntime's own coordination path (belief
            # fusion via BeliefFusion.fuse(), context-stream publication,
            # experience/world-event commit, status transitions) instead of
            # calling the ActorRuntime boundary instead of hand-rolling a second,
            # simpler belief construction here — this API entry point must
            # produce the same belief/event behavior as /societies/{id}/tick
            # and /planet/tick for the same actor.
            coordinated = await sr.tick_one_actor(actor_id)
            if not coordinated:
                raise RuntimeError(f"Actor {actor_id} tick did not complete")
            result = state.last_tick_result

            result_dict = {}
            if isinstance(result, TickResultProtocol):
                if result.plan is not None:
                    plan_data = (
                        result.plan if isinstance(result.plan, dict) else {"steps": getattr(result.plan, "steps", [])}
                    )
                    result_dict["plan"] = plan_data
                if result.actions:
                    result_dict["actions"] = [
                        {
                            "action_id": a.get("action_id", ""),
                            "success": a.get("success", False),
                            "error": a.get("error", ""),
                        }
                        for a in (result.actions or [])
                    ]
                if result.belief_updated:
                    result_dict["belief_updated"] = result.belief_updated
                if result.learned:
                    result_dict["learned"] = result.learned
                if result.error:
                    result_dict["error"] = result.error
                if result.predicted_outcome:
                    result_dict["predicted_outcome"] = result.predicted_outcome
                if result.outcome:
                    result_dict["outcome"] = result.outcome

            pr._save_actors()

            return ActorTickResponse(
                actor_id=actor_id,
                tick_count=state.cycle_count,
                result=result_dict,
            )
        except Exception as e:
            import traceback

            logger.error("Tick failed: %s\n%s", e, traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"Tick failed: {e}")

    return ActorTickResponse(
        actor_id=actor_id,
        tick_count=state.cycle_count,
        result={"status": "no_tick_handler"},
    )


@router.post("/actors/{actor_id}/observe", tags=["Actors"])
@idempotent("actors.observe_actor")
async def observe_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    obs = sr.get_observation(actor_id)
    entities = [
        {
            "entity_id": oe.entity.entity_id,
            "name": oe.entity.name,
            "entity_type": oe.entity.entity_type.value,
            "attributes": oe.entity.attributes,
            "state": oe.entity.state,
            "confidence": oe.entity.confidence,
            "quality": oe.quality.value,
        }
        for oe in obs.entities
    ]
    # Fuse the observation into the actor's belief state via the same
    # BeliefFusion (decay, hypothesis merging, versioning) SocietyRuntime's
    # coordinated tick path uses — not a second, hand-rolled belief
    # construction living only in this route.
    state.belief_state = sr._belief_fusion.fuse(actor_id, obs, state.belief_state)
    pr._save_actors()
    return {
        "actor_id": actor_id,
        "observation_id": obs.observation_id,
        "world_version": obs.world_version,
        "quality": obs.quality.value,
        "observation_time": obs.observation_time,
        "entities": entities,
        "relationships": [
            {
                "relationship_id": or_.relationship.relationship_id,
                "source_id": or_.relationship.source_id,
                "target_id": or_.relationship.target_id,
                "kind": or_.relationship.kind.value,
                "quality": or_.quality.value,
            }
            for or_ in obs.relationships
        ],
        "events": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "description": e.description,
                "attributes": e.attributes,
            }
            for e in obs.events
        ],
    }


@router.post("/actors/{actor_id}/plan", tags=["Actors"])
@idempotent("actors.plan_actor")
async def plan_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found

    if state.actor_runtime is not None:
        try:
            result = await state.actor_runtime.tick()
            state.cycle_count += 1
            state.last_cycle = time.time()
            pr._save_actors()

            plan = {}
            if hasattr(result, "plan"):
                plan = result.plan if isinstance(result.plan, dict) else {"steps": getattr(result.plan, "steps", [])}
            goal = state.profile.goals[0] if state.profile.goals else ""
            return {
                "actor_id": actor_id,
                "goal": goal,
                "plan": plan,
                "actions_count": len(getattr(result, "actions", []) or []),
                "belief_updated": getattr(result, "belief_updated", False),
                "predicted_outcome": getattr(result, "predicted_outcome", None),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Plan failed: {e}")

    return {"actor_id": actor_id, "plan": {}, "error": "no_tick_handler"}


@router.post(
    "/actors/{actor_id}/experiences",
    response_model=ExperienceRecordResponse,
    tags=["Actors"],
)
@idempotent("actors.record_actor_experience")
async def record_actor_experience(
    actor_id: str,
    body: ExperienceRecordRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="update", resource="actor")),
) -> ExperienceRecordResponse:
    """Record a completed experience for this actor into the shared
    CognitiveMemory/KnowledgeGraph (MemoryManager.record_experience) — the
    mechanism other actors' goal-driven knowledge search can later surface,
    via PlanningContext.relevant_knowledge, when they share real keywords."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if _find_actor_state(pr, actor_id) is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    if not body.text:
        raise HTTPException(status_code=400, detail="text is required")

    # record_experience embeds text via a real (lazily-loaded, blocking)
    # sentence-transformers model — same class of call that previously
    # froze the single-worker event loop when done synchronously in the
    # planner; run it off-thread here for the same reason.
    metadata = {**body.metadata, "visibility": body.visibility}
    node = await asyncio.to_thread(
        pr.memory_manager.record_experience,
        actor_id,
        body.kind,
        body.text,
        metadata,
    )
    return ExperienceRecordResponse(actor_id=actor_id, node_id=node.node_id)


@router.get("/actors/{actor_id}/experiences/shared", tags=["Actors"])
async def search_shared_experiences(
    actor_id: str,
    request: Request,
    query: str,
    user_id: str = Depends(require_permission("perm-view-actors")),
) -> list[dict[str, Any]]:
    """Cognitive Network (CCB-600): other actors' `visibility="shared"`
    experiences this actor may currently receive (co-membership in at
    least one Society — ContextConstructionEngine._may_receive_shared_
    experience). Thin wrapper for demoing/verifying retrieval directly,
    independent of running a full planning cycle."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if _find_actor_state(pr, actor_id) is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    from src.monkey_brain.kernel.pipeline.planning.context_engine import (
        ContextConstructionEngine,
    )

    engine = ContextConstructionEngine(planetary_runtime=pr, memory_manager=pr.memory_manager)
    # search_episodic embeds the query via a blocking sentence-transformers
    # model, same reason record_actor_experience above runs off-thread.
    nodes = await asyncio.to_thread(engine.search_shared_experiences, actor_id, query, 5)
    return [
        {
            "node_id": node.node_id,
            "actor_id": node.payload.get("actor_id", ""),
            "kind": node.payload.get("kind", ""),
            "text": node.payload.get("text", ""),
            "retrieval_score": node.payload.get("_retrieval_score", 0.0),
        }
        for node in nodes
    ]


async def run_actor_tick(actor_id: str, pr: Any, state: Any) -> dict[str, Any]:
    """The actual tick-and-format-response work behind POST /execute —
    factored out of execute_actor() so actor_runtime.py's own internal
    /execute route (added for the per-actor-Pod deployment model, see
    that function's own docstring for why) can share it verbatim rather
    than re-implementing the same response shape."""
    if state.actor_runtime is None:
        return {"actor_id": actor_id, "status": "no_tick_handler"}
    _gate_on_world_validation(pr, actor_id=actor_id)
    try:
        # Same 30s cap SocietyRuntime.tick_one_actor() already applies to
        # the identical operation (a single actor's cognitive tick, LLM
        # planner included) — this route called actor_runtime.tick()
        # directly with no timeout at all, so a slow LLM call here could
        # hang indefinitely instead of failing predictably like every
        # other actor-tick entry point does.
        result = await asyncio.wait_for(state.actor_runtime.tick(), timeout=30.0)
        state.cycle_count += 1
        state.last_cycle = time.time()
        pr._save_actors()

        return {
            "actor_id": actor_id,
            "goal": state.profile.goals[0] if state.profile.goals else "",
            "goal_achieved": (
                getattr(result, "outcome", {}).get("goal_achieved", False)
                if isinstance(getattr(result, "outcome", None), dict)
                else False
            ),
            "actions": [
                {
                    "action_id": a.get("action_id", ""),
                    "success": a.get("success", False),
                    "error": a.get("error", ""),
                }
                for a in (getattr(result, "actions", None) or [])
            ],
            "belief_updated": getattr(result, "belief_updated", False),
            "learned": getattr(result, "learned", False),
            "error": getattr(result, "error", None),
            "cycle_count": state.cycle_count,
        }
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Actor {actor_id} tick timed out after 30s (LLM planner latency — see docs/adr/016-performance-gate9.md)",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execute failed: {e}")


async def _proxy_execute_to_actor_pod(actor_id: str, pr: Any) -> dict[str, Any] | None:
    """Live Deployment Validation finding: execute_actor() only ever
    searched THIS process's own locally-loaded societies — correct for
    the monolithic control-plane deployment (deployment.yaml, every
    actor resident in one process), but a real gap for the per-actor-Pod
    model (actor-deployment.yaml) this codebase's own Actor Artifact
    model treats as canonical: an actor correctly migrated onto its own
    dedicated Pod is, by design, no longer resident here at all, so the
    control plane 404'd on an actor that was very much alive and
    reachable — confirmed live.

    Proxies the call over the network to that actor's own Pod instead of
    trying to reach into its process. Deliberately narrow: only attempts
    this when (a) the durable registry confirms the actor exists and
    names a node that isn't this process's own node_id, and (b)
    KUBERNETES_SERVICE_HOST is set (a standard signal this process is
    actually running inside a real cluster, not local dev/tests) — in
    every other case this returns None and the caller keeps its existing
    404 behavior unchanged. Targets the actor's canonical Kubernetes
    Service name (cognitiveos-actor-{actor_id}, see actor-deployment.
    yaml's own Service block), not the Pod's own randomly-suffixed name,
    since only the Service name is a stable, resolvable address."""
    import os

    if not os.getenv("KUBERNETES_SERVICE_HOST"):
        return None
    entry = pr.locate_actor(actor_id)
    if entry is None or not entry.node_id or entry.node_id == pr._node_id:
        return None
    import httpx

    from src.monkey_brain.api.internal_auth import internal_service_headers

    namespace = os.getenv("POD_NAMESPACE", "monkeybrain")
    url = f"http://cognitiveos-actor-{actor_id}.{namespace}.svc.cluster.local:8051/execute"
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(url, headers=internal_service_headers())
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Actor {actor_id}'s own Pod: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.post("/actors/{actor_id}/execute", tags=["Actors"])
@idempotent("actors.execute_actor")
async def execute_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    # Live Deployment Validation finding: checking local presence
    # (_find_actor_state, below) FIRST was itself the bug -- the
    # control-plane process's own unscoped _load_actors() means every
    # actor ever registered has SOME local copy here (however stale or
    # suspended once it's correctly migrated to its own dedicated Pod),
    # so `found is None` never actually triggered and the proxy fix
    # below was silently dead code. The durable registry's own node_id
    # is the only authoritative answer to "where does this actor really
    # live" -- check that FIRST, and only fall back to the local
    # in-memory lookup when the registry agrees this is the right node
    # (or has no opinion at all, e.g. a single-process/no-Redis dev run).
    entry = pr.locate_actor(actor_id)
    if entry is not None and entry.node_id and entry.node_id != pr._node_id:
        proxied = await _proxy_execute_to_actor_pod(actor_id, pr)
        if proxied is not None:
            return proxied
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, state = found
    return await run_actor_tick(actor_id, pr, state)


# ── Actor Relationships CRUD ────────────────────────────────────────────


@router.get("/actors/{actor_id}/relationships", tags=["Actors"])
async def get_actor_relationships(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    _, state = found
    affiliations = state.actor_runtime.affiliations if state.actor_runtime is not None else None
    rels = [
        affiliation_to_relationship_dict(actor_id, a)
        for a in (affiliations.all() if affiliations is not None else [])
        if is_relationship_affiliation(a)
    ]
    return {"actor_id": actor_id, "relationships": rels, "count": len(rels)}


@router.post("/actors/{actor_id}/relationships", tags=["Actors"])
@idempotent("actors.create_actor_relationship")
async def create_actor_relationship(
    actor_id: str,
    body: ActorRelationshipCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    source_found = _find_actor_state(pr, body.source_actor_id)
    if source_found is None:
        raise HTTPException(status_code=404, detail=f"Actor {body.source_actor_id} not found")
    source_runtime = source_found[1].actor_runtime
    source_affiliations = getattr(source_runtime, "affiliations", None)
    if source_affiliations is None:
        raise HTTPException(
            status_code=409,
            detail=f"Actor {body.source_actor_id} does not support relationships",
        )
    source_name = source_found[1].profile.identity.name

    target_found = _find_actor_state(pr, body.target_actor_id)
    target_name = target_found[1].profile.identity.name if target_found else body.target_actor_id

    source_affiliations.add(
        make_relationship_affiliation(
            source_id=body.source_actor_id,
            source_name=source_name,
            target_id=body.target_actor_id,
            target_name=target_name,
            relationship_type=body.relationship_type,
            strength=body.strength,
            metadata=body.metadata,
            owner_id=body.source_actor_id,
        )
    )

    if target_found is not None:
        target_affiliations = getattr(target_found[1].actor, "affiliations", None)
        if target_affiliations is not None:
            target_affiliations.add(
                make_relationship_affiliation(
                    source_id=body.source_actor_id,
                    source_name=source_name,
                    target_id=body.target_actor_id,
                    target_name=target_name,
                    relationship_type=body.relationship_type,
                    strength=body.strength,
                    metadata=body.metadata,
                    owner_id=body.target_actor_id,
                )
            )

    pr._save_actors()
    return {
        "source_actor_id": body.source_actor_id,
        "target_actor_id": body.target_actor_id,
        "relationship_type": body.relationship_type,
        "strength": body.strength,
        "metadata": dict(body.metadata),
    }


@router.delete("/actors/relationships/{source_id}/{target_id}", tags=["Actors"])
@idempotent("actors.delete_actor_relationship")
async def delete_actor_relationship(
    source_id: str,
    target_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    deleted = False
    for owner_id in (source_id, target_id):
        found = _find_actor_state(pr, owner_id)
        if found is None:
            continue
        affiliations = getattr(found[1].actor, "affiliations", None)
        if affiliations is None:
            continue
        for a in affiliations.all():
            if not is_relationship_affiliation(a):
                continue
            rd = affiliation_to_relationship_dict(owner_id, a)
            if {rd["source_actor_id"], rd["target_actor_id"]} == {source_id, target_id}:
                if affiliations.remove(a.affiliation_id):
                    deleted = True

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Relationship {source_id}->{target_id} not found")
    pr._save_actors()
    return {"status": "deleted", "source_id": source_id, "target_id": target_id}


# ── Actor Addresses (Communication Channels) CRUD ──────────────────────


@router.get("/actors/{actor_id}/addresses", tags=["Actors"])
async def get_actor_addresses(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-view-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="view", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, _ = found
    addrs = [a.to_dict() for a in sr.society.addresses if a.actor_id == actor_id]
    return {"actor_id": actor_id, "addresses": addrs, "count": len(addrs)}


@router.post("/actors/{actor_id}/addresses", tags=["Actors"])
@idempotent("actors.create_actor_address")
async def create_actor_address(
    actor_id: str,
    body: ActorAddressCreateRequest,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, _ = found
    addr = ActorAddress(
        actor_id=actor_id,
        address_type=body.address_type,
        value=body.value,
        is_primary=body.is_primary,
        metadata=body.metadata,
    )
    import dataclasses

    sr._society = dataclasses.replace(
        sr._society,
        addresses=sr.society.addresses + (addr,),
    )
    pr._save_societies()

    # Any address here is also real commerce data, not just a society-
    # scoped contact record: DeliveryCapability (kernel/domains/grocery.py)
    # resolves delivery addresses from EntityType.ADDRESS entities in the
    # commerce knowledge graph — a completely separate store from
    # sr.society.addresses above. This used to only write that entity when
    # address_type was exactly "physical"/"delivery"/"shipping" — an
    # unvalidated free-text field, so any other value a caller reasonably
    # chose (e.g. "home", "work") silently saved an address that showed up
    # in GET /addresses but could never actually be used for delivery
    # ("no delivery address on file for actor" the moment Delivery ran).
    # DeliveryCapability's own lookup already picks the right ONE address
    # (is_primary, falling back to the first) — address_type gatekeeping
    # which addresses even exist for it to choose from added a silent trap,
    # not a real distinction. Writing both here keeps "set my address" a
    # single real action instead of two different endpoints a caller would
    # have to know to call.
    kg = getattr(pr, "knowledge_graph", None)
    if kg is not None:
        from src.monkey_brain.kernel.knowledge_graph import EntityType
        from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

        # Real, live-confirmed bug: kg.add_entity() asserts
        # assert_state_mutation_allowed() (security_boundary.py), which
        # fails closed with SecurityBoundaryDenied (surfaced as an
        # unhandled 500) outside insecure-dev-mode, a commitment, or this
        # context — this endpoint is operator/onboarding-driven (no
        # agent, no capability, no committed plan behind it), so it was
        # never actually coverable by "run it inside ensure_governed"
        # like an agent capability would be. privileged_infrastructure()
        # is the correct, existing (if previously never-called) escape
        # hatch for exactly this: a trusted, non-agent-initiated
        # management write.
        with privileged_infrastructure(
            reason="POST /actors/{id}/addresses: operator-set delivery address, not an agent action"
        ):
            kg.add_entity(
                f"address_{addr.address_id}",
                EntityType.ADDRESS,
                f"{actor_id} address",
                attributes={
                    "full_address": body.value,
                    "is_primary": body.is_primary,
                    "actor_id": actor_id,
                    **body.metadata,
                },
            )

    return addr.to_dict()


@router.delete("/actors/{actor_id}/addresses/{address_id}", tags=["Actors"])
@idempotent("actors.delete_actor_address")
async def delete_actor_address(
    actor_id: str,
    address_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    found = _find_actor_state(pr, actor_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found")
    sr, _ = found
    addrs = [a for a in sr.society.addresses if a.address_id != address_id]
    if len(addrs) < len(sr.society.addresses):
        import dataclasses

        sr._society = dataclasses.replace(sr._society, addresses=tuple(addrs))
        pr._save_societies()
        return {"status": "deleted", "address_id": address_id}
    raise HTTPException(status_code=404, detail=f"Address {address_id} not found")


# ── Teams (Runtime Encapsulation Refactor follow-up) ────────────────────
# Planet -> Country -> City -> Society -> Team -> Actor. Team is a
# containment object owned by SocietyRuntime — no tick()/cycle() of its own.


@router.post("/actors/teams", tags=["Actors"])
@idempotent("actors.create_team")
async def create_team(
    body: TeamCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    """Create a team within a society."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    sr = pr.get_society_runtime(body.society_id)
    if sr is None:
        raise HTTPException(status_code=404, detail=f"Society {body.society_id} not found")

    team = sr.create_team(body.name, body.description)
    return team.to_dict()


@router.post("/actors/teams/{team_id}/members", tags=["Actors"])
@idempotent("actors.add_team_member")
async def add_team_member(
    team_id: str,
    body: TeamMemberAddRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    """Add an actor to a team. Strict membership: an actor belongs to at
    most one team per society — adding to a new team removes it from any
    prior one in the same society."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    found = _find_team(pr, team_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    sr, _ = found

    team = sr.add_actor_to_team(team_id, body.actor_id)
    if team is None:
        raise HTTPException(
            status_code=404,
            detail=f"Actor {body.actor_id} not found in this team's society",
        )
    return team.to_dict()


@router.post("/actors/teams/{team_id}/tick", tags=["Actors"])
@idempotent("actors.tick_team")
async def tick_team(
    team_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="execute", resource="actor")),
) -> dict[str, Any]:
    """Tick every member actor of a team."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    found = _find_team(pr, team_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    sr, _ = found

    result = await sr.tick_team(team_id)
    return {
        "team_id": result.team_id,
        "actors_ticked": list(result.actors_ticked),
        "duration_ms": result.duration_ms,
    }


# ── cogctl / declarative control (Final Architectural Convergence, Phase 6) ─


@router.post("/actors/apply", tags=["Actors"])
@idempotent("actors.apply_actor_specification")
async def apply_actor_specification(
    body: dict[str, Any],
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="manage", resource="actor")),
) -> dict[str, Any]:
    """`cogctl apply -f actor.yaml` — the Control API endpoint for a
    declarative ActorSpecification (kernel/society/actor_specification.py).
    Create-or-update, the same semantics as `kubectl apply`: if
    metadata.actor_id/name resolves to an existing registry record, this
    updates its placement requirements and desired state; otherwise it
    registers a brand-new Actor via the exact same canonical
    PlanetaryRuntime.register_actor() every other entry point already
    uses — this route never constructs an Actor a second, different way.

    Never starts an Actor process directly (the explicit cogctl
    invariant): this only writes desired state / placement requirements
    and wakes the event-driven reconciliation queue
    (PlanetaryRuntime._enqueue_reconciliation) — whichever Execution
    Node's own Actor Runtime the Scheduler assigns is what actually
    brings the Actor to READY, on that node's own reconciliation loop,
    not synchronously inside this request.
    """
    from src.monkey_brain.kernel.society.actor_specification import (
        ActorSpecification,
        ActorSpecificationError,
    )
    from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState
    from src.monkey_brain.kernel.society.actor_scheduler import (
        ActorPlacementRequirements,
        NodeClass,
    )
    from src.monkey_brain.kernel.society.domain import (
        ActorProfile,
        ActorIdentity,
        ActorType,
    )

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    try:
        spec = ActorSpecification.from_dict(body)
    except ActorSpecificationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    actor_id = spec.resolved_actor_id()
    entry = pr.locate_actor(actor_id)
    created = False
    if entry is None:
        # ActorIdentity.actor_id defaults via a uuid4 default_factory --
        # that only fires when the constructor kwarg is OMITTED entirely,
        # not when explicitly passed as "". spec.actor_id is usually ""
        # (the common case: only metadata.name was given), so it must
        # never be passed through literally or every such Actor would be
        # registered with an empty actor_id instead of a real one.
        identity_kwargs: dict[str, Any] = {
            "name": spec.name or spec.actor_id,
            "actor_type": ActorType.AI_AGENT,
        }
        if spec.actor_id:
            identity_kwargs["actor_id"] = spec.actor_id
        state = pr.register_actor(
            ActorProfile(
                identity=ActorIdentity(**identity_kwargs),
                goals=spec.goals,
                objective=spec.objective,
            )
        )
        actor_id = state.actor_id
        created = True

    def _resolve_node_class(value: str, field_name: str) -> Any:
        if not value:
            return None
        try:
            return NodeClass(value)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"spec.placement.{field_name} {value!r} is not a recognized node class "
                f"({[c.value for c in NodeClass]})",
            )

    required_node_class = _resolve_node_class(spec.node_class, "node_class")
    preferred_node_class = _resolve_node_class(spec.preferred_node_class, "preferred_node_class")

    pr.set_actor_placement_requirements(
        actor_id,
        ActorPlacementRequirements(
            required_capabilities=spec.required_capabilities,
            required_node_class=required_node_class,
            preferred_node_class=preferred_node_class,
            preferred_region=spec.preferred_region,
        ),
    )
    if spec.claim_node:
        pr.scheduler.migrate_actor(actor_id, target_node_id=spec.claim_node)
    pr.set_actor_desired_state(actor_id, ActorDesiredState.RUNNING, reason="cogctl apply")

    observed = pr.observe_actor(actor_id)
    return {
        "actor_id": actor_id,
        "created": created,
        "spec": spec.to_dict(),
        "desired_state": ActorDesiredState.RUNNING.value,
        "observed": {
            "exists": observed.exists,
            "status": observed.status,
            "node_id": observed.node_id,
            "resident_here": observed.resident_here,
            "desired_node_id": observed.desired_node_id,
        },
    }


@router.post("/actors/{actor_id}/restart", tags=["Actors"])
@idempotent("actors.restart_actor")
async def restart_actor(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
    _agent: dict = Depends(require_opa("agentos/routes/allow", action="lifecycle", resource="actor")),
) -> dict[str, Any]:
    """`cogctl restart actor <id>` — suspend then resume, reusing the
    existing SUSPENDED/RUNNING desired-state primitives
    (ActorLifecycleController) rather than a new lifecycle mechanism.
    Same actor_id, same checkpoint-restore path every other suspend/
    resume already uses; never reconstructs a new Actor."""
    from src.monkey_brain.kernel.society.actor_lifecycle import ActorDesiredState

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.locate_actor(actor_id) is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id} not found in registry")

    pr.lifecycle.set_desired_state(actor_id, ActorDesiredState.SUSPENDED, reason="cogctl restart")
    suspend_result = pr.lifecycle.reconcile(actor_id)
    pr.lifecycle.set_desired_state(actor_id, ActorDesiredState.RUNNING, reason="cogctl restart")
    resume_result = pr.lifecycle.reconcile(actor_id)

    return {
        "actor_id": actor_id,
        "suspend": {
            "action": suspend_result.action,
            "succeeded": suspend_result.succeeded,
        },
        "resume": {
            "action": resume_result.action,
            "succeeded": resume_result.succeeded,
        },
    }
