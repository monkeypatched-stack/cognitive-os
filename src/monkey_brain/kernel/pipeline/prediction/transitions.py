"""Transition Prediction Engine (Step 11.2) — predicts future world states
from the semantic world model, without executing anything.

    Current World + Beliefs + Candidate Actions -> Future World

The first algorithmic piece of Step 11 (11.1 was pure data). Deliberately
read-only and side-effect-free: `TransitionPredictionEngine` never calls
`ExecutionEngine`, never mutates `world_snapshot`/`belief_state` (both
remain Any-typed inputs, read via duck-typed `getattr`, never imported as
real types — same reasoning domain.py's own module docstring gives), and
every public method returns new values rather than touching its inputs.

WorldTransition/TransitionKind/TransitionModel live here rather than in
domain.py, matching the precedent every prior mini-arc's first behavioral
module set (Step 9.4's RetryExecutor types in retry.py, Step 10.3's
RewardEngine types in reward.py): sub-step-specific behavioral types stay
with their engine.

Support for all four required transition kinds:
    - DETERMINISTIC: exactly one known outcome at ~certain probability.
    - PROBABILISTIC: multiple known outcomes, each reasonably confident.
    - UNCERTAIN: any outcome whose own confidence is below threshold,
      regardless of how many outcomes are known.
    - UNKNOWN (missing knowledge): no registered transitions for this
      action at all -- the engine returns an explicit "we don't know"
      fallback rather than guessing or defaulting to success.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.monkey_brain.kernel.pipeline.prediction.domain import (
    Prediction,
    PredictionConfidence,
    PredictionOutcome,
)


class TransitionKind(Enum):
    DETERMINISTIC = "deterministic"
    PROBABILISTIC = "probabilistic"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorldTransition:
    """One possible effect of taking an action: what changes in the world,
    how likely that specific outcome is among alternatives (probability),
    and how sure we are about that probability estimate itself (confidence
    -- distinct axes, e.g. a transition can be the *only* known outcome
    (probability=1.0) while still being poorly attested (confidence=0.2))."""

    action: Any = None
    description: str = ""
    kind: TransitionKind = TransitionKind.UNKNOWN
    probability: float = 0.0
    resulting_world_delta: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    grounding_fact_key: str = ""
    """Optional "entity.attribute" key -- if belief_state exposes a
    matching fact, this transition's confidence is grounded against that
    fact's own confidence instead of whatever static value it was
    registered with."""

    def to_dict(self) -> dict[str, Any]:
        # action is real-world always a plain action-key string (see
        # TransitionModel.learn_from_execution's own action=action_key)
        # — str() is a safe, honest fallback for anything else rather
        # than failing to serialize a real learned transition.
        return {
            "action": (
                self.action if isinstance(self.action, (str, int, float, bool, type(None))) else str(self.action)
            ),
            "description": self.description,
            "kind": self.kind.value,
            "probability": self.probability,
            "resulting_world_delta": self.resulting_world_delta,
            "confidence": self.confidence,
            "grounding_fact_key": self.grounding_fact_key,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorldTransition":
        return cls(
            action=d.get("action"),
            description=d.get("description", ""),
            kind=TransitionKind(d["kind"]) if "kind" in d else TransitionKind.UNKNOWN,
            probability=d.get("probability", 0.0),
            resulting_world_delta=dict(d.get("resulting_world_delta") or {}),
            confidence=d.get("confidence", 0.0),
            grounding_fact_key=d.get("grounding_fact_key", ""),
        )


@dataclass(frozen=True)
class TransitionModel:
    """A pluggable, static knowledge source: (goal_key, action_key) -> the
    transitions it's known to produce. Step 11.7's integration is what
    would populate this from a real semantic world model; here it's just
    data the engine consults.

    Goal-scoped (Cross-Goal Plan Contamination fix): keyed by (goal_key,
    action_key), not action_key alone. A real, learned failure for one
    goal (e.g. repeated Payment failures while pursuing "buy groceries")
    must not silently suppress an unrelated goal's plan that happens to
    share the same action name (e.g. "get medicine" also using Payment) --
    a lookup for (new_goal_key, action_key) with no entry falls through to
    the honest UNKNOWN/0.5 default (see TransitionPredictionEngine.
    _unknown_transition below) rather than inheriting another goal's
    specifically bad learned value. goal_key is expected to already be
    canonicalized (kernel/pipeline/planning/goal_key.py::canonicalize_goal)
    by the caller, matching current_plan_store.py's convention -- this
    module doesn't re-derive it, to keep exactly one normalization."""

    known_transitions: dict[tuple[str, str], tuple[WorldTransition, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # JSON has no tuple-key dicts -- serialize as a list of entries
        # rather than encoding (goal_key, action_key) into a single string
        # key (which would need an escaping scheme for values containing
        # the separator).
        return {
            "known_transitions": [
                {
                    "goal_key": goal_key,
                    "action_key": action_key,
                    "transitions": [t.to_dict() for t in transitions],
                }
                for (
                    goal_key,
                    action_key,
                ), transitions in self.known_transitions.items()
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TransitionModel":
        raw = d.get("known_transitions")
        if not isinstance(raw, list):
            # Pre-goal-scoping persisted data was a flat {action_key: [...]}
            # dict (or this is missing/malformed) -- discard rather than
            # guess which goal it belonged to. That data represents exactly
            # the cross-goal-contaminated state this fix removes; carrying
            # it forward under a fabricated goal_key would be dishonest.
            return cls()
        known: dict[tuple[str, str], tuple[WorldTransition, ...]] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            goal_key = str(entry.get("goal_key", ""))
            action_key = str(entry.get("action_key", ""))
            transitions = tuple(WorldTransition.from_dict(t) for t in (entry.get("transitions") or []))
            known[(goal_key, action_key)] = transitions
        return cls(known_transitions=known)

    def learn_from_execution(
        self,
        action_key: str,
        success: bool,
        confidence: float = 0.5,
        world_delta: dict[str, Any] | None = None,
        learning_rate: float = 0.1,
        goal_key: str = "",
    ) -> "TransitionModel":
        """Create a new TransitionModel with the given (goal_key,
        action_key)'s transition updated based on observed execution
        outcome.

        Each observed execution creates or reinforces a transition:
        - If no transition exists, create one with observed outcome.
        - If transitions exist, blend using exponential moving average.

        `confidence` is the observation quality (how sure we are about the
        outcome). `success` determines direction: success raises probability,
        failure lowers it. goal_key scopes the learned evidence to the
        goal it was actually observed under (default "" for callers that
        don't yet thread a goal through -- kept working, but distinct from,
        and not readable as, any real goal_key)."""
        key = (goal_key, action_key)
        existing = self.known_transitions.get(key, ())

        # Observed probability: success=high, failure=low
        # For success: probability is bounded by confidence (high confidence = high probability)
        # For failure: probability is inversely related to confidence (high confidence = low probability)
        observed_prob = min(0.95, confidence) if success else max(0.05, 1.0 - confidence)
        # BUT: if confidence is very low (epistemic loss high), don't trust the failure either
        # Use the confidence directly as the probability for both cases to be conservative
        if confidence < 0.3:
            # Low confidence observation - be conservative
            observed_prob = 0.5  # Neutral probability when uncertain

        if not existing:
            new_transition = WorldTransition(
                action=action_key,
                description=f"Learned from execution: {'success' if success else 'failure'}",
                kind=(TransitionKind.DETERMINISTIC if observed_prob > 0.9 else TransitionKind.PROBABILISTIC),
                probability=round(observed_prob, 4),
                resulting_world_delta=world_delta or {},
                confidence=min(1.0, confidence + learning_rate),
            )
            new_transitions = (new_transition,)
        else:
            blended = []
            for t in existing:
                old_p = t.probability
                blended_p = old_p * (1 - learning_rate) + observed_prob * learning_rate
                blended.append(
                    WorldTransition(
                        action=action_key,
                        description=f"Learned: {'success' if success else 'failure'} (blended p={blended_p:.3f})",
                        kind=(
                            TransitionKind.DETERMINISTIC
                            if blended_p > 0.9
                            else (TransitionKind.PROBABILISTIC if blended_p > 0.4 else TransitionKind.UNCERTAIN)
                        ),
                        probability=round(blended_p, 4),
                        resulting_world_delta=world_delta or t.resulting_world_delta,
                        confidence=min(1.0, t.confidence + learning_rate * 0.5),
                        grounding_fact_key=t.grounding_fact_key,
                    )
                )
            new_transitions = tuple(blended)

        new_known = {**self.known_transitions, key: new_transitions}
        return TransitionModel(known_transitions=new_known)


class TransitionPredictionEngine:
    """Predicts world transitions for candidate actions, grounded in
    belief state where possible, without executing anything."""

    def __init__(self, model: TransitionModel | None = None, uncertainty_threshold: float = 0.4) -> None:
        self._model = model or TransitionModel()
        self._uncertainty_threshold = uncertainty_threshold

    # ── Single-action transitions ───────────────────────────────────────

    def predict_transitions(
        self,
        world_snapshot: Any,
        belief_state: Any,
        action: Any,
        goal_key: str = "",
    ) -> tuple[WorldTransition, ...]:
        """All known possible outcomes of one action, grounded and
        classified. Returns a single UNKNOWN transition if nothing is
        registered for this (goal_key, action) -- missing knowledge is
        represented explicitly, never silently treated as success, and a
        different goal's specifically bad learned evidence for the same
        action name is never substituted in either."""
        key = self._action_key(action)
        registered = self._model.known_transitions.get((goal_key, key))
        if not registered:
            return (self._unknown_transition(action),)

        grounded = self._ground_in_belief(registered, belief_state)
        return self._classify(grounded)

    # ── Whole-plan future-world folding ─────────────────────────────────

    def predict_future_world(
        self,
        world_snapshot: Any,
        belief_state: Any,
        actions: tuple[Any, ...],
        goal_key: str = "",
    ) -> dict[str, Any]:
        """Folds each action's most-likely transition into one predicted
        world-state delta -- a read-only summary of "what the world would
        look like," never a mutation of world_snapshot itself."""
        predicted_state: dict[str, Any] = {}
        for action in actions:
            chosen = self.most_likely(self.predict_transitions(world_snapshot, belief_state, action, goal_key))
            predicted_state.update(chosen.resulting_world_delta)
        return predicted_state

    # ── Full Prediction domain object ───────────────────────────────────

    def predict(
        self,
        world_snapshot: Any,
        belief_state: Any,
        actions: tuple[Any, ...],
        *,
        time_horizon: float = 0.0,
        goal_key: str = "",
    ) -> Prediction:
        """Wraps predict_future_world()/predict_transitions() into a full
        Step 11.1 Prediction. expected_utility stays 0.0 -- Step 11.5/11.6
        compute that; this step only predicts world state and confidence."""
        predicted_state: dict[str, Any] = {}
        outcomes: list[PredictionOutcome] = []
        confidences: list[float] = []

        for action in actions:
            transitions = self.predict_transitions(world_snapshot, belief_state, action, goal_key)
            chosen = self.most_likely(transitions)
            predicted_state.update(chosen.resulting_world_delta)
            confidences.append(chosen.confidence)
            outcomes.append(
                PredictionOutcome(
                    description=chosen.description or self._action_key(action),
                    success=chosen.kind != TransitionKind.UNKNOWN and chosen.probability >= 0.5,
                    probability=chosen.probability,
                    utility=0.0,
                    metadata={
                        "kind": chosen.kind.value,
                        "action": self._action_key(action),
                    },
                )
            )

        overall_confidence = min(confidences) if confidences else 0.0

        return Prediction(
            belief_state=belief_state,
            world_snapshot=predicted_state,
            predicted_outcomes=tuple(outcomes),
            confidence=PredictionConfidence(
                point_estimate=overall_confidence,
                rationale=(
                    f"minimum per-action transition confidence across {len(outcomes)} action(s)"
                    if outcomes
                    else "no actions predicted"
                ),
            ),
            expected_utility=0.0,
            time_horizon=time_horizon,
        )

    def most_likely(self, transitions: tuple[WorldTransition, ...]) -> WorldTransition:
        """Public utility -- Step 11.3's SimulationEngine reuses this when
        picking which transition to apply at each simulated plan step,
        rather than duplicating the selection rule."""
        return max(transitions, key=lambda t: t.probability)

    # ── Internals ────────────────────────────────────────────────────────

    def _action_key(self, action: Any) -> str:
        """Level 49 (GS-4900): must match the SAME key
        comparison/integration.py's _learn_transitions uses when it
        records what was actually learned (plan_steps[i].action — the
        short canonical name like "Payment", not the long human-readable
        .description). Checked BEFORE description/name — getting this
        backwards means Learn and Predict silently key their entries
        under two DIFFERENT strings for the exact same real action, so
        known_transitions.get(key) always misses regardless of how much
        was genuinely learned, and every prediction quietly falls back to
        the naive "no knowledge registered" default forever. Caught
        live: after real, repeated Payment failures drove its learned
        probability down to 0.15, a later tick's prediction for that same
        step still showed probability=1.0 — the Learn -> Predict link was
        completely severed by this mismatch, not by any lack of learning.
        """
        return str(
            getattr(action, "action", None)
            or getattr(action, "description", None)
            or getattr(action, "name", None)
            or action
        )

    def _unknown_transition(self, action: Any) -> WorldTransition:
        return WorldTransition(
            action=action,
            description=f"Unknown effect of '{self._action_key(action)}' (no knowledge registered)",
            kind=TransitionKind.UNKNOWN,
            # Cognitive Loop Verification: this was 1.0 — a transition
            # with literally no learned data reported as a GUARANTEED
            # success, which compounds across a whole plan (RiskEngine.
            # _path_probability multiplies per-step probability) into a
            # "100% success" headline for a plan the system has zero
            # actual evidence about. 0.5 is the honest "no informative
            # prior" value — neither guaranteed success nor guaranteed
            # failure.
            probability=0.5,
            resulting_world_delta={},
            confidence=0.0,
        )

    def _ground_in_belief(
        self, transitions: tuple[WorldTransition, ...], belief_state: Any
    ) -> tuple[WorldTransition, ...]:
        """Duck-typed: if belief_state exposes `.facts` (entity/attribute/
        confidence-shaped, matching belief_state.Fact's real shape without
        importing it), a transition tagged with a matching
        grounding_fact_key has its confidence replaced by that fact's own
        confidence -- predictions get more certain as beliefs do, without
        this module knowing what a real Fact object is."""
        facts = getattr(belief_state, "facts", None) or ()
        fact_confidence: dict[str, float] = {}
        for fact in facts:
            entity = getattr(fact, "entity", None)
            attribute = getattr(fact, "attribute", None)
            confidence = getattr(fact, "confidence", None)
            if entity and attribute and confidence is not None:
                fact_confidence[f"{entity}.{attribute}"] = confidence

        if not fact_confidence:
            return transitions

        grounded = []
        for t in transitions:
            if t.grounding_fact_key and t.grounding_fact_key in fact_confidence:
                grounded.append(dataclasses.replace(t, confidence=fact_confidence[t.grounding_fact_key]))
            else:
                grounded.append(t)
        return tuple(grounded)

    def _classify(self, transitions: tuple[WorldTransition, ...]) -> tuple[WorldTransition, ...]:
        if not transitions:
            return transitions
        if any(t.confidence < self._uncertainty_threshold for t in transitions):
            kind = TransitionKind.UNCERTAIN
        elif len(transitions) == 1 and transitions[0].probability >= 0.999:
            kind = TransitionKind.DETERMINISTIC
        else:
            kind = TransitionKind.PROBABILISTIC
        return tuple(dataclasses.replace(t, kind=kind) for t in transitions)
