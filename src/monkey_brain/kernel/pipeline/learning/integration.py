"""Runtime Integration (Step 10.7) + Φ Compiler wiring (Step 10.8) — wires
Steps 10.2-10.6's learning pipeline into the LIVE CognitiveRuntime's Learn
stage, and Step 10.8's PhiCompiler into the Compile-Φ stage, with zero
modifications to belief_runtime.py or cognitive_policy.py.

Extension point used
---------------------
Unlike planning_engine/execution_engine (Steps 8.7/9.7), CognitiveRuntime
has no `learning_engine` constructor parameter — the Learn stage is
hardcoded to `self._learn` inside `__init__`'s call to
`self._policy.configure(...)`. But that call only happens when
`not self._policy._stages` (cognitive_policy.py), and `CognitivePolicy`'s
own docstring explicitly names this as the sanctioned customization
surface: "Subclass or replace to implement alternative control flows,"
listing "Execute without learning (inference-only)" as a worked example of
exactly this kind of Learn-stage-level override.

LearningIntegratedPolicy subclasses CognitivePolicy and overrides
configure() — the same technique the codebase's own RecursivePlanningPolicy
already uses for the Plan/Predict stages. Because configure() is called
FROM WITHIN CognitiveRuntime.__init__ with `learn=self._learn` (the real
runtime instance's own bound method), the override receives a correctly-
bound reference with no double-construction or private-attribute reaching.

Why the original _learn still runs first
------------------------------------------
belief_runtime.py's own lifecycle docstring says "Predict → reads
hypotheses + confidence, writes predictions" — _predict (untouched, no
part of Step 10) depends on belief.hypotheses, which only the ORIGINAL
_learn populates (add_hypothesis for low-confidence facts, source
coverage, execution outcome). Skipping that would silently starve
_predict of its input, changing its observable output — a real
backward-compatibility break, not a hypothetical one (confirmed by
reading _predict directly, not assumed). So LearningIntegratedPolicy runs
the original `learn` callback first, unmodified, then layers the new
capture -> reward -> belief -> world -> policy pipeline on top,
*adding* keys to state.learning rather than replacing the dict Step 8/9's
_learn already produces — no downstream reader of state.learning loses
data it depended on (confirmed: state.learning has exactly one writer,
_learn itself, and no other reader in the codebase besides tests).

Compile-Φ wiring (Step 10.8)
-----------------------------
Unlike Learn, _compile_phi has no legacy behavior to preserve — its
current body is a one-line placeholder (`state.phi = None`, docstring:
"Placeholder where Q-values become transition weights"), and `state.phi`
has exactly one downstream reader: `run()` packages it verbatim into the
final result dict. Replacing None with a real PhiArtifact is a strict
enhancement, confirmed by reading both call sites directly rather than
assumed.

To avoid recomputing capture_experience()/resolve_policy().learn() a
second time inside compile_phi (which would mint a fresh, different
experience_id via uuid4()), integrated_learn hands the experience/result
pair it already computed to integrated_compile_phi through a
leading-underscore attribute on the CognitiveState instance
(`state._phi_pipeline_input`) — a same-cycle, same-object handoff between
two stages of the same policy class, not a state.learning field, so
state.learning's dict shape from Step 10.7 stays exactly as documented
there (no internal objects leaking into what was a clean, JSON-safe
summary dict).
"""

from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.pipeline.cognitive_policy import CognitivePolicy, StageFn
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.learning.domain import LearningPolicy
from src.monkey_brain.kernel.pipeline.learning.capture import capture_experience
from src.monkey_brain.kernel.pipeline.learning.policies import resolve_policy
from src.monkey_brain.kernel.pipeline.learning.phi import PhiCompiler, phi_to_dict


class LearningIntegratedPolicy(CognitivePolicy):
    """Standard 9-stage lifecycle, Learn stage enriched with the formal
    Step 10 learning pipeline (capture -> reward -> belief -> world,
    dispatched through whichever LearningPolicy strategy is configured),
    Compile-Φ stage enriched with the resulting PhiArtifact."""

    def __init__(self, learning_policy: LearningPolicy = LearningPolicy()) -> None:
        super().__init__()
        self._learning_policy = learning_policy

    def configure(
        self,
        observe: StageFn,
        believe: StageFn,
        plan: StageFn,
        execute: StageFn,
        observe_outcome: StageFn,
        learn: StageFn,
        compile_phi: StageFn,
        predict: StageFn,
        commit: StageFn,
    ) -> None:
        original_learn = learn
        original_compile_phi = compile_phi

        async def integrated_learn(state: CognitiveState) -> CognitiveState:
            state = await original_learn(state)

            experience = capture_experience(state)

            # Inject comparison losses into experience metadata if available
            comparison = getattr(state, "comparison_result", None)
            if comparison:
                from dataclasses import replace

                enhanced_metadata = {
                    **experience.metadata,
                    "comparison": comparison,
                    "actor_loss": comparison.get("actor_loss", 0.0),
                    "world_loss": comparison.get("world_loss", 0.0),
                    "policy_loss": comparison.get("policy_loss", 0.0),
                    "comparison_score": comparison.get("comparison_score", 0.0),
                }
                experience = replace(experience, metadata=enhanced_metadata)

            result = resolve_policy(self._learning_policy).learn(experience)

            state.learning = {
                **state.learning,
                "experience_id": result.experience_id,
                "reward": result.reward,
                "belief_updated": result.belief_updated,
                "world_updated": result.world_updated,
                "signals_applied": len(result.signals_applied),
                "learning_rationale": result.rationale,
            }
            state._phi_pipeline_input = (experience, result)

            # Lemon metrics (previously zero telemetry on this stage —
            # real values only, off the LearningResult this call just
            # produced). Distinct from learn.transitions.total (the
            # LearnTransitions stage, TransitionModel/PolicyStore) — this
            # is the base Learn stage's own reward/belief/world pipeline.
            from src.monkey_brain.kernel.compile import _obs

            _obs.counter("learn.total")
            _obs.gauge("learn.reward", float(result.reward))
            _obs.counter("learn.belief_updated.total", updated=str(result.belief_updated))
            _obs.counter("learn.world_updated.total", updated=str(result.world_updated))
            _obs.gauge("learn.signals_applied", float(len(result.signals_applied)))
            return state

        async def integrated_compile_phi(state: CognitiveState) -> CognitiveState:
            state = await original_compile_phi(state)

            pending = getattr(state, "_phi_pipeline_input", None)
            if pending is not None:
                experience, result = pending
                artifact = PhiCompiler().compile(experience, result)
                state.phi = phi_to_dict(artifact)

                # CognitiveOS Constitution: "repeated verified cognition
                # should become reusable deterministic capability." Until
                # this, a PhiArtifact was compiled every real cycle but
                # nothing ever looked across cycles for a repeated,
                # verified pattern -- Compile-Φ was a dead end. See
                # capability_promotion.py's module docstring for why
                # "promoted" here means "recorded as a durable, versioned
                # candidate," never "auto-registers as an executing
                # capability" (that would itself violate the "capabilities
                # are the boundary between cognition and reality" and
                # "learning cannot expand authority" tenets).
                from src.monkey_brain.kernel.pipeline.learning.capability_promotion import (
                    default_tracker,
                    extract_recipe_from_experience,
                )

                recipe = extract_recipe_from_experience(experience)
                default_tracker().observe(
                    goal_signature=artifact.goal_signature,
                    reward=artifact.reward,
                    confidence=artifact.confidence,
                    outcome_summary=artifact.outcome_summary,
                    top_signal_summary=artifact.top_signal_summary,
                    recipe=recipe,
                )

            return state

        super().configure(
            observe,
            believe,
            plan,
            execute,
            observe_outcome,
            integrated_learn,
            integrated_compile_phi,
            predict,
            commit,
        )


def build_learning_integrated_runtime(
    *,
    learning_policy: LearningPolicy = LearningPolicy(),
    observation_provider: Any = None,
    belief_fusion: Any = None,
    planning_engine: Any = None,
    plan_validator: Any = None,
    execution_engine: Any = None,
    event_bus: Any = None,
):
    """Convenience factory: a CognitiveRuntime with the Learn stage
    replaced by LearningIntegratedPolicy, composing freely with Step
    8.7/9.7's IntegratedPlanningEngine/IntegratedExecutionEngine (or any
    other planning_engine/execution_engine) since this only touches the
    Learn stage. Import of CognitiveRuntime is deferred to call time to
    avoid a module-level import cycle (belief_runtime.py never imports
    this package; this module only needs belief_runtime.py's type at
    call time, the same lazy-import shape Step 9.7 used where it needed
    execution.py's real Action/ExecutionResult types)."""
    from src.monkey_brain.kernel.pipeline.belief_runtime import (
        CognitiveRuntime as PipelineCognitiveRuntime,
    )

    return PipelineCognitiveRuntime(
        observation_provider=observation_provider,
        belief_fusion=belief_fusion,
        planning_engine=planning_engine,
        plan_validator=plan_validator,
        execution_engine=execution_engine,
        policy=LearningIntegratedPolicy(learning_policy=learning_policy),
        event_bus=event_bus,
    )
