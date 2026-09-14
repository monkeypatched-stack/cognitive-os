"""Practical strategic reasoning for interacting CognitiveOS actors.

This module is deliberately small and policy-oriented.  It adds a strategic
stage around the existing planning/coordination pipeline; it does not replace
planning or make routine single-actor requests pay a negotiation cost.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from src.monkey_brain.kernel.compile import _obs

UtilityFunction = Callable[["Strategy", Mapping[str, Any]], float]


@dataclass(frozen=True)
class Strategy:
    name: str
    action: Any = None
    expected_outcome: Mapping[str, Any] = field(default_factory=dict)
    resource_requirements: Mapping[str, float] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyProfile:
    actor_id: str
    goals: tuple[str, ...] = ()
    preferences: Mapping[str, float] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    resources: Mapping[str, float] = field(default_factory=dict)
    strategies: tuple[Strategy, ...] = ()
    utility_function: UtilityFunction | None = None
    risk_profile: Mapping[str, float] = field(default_factory=dict)
    negotiation_policy: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UtilityEvaluation:
    actor_id: str
    strategy: Strategy
    utility: float
    feasible: bool = True
    rationale: str = ""


@dataclass(frozen=True)
class NegotiationMessage:
    actor_id: str
    text: str
    proposal: Strategy | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class NegotiationAgreement:
    agreement_id: str = field(default_factory=lambda: uuid4().hex)
    topic: str = ""
    participants: tuple[str, ...] = ()
    chosen_strategy: Strategy | None = None
    utilities: Mapping[str, float] = field(default_factory=dict)
    messages: tuple[NegotiationMessage, ...] = ()
    cooperative: bool = False
    competitive: bool = False
    equilibrium: bool = False
    """True iff chosen_strategy is Nash-equilibrium-stable -- see
    GameTheoryRuntime.is_nash_equilibrium(). False means no pure-strategy
    equilibrium existed among the feasible candidates and the
    highest-aggregate-utility strategy was used as a fallback instead."""
    status: str = "settled"
    rationale: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class GameTheoryMetrics:
    negotiations_started: int = 0
    negotiations_completed: int = 0
    utility_evaluations: int = 0
    strategies_considered: int = 0
    successful_agreements: int = 0
    failed_negotiations: int = 0
    cooperative_decisions: int = 0
    competitive_decisions: int = 0
    negotiation_time_ms: float = 0.0
    actors_per_negotiation: int = 0

    def as_dict(self) -> dict[str, float | int]:
        completed = self.negotiations_completed
        return {
            "negotiations_started": self.negotiations_started,
            "negotiations_completed": completed,
            "average_negotiation_time_ms": (self.negotiation_time_ms / completed if completed else 0.0),
            "utility_evaluations": self.utility_evaluations,
            "strategies_considered": self.strategies_considered,
            "successful_agreements": self.successful_agreements,
            "failed_negotiations": self.failed_negotiations,
            "cooperative_decisions": self.cooperative_decisions,
            "competitive_decisions": self.competitive_decisions,
            "average_actors_per_negotiation": (
                self.actors_per_negotiation / self.negotiations_started if self.negotiations_started else 0.0
            ),
        }


class GameTheoryRuntime:
    """Evaluate strategies and settle explainable multi-actor agreements.

    ``world_state`` is the durable boundary: callers may replace it with a
    world-model adapter, while the default dictionary is useful for local
    runtimes and tests.  Agreements are written only after all policy checks
    pass, so later planning can consume them as ordinary world state.
    """

    def __init__(self, *, world_state: dict[str, Any] | None = None, metric_sink: Any = None) -> None:
        self.world_state = world_state if world_state is not None else {}
        self.metric_sink = metric_sink
        self.metrics = GameTheoryMetrics()
        self._agreements: dict[str, NegotiationAgreement] = {}

    @staticmethod
    def requires_strategic_reasoning(
        actor_ids: Iterable[str],
        *,
        shared_resources: Iterable[str] = (),
        interacting_objectives: bool = False,
    ) -> bool:
        actors = tuple(dict.fromkeys(actor_ids))
        return len(actors) > 1 and (bool(tuple(shared_resources)) or interacting_objectives)

    def evaluate(
        self, profile: StrategyProfile, context: Mapping[str, Any] | None = None
    ) -> tuple[UtilityEvaluation, ...]:
        context = context or {}
        result: list[UtilityEvaluation] = []
        for strategy in profile.strategies:
            feasible, reason = self._check_constraints(profile, strategy, context)
            if not feasible:
                value = -math.inf
            elif profile.utility_function is not None:
                value = float(profile.utility_function(strategy, context))
                reason = reason or "custom utility function"
            else:
                value = self._default_utility(profile, strategy, context)
                reason = reason or "weighted preferences and expected outcomes"
            result.append(UtilityEvaluation(profile.actor_id, strategy, value, feasible, reason))
        result.sort(key=lambda item: (-item.utility, item.strategy.name))
        self.metrics.utility_evaluations += len(result)
        self.metrics.strategies_considered += len(result)
        _obs.counter("game_theory.utility_evaluations", len(result), actor_id=profile.actor_id)
        return tuple(result)

    def choose(self, profile: StrategyProfile, context: Mapping[str, Any] | None = None) -> UtilityEvaluation:
        evaluations = self.evaluate(profile, context)
        if not evaluations or not evaluations[0].feasible:
            raise ValueError(f"Actor {profile.actor_id} has no feasible strategy")
        return evaluations[0]

    def negotiate(
        self,
        topic: str,
        profiles: Iterable[StrategyProfile],
        *,
        context: Mapping[str, Any] | None = None,
        messages: Iterable[NegotiationMessage] = (),
    ) -> NegotiationAgreement:
        started = time.perf_counter()
        profiles = tuple(profiles)
        if len(profiles) < 2:
            raise ValueError("negotiation requires at least two actors")
        self.metrics.negotiations_started += 1
        self.metrics.actors_per_negotiation += len(profiles)
        evaluations = {p.actor_id: self.evaluate(p, context) for p in profiles}
        candidates = {e.strategy.name: e.strategy for choices in evaluations.values() for e in choices if e.feasible}
        scored: list[tuple[float, Strategy, dict[str, float]]] = []
        for strategy in candidates.values():
            utilities = {
                actor: next(
                    (e.utility for e in choices if e.strategy.name == strategy.name),
                    -math.inf,
                )
                for actor, choices in evaluations.items()
            }
            if all(math.isfinite(v) for v in utilities.values()):
                scored.append((sum(utilities.values()), strategy, utilities))
        if not scored:
            self.metrics.failed_negotiations += 1
            raise ValueError(f"No mutually feasible strategy for {topic}")

        # Prefer an equilibrium-stable candidate (no participant has an
        # incentive to prefer a different jointly-feasible strategy) over
        # pure aggregate-utility maximization; fall back to the latter,
        # explicitly, when no pure-strategy equilibrium exists among the
        # feasible candidates -- see is_nash_equilibrium().
        equilibria = [item for item in scored if self.is_nash_equilibrium(evaluations, item[1])]
        pool = equilibria or scored
        equilibrium_found = bool(equilibria)
        _, chosen, utilities = max(pool, key=lambda item: (item[0], -len(item[1].name), item[1].name))

        cooperative = sum(utilities.values()) > max(utilities.values()) * len(utilities) * 0.8
        competitive = not cooperative or len({round(v, 8) for v in utilities.values()}) > 1
        rationale = (
            "Selected an equilibrium-stable strategy: no participant has a "
            "jointly-feasible alternative it strictly prefers."
            if equilibrium_found
            else "No pure-strategy equilibrium existed among feasible candidates; "
            "selected the strategy with the highest aggregate actor utility."
        )
        agreement = NegotiationAgreement(
            topic=topic,
            participants=tuple(p.actor_id for p in profiles),
            chosen_strategy=chosen,
            utilities=utilities,
            messages=tuple(messages),
            cooperative=cooperative,
            competitive=competitive,
            equilibrium=equilibrium_found,
            rationale=rationale,
        )
        self._agreements[agreement.agreement_id] = agreement
        self.world_state.setdefault("negotiated_agreements", {})[agreement.agreement_id] = {
            "topic": topic,
            "participants": list(agreement.participants),
            "strategy": chosen.name,
            "utilities": dict(utilities),
            "equilibrium": equilibrium_found,
            "rationale": agreement.rationale,
        }
        self.metrics.negotiations_completed += 1
        self.metrics.successful_agreements += 1
        if cooperative:
            self.metrics.cooperative_decisions += 1
        if competitive:
            self.metrics.competitive_decisions += 1
        elapsed = (time.perf_counter() - started) * 1000
        self.metrics.negotiation_time_ms += elapsed
        _obs.counter("game_theory.negotiations_completed")
        _obs.event("game_theory.agreement", agreement_id=agreement.agreement_id, topic=topic)
        return agreement

    @staticmethod
    def is_nash_equilibrium(
        evaluations: Mapping[str, tuple["UtilityEvaluation", ...]],
        chosen_strategy: Strategy,
    ) -> bool:
        """Best-response stability check over evaluate()'s own output: true
        iff no actor has a feasible strategy, among the ones it itself
        evaluated, that it strictly prefers over chosen_strategy.

        This module's negotiate() has every participant adopt the *same*
        named strategy (a joint agreement, not independent per-player
        actions), so "holding others' strategies fixed" collapses to: would
        any single actor rather the group had picked something else? If
        not, no one has an incentive to unilaterally push for a different
        outcome -- the coordination-game reading of Nash equilibrium that
        fits this module's shared-strategy negotiation model, not a general
        per-player independent-action bimatrix search.
        """
        for actor_id, choices in evaluations.items():
            own_utility = next(
                (e.utility for e in choices if e.strategy.name == chosen_strategy.name),
                None,
            )
            if own_utility is None:
                return False
            best_alternative = max((e.utility for e in choices if e.feasible), default=-math.inf)
            if best_alternative > own_utility:
                return False
        return True

    def get_agreement(self, agreement_id: str) -> NegotiationAgreement | None:
        return self._agreements.get(agreement_id)

    def metrics_snapshot(self) -> dict[str, float | int]:
        return self.metrics.as_dict()

    @staticmethod
    def _check_constraints(
        profile: StrategyProfile, strategy: Strategy, context: Mapping[str, Any]
    ) -> tuple[bool, str]:
        for resource, required in strategy.resource_requirements.items():
            available = context.get("resources", profile.resources).get(resource, 0)
            if available < required:
                return False, f"requires {required} {resource}; {available} available"
        for key, expected in profile.constraints.items():
            if key in strategy.attributes and strategy.attributes[key] != expected:
                return False, f"violates constraint {key}={expected!r}"
        return True, ""

    @staticmethod
    def _default_utility(profile: StrategyProfile, strategy: Strategy, context: Mapping[str, Any]) -> float:
        score = 0.0
        values = {**strategy.attributes, **strategy.expected_outcome}
        for preference, weight in profile.preferences.items():
            value = values.get(preference, 0.0)
            score += float(weight) * (float(value) if isinstance(value, (int, float)) else (1.0 if value else 0.0))
        risk = float(profile.risk_profile.get("risk_aversion", 0.0))
        score -= risk * float(values.get("risk", 0.0) or 0.0)
        return score
