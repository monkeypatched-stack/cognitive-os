"""Prediction domain model (Step 11.1) — a formal representation of
prediction concepts, independent of any prediction algorithm.

Mirrors the "data now, algorithm later" split every prior mini-arc used:
Step 8.1 (planning), Step 9.1 (execution), Step 10.1 (learning) all
defined pure data first, then built behavior on top in later sub-steps.
This step defines WHAT a prediction, scenario, and confidence estimate
are — not HOW to predict future world states (Step 11.2), simulate plans
(11.3), reason counterfactually (11.4), quantify risk (11.5), evaluate
multiple scenarios (11.6), or select a prediction policy (11.10).

Does not modify LearningEngine (belief_runtime.py's Learn stage / the
learning/ subpackage's runtime wiring) or ExecutionEngine (execution.py /
action_executor.py) — confirmed by an ownership-boundary test that parses
actual imports, the same AST-based check Step 10.1 introduced after a
substring-scan false positive.

Why belief_state / world_snapshot are Any
-------------------------------------------
Same reasoning Step 10.1 gave for LearningExperience.goal/plan/execution:
a Prediction has to work from *any* cognitive cycle's belief state and
world snapshot — including the default LLMPlanner/ActionExecutor
path, whose belief_state.BeliefState and execution_state.WorldSnapshot are
real, protected-file types this module must not import (importing them
here would be a step toward needing to modify those files' shape later,
exactly what Step 11.1 forbids for LearningEngine/ExecutionEngine).

Why Provenance is reused, not reinvented
-------------------------------------------
`learning.domain.Provenance` (actor_id, tenant_id, run_id, source,
recorded_at) is already a generic, algorithm-independent "who/when/where
this record came from" concept with no learning-specific behavior —
reusing it here is the same deliberate cross-subsystem reuse Step 9.1 used
for ExecutionStep.operator: PlanningOperator (a genuinely shared concept,
not a new near-duplicate type). This is reuse of a pure-data type Step
10.1 already proved has zero runtime coupling, not a dependency on
LearningEngine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.learning.domain import Provenance


@dataclass(frozen=True)
class PredictionConfidence:
    """A structured confidence estimate — a point value plus the interval
    and rationale around it. Richer than a bare float because Step 11.5
    (Risk & Uncertainty Analysis) needs somewhere to attach confidence
    intervals and named uncertainty sources; this is that shape, defined
    now as data, filled in with real interval math later."""

    point_estimate: float = 0.5
    lower_bound: float = 0.0
    upper_bound: float = 1.0
    rationale: str = ""
    uncertainty_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredictionOutcome:
    """One predicted outcome of a candidate future."""

    description: str = ""
    success: bool = False
    probability: float = 0.0
    utility: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Prediction:
    """The central type — one complete predicted future, given a belief
    state, a world snapshot, and a set of assumptions."""

    prediction_id: str = field(default_factory=lambda: uuid4().hex)
    belief_state: Any = None
    world_snapshot: Any = None
    assumptions: tuple[str, ...] = ()
    predicted_outcomes: tuple[PredictionOutcome, ...] = ()
    confidence: PredictionConfidence = field(default_factory=PredictionConfidence)
    expected_utility: float = 0.0
    time_horizon: float = 0.0
    provenance: Provenance = field(default_factory=Provenance)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionRequest:
    """What to predict, and from what state — the input to the prediction
    subsystem. goal/plan are Any for the same reason LearningExperience's
    were (see module docstring): must work with whatever the actual
    planning/execution engines in use produced, not just the rich Step
    8.7/9.7 pipelines."""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    goal: Any = None
    plan: Any = None
    belief_state: Any = None
    world_snapshot: Any = None
    time_horizon: float = 0.0
    actor_id: str = ""
    tenant_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionContext:
    """The working scope threaded through the prediction pipeline —
    mirrors PlanningContext (8.1) / ExecutionContext (9.1) /
    LearningContext (10.1): the request plus whatever assumptions and
    candidates have accumulated so far in this cycle."""

    request: PredictionRequest = field(default_factory=PredictionRequest)
    assumptions: tuple[str, ...] = ()
    candidates: tuple[PredictionCandidate, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionCandidate:
    """One scenario under consideration — a Prediction plus how likely it
    is and whether it was rejected. Mirrors PlanCandidate (8.1) wrapping
    Plan, and matches the acceptance criteria's "Scenario A / Scenario B /
    Scenario C" shape (scenario_label + probability)."""

    candidate_id: str = field(default_factory=lambda: uuid4().hex)
    prediction: Prediction = field(default_factory=Prediction)
    scenario_label: str = ""
    probability: float = 0.0
    rejected: bool = False
    rejection_reason: str = ""


@dataclass(frozen=True)
class PredictionTrace:
    """Lightweight, data-only trace placeholder — plain message strings,
    matching the exact precedent Step 8.1's Plan.trace: tuple[str, ...]
    set ("the structured trace model ... is a later deliverable, not
    invented early here"). Step 11.9 builds the real explainability
    engine (scenarios explored, rejected futures, confidence rationale)
    on top of this shape."""

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    entries: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionResult:
    """The prediction subsystem's final output — mirrors LearningResult
    (10.1) as "the enhanced stage output": which candidate was selected,
    every candidate considered, and why."""

    prediction_id: str = ""
    candidates: tuple[PredictionCandidate, ...] = ()
    selected: PredictionCandidate | None = None
    recommendation: str = ""
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
