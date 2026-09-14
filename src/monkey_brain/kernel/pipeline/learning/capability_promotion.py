"""Capability Promotion — closes the missing half of "Compile Φ."

CognitiveOS Constitution: "repeated verified cognition should become
reusable deterministic capability." learning/phi.py already compresses one
verified cognitive cycle into a structured, persistable PhiArtifact
(goal_signature, reward, confidence, outcome_summary) on every real
production tick (see LearningIntegratedPolicy.configure()'s
integrated_compile_phi in learning/integration.py). Until this module,
nothing ever read a SEQUENCE of those artifacts for the same goal to
notice a pattern worth promoting — Φ compilation was a per-cycle dead end.

Scope, deliberately: promotion here means "recorded as a durable,
versioned, inspectable candidate," never "silently starts executing with
production authority." Auto-synthesizing and registering *executable*
code from an LLM's own summary of past success would itself violate two
other Constitution tenets this codebase otherwise takes seriously —
"capabilities are the boundary between cognition and reality" (a
capability's contract should be something an operator authored and can
reason about, not a side effect of learning) and "learning cannot expand
authority" (a self-registering executable capability IS an authority
expansion). So a repeated pattern becomes a real, versioned
PromotedCapabilityCandidate record — durable, queryable, auditable — that
a human/operator can inspect and decide to actually author a capability
from. That keeps the system "more capable without becoming less
governable" (Constitution tenet #7/#20) instead of trading one for the
other.

Same lazy Redis singleton / never-raises persistence shape as
negotiation_store.py / approval_store.py / execution_checkpoint_store.py.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.pipeline.learning.domain import (
    experience_tenant_id,
    scoped_goal_signature,
)

logger = logging.getLogger("agentos.pipeline.capability_promotion")

_CANDIDATE_KEY_PREFIX = "monkeybrain:promoted_capability:"
_CANDIDATE_INDEX_KEY = "monkeybrain:promoted_capability:index"
_RECIPE_KEY_PREFIX = "monkeybrain:promoted_capability:recipe:"
_ACTIVE_KEY_PREFIX = "monkeybrain:promoted_capability:active:"

_DEFAULT_STREAK_THRESHOLD = 3
_DEFAULT_CONFIDENCE_THRESHOLD = 0.75

_client: Any = None
_connect_attempted = False


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    return f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"


def _get_client() -> Any:
    """Lazy, module-level singleton — same shape as negotiation_store.py's."""
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
        logger.warning(
            "PromotedCapabilityCandidate persistence: Redis unavailable (non-fatal): %s",
            exc,
        )
        _client = None
    return _client


@dataclass
class PromotedCapabilityCandidate:
    """A durable, versioned record that repeated verified cognition
    crossed the promotion threshold for one goal_signature. `version` is
    the count of times this SAME goal_signature has re-crossed the
    threshold (a fresh streak after a `reset`, e.g. following observed
    drift) — real versioned infrastructure, not a cosmetic field."""

    goal_signature: str
    candidate_id: str = ""
    consecutive_successes: int = 0
    confidence: float = 0.0
    outcome_summary: str = ""
    top_signal_summary: str = ""
    version: int = 1
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_signature": self.goal_signature,
            "candidate_id": self.candidate_id,
            "consecutive_successes": self.consecutive_successes,
            "confidence": self.confidence,
            "outcome_summary": self.outcome_summary,
            "top_signal_summary": self.top_signal_summary,
            "version": self.version,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PromotedCapabilityCandidate:
        return PromotedCapabilityCandidate(
            goal_signature=d.get("goal_signature", ""),
            candidate_id=d.get("candidate_id", ""),
            consecutive_successes=int(d.get("consecutive_successes", 0)),
            confidence=float(d.get("confidence", 0.0)),
            outcome_summary=d.get("outcome_summary", ""),
            top_signal_summary=d.get("top_signal_summary", ""),
            version=int(d.get("version", 1)),
            created_at=float(d.get("created_at", time.time())),
        )


@dataclass(frozen=True)
class FrozenPlanStep:
    """One verified plan step — JSON-safe, no LLM, no runtime objects."""

    action: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[int, ...] = ()
    required_permission: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "description": self.description,
            "parameters": dict(self.parameters),
            "depends_on": list(self.depends_on),
            "required_permission": self.required_permission,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> FrozenPlanStep:
        raw_depends = d.get("depends_on") or ()
        return FrozenPlanStep(
            action=str(d.get("action", "")),
            description=str(d.get("description", "")),
            parameters=dict(d.get("parameters") or {}),
            depends_on=tuple(int(i) for i in raw_depends),
            required_permission=str(d.get("required_permission", "")),
        )


@dataclass(frozen=True)
class VerifiedExecutionRecipe:
    """Frozen action sequence from one verified-successful cognitive cycle.

    Promotion replays THIS exact sequence — no LLM re-planning, no
    synthesized code. Authority stays bounded to capabilities already on
    the bus when the operator activates."""

    goal_signature: str
    steps: tuple[FrozenPlanStep, ...]
    source_candidate_id: str = ""
    verified_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_signature": self.goal_signature,
            "steps": [s.to_dict() for s in self.steps],
            "source_candidate_id": self.source_candidate_id,
            "verified_at": self.verified_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> VerifiedExecutionRecipe:
        return VerifiedExecutionRecipe(
            goal_signature=str(d.get("goal_signature", "")),
            steps=tuple(FrozenPlanStep.from_dict(s) for s in (d.get("steps") or [])),
            source_candidate_id=str(d.get("source_candidate_id", "")),
            verified_at=float(d.get("verified_at", time.time())),
        )


def promoted_capability_name(goal_signature: str) -> str:
    """Stable bus name for an activated promoted capability."""
    return f"Promoted::{goal_signature}"


def _step_permission_allowed(step: FrozenPlanStep, context: dict[str, Any]) -> bool:
    """Re-validate planner-declared permissions at replay time."""
    if not step.required_permission:
        return True
    resolved = context.get("_resolved_permissions")
    if resolved is None:
        return False
    if isinstance(resolved, (list, tuple, set, frozenset)):
        return step.required_permission in resolved
    if isinstance(resolved, dict):
        return step.required_permission in resolved.values() or step.required_permission in resolved
    return False


def extract_recipe_from_experience(experience: Any) -> VerifiedExecutionRecipe | None:
    """Capture the verified plan steps from a learning experience.

    Called from the learning integration path only — never registers or
    activates anything."""
    bare_goal_name = str((experience.metadata or {}).get("goal_name", "") or "")
    if not bare_goal_name:
        return None
    goal_signature = scoped_goal_signature(bare_goal_name, experience_tenant_id(experience))
    plan = getattr(experience, "plan", None)
    if plan is None:
        return None
    steps = getattr(plan, "steps", None) or ()
    if not steps:
        return None
    frozen: list[FrozenPlanStep] = []
    for step in steps:
        action = getattr(step, "action", "") or ""
        if not action:
            continue
        frozen.append(
            FrozenPlanStep(
                action=action,
                description=str(getattr(step, "description", "") or ""),
                parameters=dict(getattr(step, "parameters", None) or {}),
                depends_on=tuple(getattr(step, "depends_on", ()) or ()),
                required_permission=str(getattr(step, "required_permission", "") or ""),
            )
        )
    if not frozen:
        return None
    return VerifiedExecutionRecipe(goal_signature=goal_signature, steps=tuple(frozen))


# Process-local fallback when Redis is unavailable (tests, dev). Learning
# writes here too so operator activation can still find a freshly minted
# candidate without requiring Redis.
_memory_candidates: dict[str, PromotedCapabilityCandidate] = {}
_memory_recipes: dict[str, VerifiedExecutionRecipe] = {}
_active_promotions: dict[str, str] = {}  # goal_signature -> candidate_id


def _save_candidate(candidate: PromotedCapabilityCandidate) -> bool:
    _memory_candidates[candidate.candidate_id] = candidate
    client = _get_client()
    if client is None or not candidate.goal_signature:
        return False
    try:
        key = f"{_CANDIDATE_KEY_PREFIX}{candidate.candidate_id}"
        client.set(key, json.dumps(candidate.to_dict()))
        client.sadd(_CANDIDATE_INDEX_KEY, candidate.candidate_id)
        return True
    except Exception as exc:
        logger.warning(
            "_save_candidate(%s) failed: %s",
            candidate.goal_signature,
            exc,
            exc_info=True,
        )
        return False


def _save_recipe(recipe: VerifiedExecutionRecipe) -> bool:
    if not recipe.source_candidate_id:
        return False
    _memory_recipes[recipe.source_candidate_id] = recipe
    client = _get_client()
    if client is None:
        return False
    try:
        key = f"{_RECIPE_KEY_PREFIX}{recipe.source_candidate_id}"
        client.set(key, json.dumps(recipe.to_dict()))
        return True
    except Exception as exc:
        logger.warning(
            "_save_recipe(%s) failed: %s",
            recipe.source_candidate_id,
            exc,
            exc_info=True,
        )
        return False


def load_candidate(candidate_id: str) -> PromotedCapabilityCandidate | None:
    if candidate_id in _memory_candidates:
        return _memory_candidates[candidate_id]
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_CANDIDATE_KEY_PREFIX}{candidate_id}")
        if not raw:
            return None
        candidate = PromotedCapabilityCandidate.from_dict(json.loads(raw))
        _memory_candidates[candidate_id] = candidate
        return candidate
    except Exception as exc:
        logger.debug("load_candidate(%s) failed: %s", candidate_id, exc)
        return None


def load_recipe(candidate_id: str) -> VerifiedExecutionRecipe | None:
    if candidate_id in _memory_recipes:
        return _memory_recipes[candidate_id]
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(f"{_RECIPE_KEY_PREFIX}{candidate_id}")
        if not raw:
            return None
        recipe = VerifiedExecutionRecipe.from_dict(json.loads(raw))
        _memory_recipes[candidate_id] = recipe
        return recipe
    except Exception as exc:
        logger.debug("load_recipe(%s) failed: %s", candidate_id, exc)
        return None


def list_promoted_capabilities() -> tuple[PromotedCapabilityCandidate, ...]:
    """Every durable promotion-candidate record, for an operator (or a
    future authoring tool) to review. Empty tuple, never raises, if Redis
    is unavailable — same fail-soft contract as every other store here."""
    client = _get_client()
    if client is None:
        return ()
    try:
        ids = client.smembers(_CANDIDATE_INDEX_KEY) or ()
        results = []
        for candidate_id in ids:
            raw = client.get(f"{_CANDIDATE_KEY_PREFIX}{candidate_id}")
            if raw:
                results.append(PromotedCapabilityCandidate.from_dict(json.loads(raw)))
        return tuple(sorted(results, key=lambda c: c.created_at))
    except Exception as exc:
        logger.debug("list_promoted_capabilities failed: %s", exc)
        return ()


@dataclass
class _GoalStreak:
    consecutive_successes: int = 0
    last_confidence: float = 0.0
    last_outcome_summary: str = ""
    last_top_signal_summary: str = ""
    times_promoted: int = 0
    promoted_this_run: bool = False
    """True once the CURRENT unbroken streak has already produced a
    candidate -- reset to False the moment the streak breaks, so a
    pattern is proposed exactly once per unbroken run, and can be
    proposed again (as a new version) after a later re-earned streak."""


class CapabilityPromotionTracker:
    """Tracks consecutive verified-successful PhiArtifacts per
    goal_signature and returns a promotion candidate the moment a pattern
    first crosses (streak_threshold, confidence_threshold). Thread-safe —
    actors within a society tick concurrently."""

    def __init__(
        self,
        *,
        streak_threshold: int = _DEFAULT_STREAK_THRESHOLD,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._streaks: dict[str, _GoalStreak] = {}
        self._lock = threading.Lock()
        self._streak_threshold = streak_threshold
        self._confidence_threshold = confidence_threshold

    def observe(
        self,
        *,
        goal_signature: str,
        reward: float,
        confidence: float,
        outcome_summary: str,
        top_signal_summary: str,
        recipe: VerifiedExecutionRecipe | None = None,
    ) -> PromotedCapabilityCandidate | None:
        """Record one PhiArtifact's outcome. Returns a new, already-persisted
        PromotedCapabilityCandidate the moment this goal_signature's streak
        crosses the threshold; None otherwise (including on every later
        repeat of an already-promoted streak — a pattern is proposed once
        per streak, not re-proposed every subsequent cycle). A broken
        streak (verified_success is False) resets the count, so a pattern
        that later starts failing must re-earn promotion rather than
        staying permanently eligible off a stale streak."""
        if not goal_signature:
            return None
        verified_success = reward > 0.0 and confidence >= self._confidence_threshold
        with self._lock:
            streak = self._streaks.setdefault(goal_signature, _GoalStreak())
            if verified_success:
                streak.consecutive_successes += 1
            else:
                streak.consecutive_successes = 0
                streak.promoted_this_run = False
            streak.last_confidence = confidence
            streak.last_outcome_summary = outcome_summary
            streak.last_top_signal_summary = top_signal_summary

            if streak.consecutive_successes >= self._streak_threshold and not streak.promoted_this_run:
                streak.promoted_this_run = True
                streak.times_promoted += 1
                candidate = PromotedCapabilityCandidate(
                    goal_signature=goal_signature,
                    candidate_id=f"{goal_signature}::v{streak.times_promoted}",
                    consecutive_successes=streak.consecutive_successes,
                    confidence=confidence,
                    outcome_summary=outcome_summary,
                    top_signal_summary=top_signal_summary,
                    version=streak.times_promoted,
                )
                _save_candidate(candidate)
                if recipe is not None:
                    recipe = VerifiedExecutionRecipe(
                        goal_signature=recipe.goal_signature,
                        steps=recipe.steps,
                        source_candidate_id=candidate.candidate_id,
                        verified_at=recipe.verified_at,
                    )
                    _save_recipe(recipe)
                logger.info(
                    "capability_promotion: %r crossed promotion threshold "
                    "(%d consecutive verified successes, confidence=%.2f) -- "
                    "recorded as PromotedCapabilityCandidate v%d",
                    goal_signature,
                    streak.consecutive_successes,
                    confidence,
                    streak.times_promoted,
                )
                return candidate
        return None

    def reset(self, goal_signature: str) -> None:
        """Forget an in-memory streak (e.g. after observed drift on an
        already-promoted pattern), so it must re-earn promotion rather
        than staying eligible forever off a stale streak. Does not delete
        the persisted candidate record — that stays as an honest history
        of what WAS promoted, when."""
        with self._lock:
            self._streaks.pop(goal_signature, None)


_default_tracker = CapabilityPromotionTracker()


def default_tracker() -> CapabilityPromotionTracker:
    return _default_tracker


class PromotedDeterministicCapability:
    """Replays a verified execution recipe through an existing capability bus.

    Does NOT introduce new permissions — only sequences capabilities the
    operator already approved when calling activate_promoted_capability().
    Learning never registers this class; only the explicit operator gate does.
    """

    def __init__(self, recipe: VerifiedExecutionRecipe, capability_bus: Any) -> None:
        self._recipe = recipe
        self._bus = capability_bus
        self.goal_signature = recipe.goal_signature
        self.candidate_id = recipe.source_candidate_id
        self.name = promoted_capability_name(recipe.goal_signature)

    def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        import inspect

        context = args.get("context") if isinstance(args.get("context"), dict) else {}
        outcomes: list[dict[str, Any]] = []
        step_success: dict[int, bool] = {}

        for idx, step in enumerate(self._recipe.steps):
            if step.depends_on:
                blocked = [d for d in step.depends_on if not step_success.get(d)]
                if blocked:
                    return {
                        "success": False,
                        "promoted_replay": True,
                        "candidate_id": self.candidate_id,
                        "error": f"blocked: dependency step(s) {blocked} not satisfied",
                        "outcomes": outcomes,
                    }

            if not _step_permission_allowed(step, context):
                return {
                    "success": False,
                    "promoted_replay": True,
                    "candidate_id": self.candidate_id,
                    "error": f"permission denied: {step.required_permission!r}",
                    "outcomes": outcomes,
                }

            capability = self._bus.discover(step.action)
            if capability is None:
                return {
                    "success": False,
                    "promoted_replay": True,
                    "candidate_id": self.candidate_id,
                    "error": f"Capability not found: {step.action}",
                    "outcomes": outcomes,
                }

            handle_args = {
                "action": step.action,
                "parameters": dict(step.parameters),
                "context": context,
            }
            if inspect.iscoroutinefunction(getattr(capability, "handle", None)):
                import asyncio

                result = asyncio.get_event_loop().run_until_complete(capability.handle(handle_args))
            else:
                result = capability.handle(handle_args)

            success = True
            if isinstance(result, dict):
                success = bool(result.get("success", True))
            step_success[idx] = success
            outcomes.append(
                {
                    "step": idx,
                    "action": step.action,
                    "success": success,
                    "result": result,
                }
            )
            if not success:
                return {
                    "success": False,
                    "promoted_replay": True,
                    "candidate_id": self.candidate_id,
                    "outcomes": outcomes,
                    "error": (
                        (result or {}).get("error", f"step {idx} ({step.action}) failed")
                        if isinstance(result, dict)
                        else f"step {idx} ({step.action}) failed"
                    ),
                }

        return {
            "success": True,
            "promoted_replay": True,
            "candidate_id": self.candidate_id,
            "goal_signature": self.goal_signature,
            "outcomes": outcomes,
        }


def _mark_active(goal_signature: str, candidate_id: str) -> None:
    _active_promotions[goal_signature] = candidate_id
    client = _get_client()
    if client is None:
        return
    try:
        client.set(f"{_ACTIVE_KEY_PREFIX}{goal_signature}", candidate_id)
    except Exception as exc:
        logger.debug("_mark_active(%s) failed: %s", goal_signature, exc)


def _clear_active(goal_signature: str) -> None:
    _active_promotions.pop(goal_signature, None)
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(f"{_ACTIVE_KEY_PREFIX}{goal_signature}")
    except Exception as exc:
        logger.debug("_clear_active(%s) failed: %s", goal_signature, exc)


def active_candidate_id(goal_signature: str) -> str | None:
    if goal_signature in _active_promotions:
        return _active_promotions[goal_signature]
    client = _get_client()
    if client is None:
        return None
    try:
        candidate_id = client.get(f"{_ACTIVE_KEY_PREFIX}{goal_signature}")
        if candidate_id:
            _active_promotions[goal_signature] = candidate_id
        return candidate_id or None
    except Exception as exc:
        logger.debug("active_candidate_id(%s) failed: %s", goal_signature, exc)
        return None


def list_active_promotions() -> dict[str, str]:
    """goal_signature -> candidate_id for every operator-activated promotion."""
    return dict(_active_promotions)


def activate_promoted_capability(
    candidate_id: str,
    capability_bus: Any,
) -> PromotedDeterministicCapability | None:
    """Operator-gated: register a verified recipe on the capability bus.

    Learning integration never calls this — only explicit operator action
    closes the loop from candidate to dispatchable execution."""
    recipe = load_recipe(candidate_id)
    candidate = load_candidate(candidate_id)
    if recipe is None or candidate is None:
        logger.warning(
            "activate_promoted_capability: missing candidate or recipe for %r",
            candidate_id,
        )
        return None
    promoted = PromotedDeterministicCapability(recipe, capability_bus)
    capability_bus.register(promoted)
    _mark_active(candidate.goal_signature, candidate_id)
    logger.info(
        "capability_promotion: operator activated %r as %r on capability bus",
        candidate_id,
        promoted.name,
    )
    return promoted


def deactivate_promoted_capability(goal_signature: str, capability_bus: Any) -> bool:
    """Operator-gated: remove an activated promotion from the bus."""
    candidate_id = active_candidate_id(goal_signature)
    if not candidate_id:
        return False
    name = promoted_capability_name(goal_signature)
    caps = getattr(capability_bus, "_capabilities", None)
    if isinstance(caps, dict):
        caps.pop(name, None)
    _clear_active(goal_signature)
    logger.info(
        "capability_promotion: operator deactivated %r (%s)",
        goal_signature,
        candidate_id,
    )
    return True


def try_resolve_promoted_plan(goal_signature: str) -> Any | None:
    """Return a frozen belief_state.Plan when an operator has activated a promotion.

    Called from LLMPlanner before any LLM call — deterministic replay of
    the verified recipe, not learning-driven authority expansion."""
    candidate_id = active_candidate_id(goal_signature)
    if not candidate_id:
        return None
    recipe = load_recipe(candidate_id)
    if recipe is None or not recipe.steps:
        return None
    from src.monkey_brain.kernel.pipeline.belief_state import Plan, PlanStep

    return Plan(
        goal=goal_signature,
        steps=tuple(
            PlanStep(
                action=step.action,
                description=step.description,
                parameters=dict(step.parameters),
                depends_on=step.depends_on,
                required_permission=step.required_permission,
                confidence=1.0,
            )
            for step in recipe.steps
        ),
        confidence=1.0,
        planner="promoted_deterministic",
        metadata={
            "promoted": True,
            "candidate_id": candidate_id,
            "goal_id": goal_signature,
        },
    )


def reset_promotion_state_for_tests() -> None:
    """Clear in-memory promotion state — tests only."""
    _memory_candidates.clear()
    _memory_recipes.clear()
    _active_promotions.clear()
