"""Comparison Integration — wires ComparatorRuntime into the cognitive
lifecycle so prediction vs execution outcome is compared automatically,
and the resulting losses feed into learning.

Pipeline order:

    Observe → Believe → Plan → Predict (simulate) → Execute (real plan)
    → Observe Outcome → Compare (prediction vs outcome) → Learn
    → Learn Transitions → Compile Φ → Commit

Predict runs BEFORE Execute — it is a genuine blind forecast made from
the pre-execution world_snapshot/belief the Plan stage just produced,
not a look at what already happened. Running Predict after Execute (an
earlier version of this policy did exactly that) let the "prediction"
see a world already mutated by the real actions it was supposed to be
forecasting, which made the actor_loss/world_loss comparison numbers
meaningless — comparing an outcome against a forecast that was itself
computed with knowledge of that outcome. Compare (predicted vs actual)
and Learn both still run after Execute/ObserveOutcome, once the real
outcome is known.

The comparison result (actor_loss, world_loss, policy_loss) is injected
into the LearningExperience metadata so the existing reward/belief/world
learning pipeline automatically benefits from prediction accuracy feedback.

Comparator-hardening pass: Compare is now measurement-only (matches
comparator_runtime.py's own "Comparator MEASURES. Learner OPTIMIZES."
principle) -- the TransitionModel update that _run_comparison used to
perform inline (learn_from_execution + Redis persistence, a real
learning-state mutation) now runs in its own "learn_transitions" stage,
placed after "learn" so nothing updates learning state before Compare
has produced its result and Learn has run.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from typing import Any

from src.monkey_brain.kernel.pipeline.cognitive_policy import StageFn
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
from src.monkey_brain.kernel.pipeline.learning.domain import LearningPolicy
from src.monkey_brain.kernel.pipeline.prediction.integration import (
    PredictionIntegratedPolicy,
)
from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import CounterfactualAssumption
from src.monkey_brain.kernel.pipeline.prediction.scenarios import DEFAULT_REJECTION_THRESHOLD

logger = logging.getLogger("agentos.pipeline.comparison_integration")


class ComparisonIntegratedPolicy(PredictionIntegratedPolicy):
    """Extends PredictionIntegratedPolicy to add automatic comparison
    between predicted and actual outcomes, feeding losses into learning.

    Pipeline order (changed from PredictionIntegratedPolicy):
        Observe → Believe → Plan → Predict → Execute → ObserveOutcome
        → Compare → Learn → LearnTransitions → Compile Φ → Commit

    Comparison result is stored on state.comparison_result and injected
    into LearningExperience.metadata so existing learning pipelines
    (RewardEngine, BeliefLearner, WorldEvolution) automatically benefit
    from prediction accuracy feedback.
    """

    def __init__(
        self,
        learning_policy: LearningPolicy = LearningPolicy(),
        transition_model: TransitionModel | None = None,
        counterfactual_assumptions: tuple[CounterfactualAssumption, ...] = (),
        rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
        time_horizon: float = 0.0,
        society_activation: Any = None,
        capability_runtime: Any = None,
        current_plans: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            learning_policy=learning_policy,
            transition_model=transition_model,
            counterfactual_assumptions=counterfactual_assumptions,
            rejection_threshold=rejection_threshold,
            time_horizon=time_horizon,
        )
        self._society_activation = society_activation
        self._capability_runtime = capability_runtime
        """Society as Organizational Context refactor: optional
        kernel/society/activation.py::SocietyActivationEngine, threaded
        into ReasoningRuntime in configure() below."""
        self._current_plans: dict[str, Any] = dict(current_plans) if current_plans else {}
        """Plan hysteresis: goal_key -> kernel/pipeline/planning/
        current_plan_store.py::CurrentPlanRecord, the actor's real,
        persisted standing plan FOR THAT GOAL. A goal_key with no entry
        means "no Current Plan yet for this goal," distinct from an empty
        one; decide_stage (_run_decide) always replaces when no entry
        exists (bootstrap) and lazily loads from Redis on first use per
        goal_key rather than eagerly preloading a single record at
        registration (there is no longer one "the" current plan to
        preload — see current_plan_store.py's own module docstring)."""

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
        # Run PredictionIntegratedPolicy.configure() first — this wires
        # integrated_predict, integrated_learn, and integrated_compile_phi
        # via LearningIntegratedPolicy.configure() → CognitivePolicy.configure()
        super().configure(
            observe,
            believe,
            plan,
            execute,
            observe_outcome,
            learn,
            compile_phi,
            predict,
            commit,
        )

        # Extract the wired stages so we can reorder them.
        # super().configure() called CognitivePolicy.configure() with
        # (integrated_learn, integrated_compile_phi, integrated_predict, commit)
        # so the stage list is already built. Reorder it:
        #   original: observe, believe, plan, execute, observe_outcome,
        #             learn, compile_phi, predict, commit
        #   desired:  observe, believe, plan, predict, execute,
        #             observe_outcome, compare, learn, learn_transitions,
        #             compile_phi, commit
        stage_map = dict(self._stages)

        # Save the integrated functions
        integrated_predict = stage_map.get("predict")
        integrated_learn = stage_map.get("learn")
        integrated_compile_phi = stage_map.get("compile_phi")

        # Wrap predict to inject the policy's learned transition model
        # so predictions use accumulated knowledge from prior ticks
        policy_ref = self

        async def predict_with_model(state: CognitiveState) -> CognitiveState:
            # Inject accumulated transition model before predict runs
            if policy_ref._transition_model.known_transitions:
                state.transition_model = policy_ref._transition_model
            return await integrated_predict(state)

        async def compare_stage(state: CognitiveState) -> CognitiveState:
            return await _run_comparison(state, self)

        async def decide_stage(state: CognitiveState) -> CognitiveState:
            return await _run_decide(state, self, repredict=predict_with_model)

        async def learn_transitions_stage(state: CognitiveState) -> CognitiveState:
            return _apply_transition_learning(state, self)

        async def plan_outcome_feedback_stage(state: CognitiveState) -> CognitiveState:
            return _record_plan_outcome_feedback(state, self)

        # Rebuild stage list with new ordering: Predict is a genuine blind
        # forecast, so it must run BEFORE Execute mutates any real state —
        # right after Plan, from the same pre-execution world_snapshot/
        # belief the plan itself was built from. Compare (predicted vs
        # actual) and Learn still run after Execute/ObserveOutcome, once
        # the real outcome exists to compare against. Decide runs right
        # after Predict — it needs state.prediction_result (the newly
        # generated plan's score inputs) and decides, before Execute ever
        # runs, WHICH plan actually executes this tick: the freshly
        # generated one, or the actor's real persisted Current Plan (plan
        # hysteresis: kernel/pipeline/planning/plan_hysteresis.py). The
        # full pipeline always runs — Decide swaps in a plan, it never
        # skips execution.
        self._stages = [
            ("observe", stage_map["observe"]),
            ("believe", stage_map["believe"]),
            ("plan", stage_map["plan"]),
            ("predict", predict_with_model),
            ("decide", decide_stage),
            ("execute", stage_map["execute"]),
            ("observe_outcome", stage_map["observe_outcome"]),
            ("plan_outcome_feedback", plan_outcome_feedback_stage),
            ("compare", compare_stage),
            ("learn", integrated_learn),
            ("learn_transitions", learn_transitions_stage),
            ("compile_phi", integrated_compile_phi),
            ("commit", stage_map["commit"]),
        ]

        # Runtime Encapsulation Refactor follow-up: build the real,
        # decoupled Reasoning/Execution halves once, here, where the final
        # stage list is known. `plan`/`execute` are bound methods of the
        # owning CognitiveRuntime instance (belief_runtime.py — every real
        # call site passes self._generate_plan/self._execute_plan), so
        # `.__self__` recovers that instance without changing configure()'s
        # signature (shared across all CognitivePolicy subclasses).
        from src.monkey_brain.kernel.cognitive_os.reasoning_runtime import ReasoningRuntime
        from src.monkey_brain.kernel.cognitive_os.execution_runtime import ExecutionRuntime

        runtime_ref = getattr(plan, "__self__", None)

        self.reasoning = ReasoningRuntime(
            self.stages_from("observe", "believe", "plan", "predict", "decide"),
            planning_engine=getattr(runtime_ref, "_planning_engine", None),
            transition_model=self._transition_model,
            learning_policy=self._learning_policy,
            society_activation=self._society_activation,
        )
        self.execution = ExecutionRuntime(
            self.stages_from(
                "execute",
                "observe_outcome",
                "plan_outcome_feedback",
                "compare",
                "learn",
                "learn_transitions",
                "compile_phi",
                "commit",
            ),
            capability_runtime=self._capability_runtime or getattr(runtime_ref, "_capability_runtime", None),
        )

    async def execute(self, state: CognitiveState) -> CognitiveState:
        """Reasoning produces a CognitiveState (plan + prediction_result);
        Execution consumes it. No engine object is shared between the two
        — the state itself is the entire handoff, matching the
        RecursivePlanningPolicy override pattern this codebase already
        supports for non-linear stage orchestration.

        Plan hysteresis: the full pipeline always runs, every tick.
        Decide (now part of Reasoning) doesn't decide WHETHER to execute
        — it decides WHICH plan to execute: if the freshly generated plan
        cleared the hysteresis bar, state.plan/state.belief.plan are left
        as that new plan (unchanged, default); if not, decide_stage has
        already swapped state.plan/state.belief.plan to the actor's real
        persisted Current Plan and re-run Predict against it, so Execute
        below always runs a plan that genuinely matches
        state.prediction_result — either way, this is real execution
        with real side effects, never a skip."""
        state = await self.reasoning.reason(state)
        return await self.execution.execute(state)


async def _run_comparison(state: CognitiveState, policy: Any = None) -> CognitiveState:
    """Compare prediction vs execution. Measurement only.

    Reads state.prediction_result (from Predict stage) and
    state.execution_result (from Execute stage), normalizes both into
    a common graph format using plan steps as canonical node IDs, runs
    the comparator, and stores the ComparisonResult on state.

    Comparator-hardening pass: this used to also update and persist the
    TransitionModel inline (a real learning-state mutation happening
    inside "compare", one stage before "learn" ever ran) -- that violated
    comparator_runtime.py's own "Comparator MEASURES. Learner OPTIMIZES."
    principle. That responsibility now lives in _apply_transition_learning,
    invoked from its own "learn_transitions" stage (see configure()),
    which runs after "learn" -- this function no longer mutates or
    persists anything; `policy` is accepted only so callers/tests that
    already pass it don't need updating, and is otherwise unused here.
    """
    prediction = getattr(state, "prediction_result", None)
    execution = state.execution_result

    from src.monkey_brain.kernel.compile import _obs

    if prediction is None or execution is None:
        # Real reliability gap: this branch used to be completely silent
        # (no log line at all) -- every one of its skips looked, from
        # outside the process, identical to the exception branch below
        # NOT firing, i.e. indistinguishable from "everything is fine."
        # Confirmed live via server log: zero "Comparison failed"
        # WARNING lines were ever emitted despite comparator_outcome
        # coming back missing on a real, otherwise-successful tick --
        # this silent branch was the only place it could have gone.
        logger.warning(
            "Comparison skipped (execution_id=%s): prediction_result=%s execution_result=%s",
            state.metrics.get("execution_id", "") if isinstance(state.metrics, dict) else "",
            "present" if prediction is not None else "MISSING",
            "present" if execution is not None else "MISSING",
        )
        state.comparison_result = None
        _obs.counter("compare.total", outcome="skipped")
        return state

    plan_steps = state.belief.plan.steps if state.belief.plan else ()
    execution_id = state.metrics.get("execution_id", "") if isinstance(state.metrics, dict) else ""
    sim_graph = _prediction_to_graph(prediction, plan_steps, execution_id=execution_id)
    exec_graph = _execution_to_graph(execution, plan_steps, execution_id=execution_id)

    try:
        from src.monkey_brain.kernel.comparator_runtime import get_comparator_runtime

        comparator = get_comparator_runtime()
        result = await comparator.compare(sim_graph, exec_graph)
        state.comparison_result = result.to_dict()
        # Lemon metrics (previously zero telemetry on this stage — the
        # ComparatorRuntime computed actor_loss/world_loss/policy_loss
        # every tick but published none of it). Measurement-only, matching
        # comparator_runtime.py's own "Comparator MEASURES" principle —
        # this reports what the comparator just measured, never mutates it.
        _obs.counter("compare.total", outcome=str(result.outcome))
        _obs.gauge("compare.actor_loss", float(result.actor_loss))
        _obs.gauge("compare.world_loss", float(result.world_loss))
        _obs.gauge("compare.policy_loss", float(result.policy_loss))
        # execution_id logged explicitly (not just implied by request
        # order): this server also runs a background autonomous actor
        # loop that ticks other actors concurrently on the same event
        # loop, so multiple unrelated "Comparison: outcome=..." lines can
        # interleave in this one log within a single request's own
        # window -- without the id, a reader (human or an E2E test tailing
        # this file) has no way to tell which line was theirs.
        logger.info(
            "Comparison: execution_id=%s outcome=%s actor_loss=%.4f world_loss=%.4f policy_loss=%.4f",
            execution_id,
            result.outcome,
            result.actor_loss,
            result.world_loss,
            result.policy_loss,
        )
    except Exception as e:
        logger.warning("Comparison failed (non-fatal): %s", e)
        state.comparison_result = None
        _obs.counter("compare.total", outcome="error")

    return state


def _apply_transition_learning(state: CognitiveState, policy: Any = None) -> CognitiveState:
    """Update the TransitionModel from actual execution outcomes.

    Deliberately NOT part of Compare (see _run_comparison's docstring) --
    this is real learning-state mutation (learn_from_execution + Redis
    persistence), and must only happen after Compare has established what
    actually happened and Learn has run, not interleaved into measurement.
    Same logic _run_comparison used to run inline, unchanged; only the
    call site moved.
    """
    execution = state.execution_result
    if execution is None:
        return state

    plan_steps = state.belief.plan.steps if state.belief.plan else ()

    # Read from policy's accumulated model (persists across ticks), then
    # push the updated model back.
    existing_model = getattr(policy, "_transition_model", None) if policy else None

    # Gap D (Learning-hardening follow-up, "fix the gaps" round): a real
    # Q-table/PolicyStore integration into the live per-tick Learning path.
    # Scope note, reported transparently rather than silently narrowed: the
    # user's selection was "merge disconnected Q-table/GraphManager
    # systems." A literal merge was investigated and found architecturally
    # incoherent -- GraphManager's QTable keys on (agent_name, "__self__")
    # (one scalar per agent, no action dimension) and requires a
    # {nodes, edges}-shaped simulation_graph for apply_learning()'s
    # topological-loss gating; BellmanPolicy keys on
    # (hash_state(ExecutionState.to_dict()), action) via its own Transition
    # type. Neither is reachable from this pipeline without either
    # fabricating fake data to satisfy their real shape or dragging in the
    # already-confirmed-disconnected ExecutionGraph/codegen subsystem
    # (Execution-hardening pass's own finding). Instead: kernel/policy/
    # store.py::PolicyStore -- confirmed genuinely standalone (only
    # stdlib imports), its own docstring already declaring "Policy storage
    # never mutates the world tensor" -- is wired in here as the smallest
    # correct integration, keyed identically to TransitionModel
    # ((goal_key, action_key), not a third incompatible scheme).
    policy_store = getattr(policy, "_policy_store", None) if policy else None
    if policy_store is None and policy is not None:
        from src.monkey_brain.kernel.policy.store import PolicyStore

        policy_store = PolicyStore(owner_id=getattr(state.belief, "actor_id", None))
        policy._policy_store = policy_store

    _learn_transitions(state, execution, plan_steps, existing_model, policy_store)
    learned = getattr(state, "transition_model", None)
    if learned is not None and policy is not None:
        policy._transition_model = learned
        # Real persistence for real learning — this used to live only on
        # the in-memory policy object, reset to zero every restart (see
        # kernel/pipeline/prediction/persistence.py's own module
        # docstring). actor_id is all this layer has (no name — the
        # actor's real display name is only ever known at the
        # registration layer, which separately writes a companion
        # "actor_id -> name" record when it loads/saves this same key).
        actor_id = getattr(state.actor, "actor_id", "") if getattr(state, "actor", None) else ""
        if actor_id:
            from src.monkey_brain.kernel.pipeline.prediction.persistence import save_transition_model

            save_transition_model(actor_id, learned)

    return state


async def _run_decide(state: CognitiveState, policy: Any, repredict: Any = None) -> CognitiveState:
    """Plan hysteresis Decide stage: score the freshly generated plan
    (state.plan, already-computed state.prediction_result) against the
    actor's persisted Current Plan FOR THIS SAME GOAL
    (policy._current_plans[goal_key]) and decide which one actually
    executes this tick.

    Goal-scoped (kernel/pipeline/planning/goal_key.py::canonicalize_goal) —
    a standing plan for one goal must never suppress, or be returned in
    place of, a freshly generated plan for a different goal. Before this,
    policy._current_plan was a single actor-wide slot: an actor's stale,
    already-failing plan for goal A would get compared against (and could
    discard) a genuinely fresh plan for unrelated goal B — confirmed live
    ("find the nearest pharmacy" silently executing an old grocery plan
    instead). _current_plans is now a dict keyed by goal_key, and Redis
    persistence (current_plan_store.py) is keyed by (actor_id, goal_key).

    The full pipeline always runs, every tick — this never skips
    execution. On "replace," state.plan/state.belief.plan are left as
    the new plan (already the case by the time this stage runs) and the
    new plan becomes the Current Plan for this goal_key. On "keep,"
    state.plan/state.belief.plan are swapped to the real, persisted
    Current Plan for this SAME goal_key (reconstructed via plan_from_dict)
    and `repredict` (the same predict_with_model stage function, passed in
    by configure()) is re-run so state.prediction_result genuinely matches
    what's about to execute — Compare/Learn downstream must never see a
    prediction for one plan and an execution of another.
    """
    import os

    from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import score_plan, decide
    from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
        CurrentPlanRecord,
        plan_to_dict,
        plan_from_dict,
        save_current_plan,
        load_current_plan,
    )
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

    # Real gap this closes: Plan.goal only ever carries resolved_goal.name
    # (llm_planner.py) -- the one-off triggering text (e.g. "buy 1 dozen
    # large eggs") lives in belief.goal.description, not plan.goal or
    # belief.goal.name. Computing goal_key from name alone (as this used
    # to) collapses EVERY one-off request sharing the same standing goal
    # ("buy groceries efficiently") into ONE hysteresis slot -- confirmed
    # live: a fresh, correctly-generated eggs plan lost to "keep" against
    # a stale Current Plan a much earlier, unrelated milk request had
    # saved under that same bare name, silently executing the wrong
    # plan even though the LLM itself picked the right product. Folding
    # description in here mirrors the exact goal_key _generate_plan
    # (belief_runtime.py) already computes for its own skip-replan check,
    # so hysteresis and replanning agree on what "the same goal" means.
    belief_goal_obj = getattr(state.belief, "goal", None)
    belief_goal_name = getattr(belief_goal_obj, "name", "") or ""
    belief_goal_description = getattr(belief_goal_obj, "description", "") or ""
    plan_goal = getattr(state.plan, "goal", "") or ""
    full_goal_text = (
        f"{belief_goal_name} {belief_goal_description}".strip() if belief_goal_name else (plan_goal or belief_goal_name)
    )
    goal_key = canonicalize_goal(full_goal_text)

    if not hasattr(policy, "_current_plans") or policy._current_plans is None:
        policy._current_plans = {}
    current = policy._current_plans.get(goal_key)
    if current is None and state.actor_id and goal_key:
        # Lazy per-goal load — mirrors the old eager single-record preload
        # at registration, but correct-by-construction: there is no single
        # "the actor's plan" to preload anymore, only "the actor's plan
        # for goal_key," which isn't known until this tick's own goal is.
        current = load_current_plan(state.actor_id, goal_key)
        if current is not None:
            policy._current_plans[goal_key] = current

    # Invariant: it must be structurally impossible to compare against a
    # standing plan belonging to a different goal (task requirement: make
    # cross-goal selection impossible, not merely unlikely).
    assert current is None or canonicalize_goal(current.goal) == goal_key, (
        f"Cross-goal plan contamination detected: standing plan goal_key "
        f"{canonicalize_goal(current.goal)!r} != candidate goal_key {goal_key!r}"
    )

    has_new_plan = state.plan is not None and bool(getattr(state.plan, "steps", None))

    # Plan invalidation / stale-world revalidation: re-check the Current
    # Plan's recorded entity_versions against live KG state before it can
    # win "keep" over (or simply stand in for an absent) fresh candidate.
    # Mirrors the last_execution_failed bypass immediately below — a
    # Current Plan whose world-state assumptions have moved on is treated
    # the same as one that's already demonstrated it doesn't work.
    from src.monkey_brain.kernel.pipeline.planning.plan_staleness import check_plan_staleness

    kg = state.context.get("knowledge_graph") if isinstance(state.context, dict) else None
    staleness = check_plan_staleness(kg, current) if current is not None else None
    execution_id = state.metrics.get("execution_id", "") if isinstance(state.metrics, dict) else ""
    from src.monkey_brain.kernel.compile import _obs

    if staleness is not None and staleness.is_stale:
        state.metrics["plan_stale"] = staleness.to_dict()
        state.metrics["plan_stale"]["plan_id"] = current.plan_id
        from src.monkey_brain.kernel.pipeline.audit_trail import record_plan_event

        record_plan_event(
            "invalidated",
            plan_id=current.plan_id,
            actor_id=state.actor_id,
            execution_id=execution_id,
            goal=current.goal,
            steps=current.steps,
            step_descriptions=current.step_descriptions,
            result="; ".join(r["reason"] for r in staleness.to_dict()["affected_assumptions"]),
            metadata={"affected_assumptions": staleness.to_dict()["affected_assumptions"]},
        )
        _obs.counter("plan.validation.total", result="stale")
        _obs.counter("plan.replan.total")
    elif staleness is not None:
        _obs.counter("plan.validation.total", result="accepted")

    if (
        has_new_plan
        and current is not None
        and (current.last_execution_failed or (staleness is not None and staleness.is_stale))
    ):
        # Failure-driven re-plan: score_plan only looks at predicted
        # probability/utility/cost/risk, never actual execution success —
        # a plan that scores competitively can otherwise stay "current"
        # (and keep re-executing verbatim) forever, even after every real
        # attempt at it has failed. A Current Plan whose last real
        # execution failed has already forfeited its hysteresis
        # protection; bypass the score comparison entirely rather than
        # asking a fresh candidate to clear the normal 10% margin over a
        # plan that's already demonstrated it doesn't work.
        from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import HysteresisVerdict

        new_score, components = score_plan(state.plan, state.prediction_result)
        bypass_reason = (
            "world-state assumptions are stale"
            if (staleness is not None and staleness.is_stale)
            else "last execution failed"
        )
        verdict = HysteresisVerdict(
            action="replace",
            reason=(
                f"Existing Current Plan's {bypass_reason} — bypassing "
                f"hysteresis and replacing with a freshly generated plan "
                f"(new score {new_score:.3f})."
            ),
            new_score=new_score,
            current_score=current.score,
            percent_improvement=None,
        )
    elif has_new_plan and os.environ.get("ACTOR_NODE_CLASS", "cloud") == "robot":
        # Same bypass shape as the last_execution_failed/staleness branch
        # above, for a different reason: score_plan's generic confidence/
        # structure-based score has no way to tell "x=5, y=5, height_m=15"
        # apart from "x=0, y=0, height_m=2" -- both can score identically
        # well on plan quality alone. Confirmed live: testing the exact
        # same flight-mission text repeatedly (a natural consequence of
        # iterating on a fix) meant every fresh, CORRECTLY-parameterized
        # plan kept losing this goal_key's hysteresis comparison to an
        # earlier, wrong-parameter attempt already persisted as the
        # Current Plan, since it never cleared the 10% margin on a metric
        # blind to the very thing that was wrong. A robot mission's
        # numeric parameters are the safety-critical part of the plan;
        # silently substituting a "similar-scoring" stale one is worse
        # than the minor replan churn this bypass trades for it. Same
        # ACTOR_NODE_CLASS convention already used to skip Moss's
        # semantic plan cache (llm_planner.py) for the identical reason.
        new_score, components = score_plan(state.plan, state.prediction_result)
        from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import HysteresisVerdict

        verdict = HysteresisVerdict(
            action="replace",
            reason=(
                f"Robot-class actor — always executing the freshly generated "
                f"plan rather than comparing scores against the Current Plan "
                f"(new score {new_score:.3f})."
            ),
            new_score=new_score,
            current_score=current.score if current else None,
            percent_improvement=None,
        )
    elif has_new_plan:
        new_score, components = score_plan(state.plan, state.prediction_result)
        verdict = decide(new_score, current)
    else:
        # Nothing real was generated this tick (e.g. a noisy autonomous
        # tick with no concrete goal) — never let this outscore or
        # overwrite a real Current Plan; always keep whatever exists FOR
        # THIS GOAL.
        from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import HysteresisVerdict

        new_score, components = 0.0, {}
        verdict = HysteresisVerdict(
            action="keep",
            reason="Empty plan generated — nothing to decide; the existing Current Plan for this goal (if any) is reused.",
            new_score=0.0,
            current_score=(current.score if current else None),
            percent_improvement=None,
        )

    state.metrics["decide_action"] = verdict.action
    state.metrics["decide_reason"] = verdict.reason
    state.metrics["decide_new_score"] = new_score if has_new_plan else None
    state.metrics["decide_score_components"] = components
    state.metrics["decide_current_score"] = current.score if current else None
    state.metrics["decide_current_plan_id"] = current.plan_id if current else None
    # Observability (task requirement): a log line built from these two
    # keys makes a goal_key mismatch immediately visible — that condition
    # is asserted unreachable above, not merely logged.
    state.metrics["decide_goal_key"] = goal_key
    state.metrics["decide_standing_plan_goal_key"] = canonicalize_goal(current.goal) if current else None

    if verdict.action == "replace":
        new_plan_id = uuid.uuid4().hex
        state.metrics["decide_new_plan_id"] = new_plan_id
        state.metrics["decide_replaced_plan_snapshot"] = current.to_dict() if current else None

        from src.monkey_brain.kernel.pipeline.planning.plan_staleness import capture_entity_versions

        record = CurrentPlanRecord(
            plan_id=new_plan_id,
            actor_id=state.actor_id,
            goal=full_goal_text,
            steps=tuple(s.action for s in state.plan.steps),
            step_descriptions=tuple(s.description for s in state.plan.steps),
            cost=float(getattr(state.plan, "cost", 0.0) or 0.0),
            risk=float(getattr(state.plan, "risk", 0.0) or 0.0),
            confidence=float(getattr(state.plan, "confidence", 0.0) or 0.0),
            score=new_score,
            score_components=components,
            plan=plan_to_dict(state.plan),
            entity_versions=capture_entity_versions(kg, state.plan),
        )
        policy._current_plans[goal_key] = record
        if state.actor_id:
            save_current_plan(state.actor_id, goal_key, record)

        from src.monkey_brain.kernel.pipeline.audit_trail import record_plan_event

        record_plan_event(
            "generated",
            plan_id=new_plan_id,
            actor_id=state.actor_id,
            execution_id=execution_id,
            goal=full_goal_text,
            steps=record.steps,
            step_descriptions=record.step_descriptions,
            metadata={"replaces": current.plan_id} if current is not None else {},
        )
        _obs.counter("plan.total", status="created")
        # state.plan / state.belief.plan already ARE the new plan; no swap.
        return state

    # "keep": execute the Current Plan (for this same goal_key) instead of
    # the freshly generated (losing, or absent) one.
    state.metrics["decide_new_plan_id"] = None
    state.metrics["decide_replaced_plan_snapshot"] = None

    if current is None or not current.plan:
        # Nothing persisted to fall back to either — genuinely nothing
        # real to execute this tick. Downstream stages already handle an
        # empty/no-op plan gracefully (pre-existing behavior).
        return state

    if staleness is not None and staleness.is_stale:
        # Critical invariant: no stale plan may produce a consequential
        # side effect. This tick had no fresh replacement plan to bypass
        # into (has_new_plan was False, or its score simply lost) — but a
        # stale Current Plan losing on score is not the same as it being
        # SAFE, so it must not be assigned to state.plan/state.belief.plan
        # for execution either. Leaving state.plan untouched here means
        # execution sees an empty/no-op plan this tick (already gracefully
        # handled downstream), same as the "nothing persisted" branch
        # above — the actor gets no order, no payment, and a real replan
        # attempt on its next tick instead of a silent stale purchase.
        return state

    old_plan = plan_from_dict(current.plan)
    state.plan = old_plan
    if state.belief is not None:
        state.belief.plan = old_plan
    if repredict is not None:
        state = await repredict(state)

    import time as _time

    updated = dataclasses.replace(current, kept_count=current.kept_count + 1, last_kept_at=_time.time())
    policy._current_plans[goal_key] = updated
    state.metrics["decide_current_plan_id"] = updated.plan_id
    if state.actor_id:
        save_current_plan(state.actor_id, goal_key, updated)

    return state


def _record_plan_outcome_feedback(state: CognitiveState, policy: Any) -> CognitiveState:
    """Runs right after observe_outcome, once this tick's real execution
    result is known. Marks the Current Plan for this goal_key (the exact
    plan that just executed, whether it arrived via "replace" or "keep")
    with whether that real execution failed, so the NEXT tick's
    _run_decide can bypass hysteresis for it (see the
    current.last_execution_failed check there). Closes the loop
    plan_hysteresis.py's docstring flags as deferred: score_plan has no
    actual-outcome feedback of its own — this is that feedback, applied
    one layer up rather than inside the pure scoring function."""
    from src.monkey_brain.kernel.pipeline.planning.current_plan_store import save_current_plan

    goal_key = state.metrics.get("decide_goal_key")
    if not goal_key or not hasattr(policy, "_current_plans"):
        return state
    current = policy._current_plans.get(goal_key)
    if current is None:
        return state
    outcome = state.outcome or {}
    actions_executed = int(outcome.get("actions_executed", 0) or 0)
    # Only a tick that genuinely executed something can mark a plan
    # failed — a zero-actions tick (nothing ran) says nothing about
    # whether the plan itself works, and outcome's own "nothing executed"
    # fallback defaults goal_achieved to False, which would otherwise
    # read as a false failure.
    failed = actions_executed > 0 and (bool(outcome.get("failure_count", 0)) or not outcome.get("goal_achieved", True))
    if current.last_execution_failed == failed:
        return state
    updated = dataclasses.replace(current, last_execution_failed=failed)
    policy._current_plans[goal_key] = updated
    if state.actor_id:
        save_current_plan(state.actor_id, goal_key, updated)
    return state


def _learn_transitions(
    state: CognitiveState,
    execution: Any,
    plan_steps: tuple,
    policy_transition_model: Any = None,
    policy_store: Any = None,
) -> None:
    """Update the TransitionModel from Comparator-verified execution evidence.

    Each executed action may become a learned transition only when the
    Comparator supplied node-level evidence for that action. Raw execution
    success, capability returns, and HTTP status are intentionally ignored
    here: the Comparator is the authoritative source for whether the
    expected world transition actually happened.

    Initializes from the policy's accumulated model (not from state,
    which is fresh each tick) so learning persists across ticks.

    Goal-scoped (Cross-Goal Plan Contamination fix): learned evidence is
    tagged with the goal it was actually observed under, so a real,
    learned failure while pursuing one goal can't silently suppress an
    unrelated goal's plan that happens to share an action name later.

    `policy_store` (Gap D, optional): when supplied, each Comparator-
    verified observation also updates a kernel/policy/store.py::PolicyStore
    Q-value for (goal_key, action_key) -- the same identity TransitionModel
    uses. Gated on the identical evidence this function already requires
    for TransitionModel (a real, non-None per-node actual_success); never
    updated from raw execution/HTTP state.
    """
    from src.monkey_brain.kernel.pipeline.prediction.transitions import TransitionModel
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal

    # Start from the policy's accumulated model, not state (fresh each tick)
    current_model = policy_transition_model or TransitionModel()
    # goal_key MUST be derived identically to the Predict-side lookup
    # (prediction/integration.py:155 passes plan=state.belief.plan into
    # DeterministicPredictionPolicy; prediction/simulation.py::simulate()/
    # counterfactuals.py::branch() both now fold belief.goal.description
    # in too, matching this exact widening) or learned evidence gets
    # written under a key Prediction will never look up.
    #
    # Widened again (Goal-Key Contamination fix): `plan.goal` only ever
    # carries the standing goal NAME (llm_planner.py's Plan construction),
    # never the one-off triggering description -- so this key used to
    # collapse EVERY one-off request sharing a standing goal into ONE
    # transition-model slot. Confirmed live: an unrelated actor's repeated
    # failed attempts at one specific request drove scenario probability
    # to 0% for every OTHER, unrelated request sharing that same standing
    # goal. Write-key must equal read-key across every site listed above.
    goal_key = (
        canonicalize_goal(f"{state.belief.plan.goal} {state.belief.goal.description}".strip())
        if state.belief and state.belief.plan
        else ""
    )
    state.transition_model = current_model

    # Compound Disruption pass (Phase 3 x Phase 4 composition): a resumed
    # tick (meta.resume_execution_id, kernel/pipeline/execution_checkpoint_
    # store.py) replays already-completed steps through this SAME function
    # -- ActionExecutor tags a replayed ActionOutcome's metadata with
    # resumed_from_checkpoint, but nothing here ever read that flag. Safe
    # for a genuine crash-and-resume (the original tick never reached
    # compare/learn at all, so this is the first real observation), but NOT
    # safe for the other real reason resume_execution_id exists -- a caller
    # retrying because a RESPONSE was lost, not because the server crashed
    # (see OrderCreationCapability's own resume_order_id precedent) -- if
    # the original tick's own compare/learn already ran, re-processing the
    # same replayed steps here would blend duplicate evidence into
    # TransitionModel.learn_from_execution's exponential moving average
    # (and double-reward policy_store) for one real observation.
    # learning_event_store.py (Phase 4) already records exactly what's
    # needed to tell the two cases apart -- reused here as an idempotency
    # ledger, not a second store.
    execution_id = state.metrics.get("execution_id", "") if isinstance(state.metrics, dict) else ""
    already_learned: set[tuple[str, str]] = set()
    if execution_id:
        from src.monkey_brain.kernel.pipeline.learning_event_store import load_learning_events_for_execution

        already_learned = {(e.goal_key, e.action_key) for e in load_learning_events_for_execution(execution_id)}

    actions = []
    if hasattr(execution, "actions"):
        actions = list(execution.actions) if execution.actions else []
    elif isinstance(execution, dict):
        actions = execution.get("actions", [])

    from src.monkey_brain.kernel.compile import _obs

    comparison = state.comparison_result if isinstance(state.comparison_result, dict) else None
    if not comparison:
        logger.warning(
            "Transition learning skipped: Comparator result is missing; raw execution "
            "state is not authoritative learning evidence."
        )
        _obs.counter("learn.skipped.total", reason="no_comparison")
        return

    outcome = str(comparison.get("outcome", "") or "")
    if outcome in ("inconclusive", "no_change"):
        logger.info("Transition learning skipped: Comparator outcome=%s has no transition evidence.", outcome)
        _obs.counter("learn.skipped.total", reason=outcome)
        return

    node_diffs = comparison.get("node_diffs", {})
    if not isinstance(node_diffs, dict) or not node_diffs:
        logger.info("Transition learning skipped: Comparator supplied no node-level evidence.")
        _obs.counter("learn.skipped.total", reason="no_node_diffs")
        return

    # Low-success-episode guard: a multi-step episode where most
    # GENUINELY-ATTEMPTED steps failed is far more likely to reflect a
    # one-off plan-generation defect (a malformed step order/missing
    # dependency the LLM produced) than genuine per-action world
    # evidence -- confirmed live, a duplicated-OrderConfirmation plan's
    # genuinely-executed (not not_attempted) failing step got learned as
    # probability=0.15, which then, via _path_probability's dependency-
    # chain gate (risk.py), drove every LATER prediction for every step
    # depending on it straight to 0% -- permanently rejecting an
    # otherwise-normal goal on the strength of one anomalous episode,
    # with no real way back (only a 5%/tick epsilon-exploration chance --
    # belief_runtime.py's _exploration_epsilon).
    #
    # Ratio computed from node_diffs's actual_success (True/False/absent),
    # NOT execution.success_count/failure_count: a step BLOCKED by an
    # earlier step's real failure (dependency cascade -- see
    # _reject_plan's not_attempted convention) is neither a success nor a
    # failure -- it was never attempted at all -- but raw execution
    # counts fold it in as a "failure" anyway, undercounting the real
    # success rate among steps that actually ran. Confirmed live: a
    # 3-step plan where one step genuinely failed and blocked one
    # downstream step (1 success, 1 real failure, 1 blocked) is a clean
    # 1/2 (50%) among GENUINE attempts -- not the 1/3 (33%) raw execution
    # counts implied, which wrongly tripped this guard and discarded the
    # one real, learnable failure along with the blocked non-evidence. A
    # single-genuine-attempt episode is exempt: "1/1 failed" is one
    # clean, real observation about that one action, not an outlier
    # pattern across a whole plan.
    genuine = [v.get("actual_success") for v in node_diffs.values() if v.get("actual_success") is not None]
    genuine_success = sum(1 for s in genuine if s)
    if len(genuine) > 1 and genuine_success / len(genuine) < 0.5:
        logger.info(
            "Transition learning skipped (goal=%s): low-success episode (%d/%d among genuinely-attempted steps) -- "
            "treated as a likely plan-generation defect, not per-action evidence.",
            goal_key,
            genuine_success,
            len(genuine),
        )
        _obs.counter("learn.skipped.total", reason="low_success_episode")
        return

    # confidence here means "how much should this ONE observation move the
    # learned probability" (learn_from_execution clamps a success's
    # observed_prob to at most `confidence`, and a failure's to at least
    # `1-confidence` -- see prediction/transitions.py, not modified). It
    # must reflect confidence in the OBSERVATION -- which per-node
    # actual_success already is, since it's gated on the Comparator having
    # a real, non-None per-node record -- not confidence in the PRIOR
    # PREDICTION. Deriving it from epistemic_loss (prediction accuracy)
    # was a real bug found this pass: a cold-start action has no prior
    # prediction to be accurate about, so epistemic_loss is always high
    # early on, which capped a genuinely-observed success's learned
    # probability near ~0.35 instead of trending toward the real ceiling
    # -- the opposite of what a correct success should do. A flat, high
    # default (matching this function's value before the Comparator
    # integration) trusts a real, gated observation appropriately
    # regardless of how good the prediction that preceded it was.
    confidence = 0.85

    state_diff = comparison.get("state_diff", {})
    world_delta = {}
    if isinstance(state_diff, dict):
        observed = state_diff.get("observed", {})
        if isinstance(observed, dict):
            world_delta = observed

    learning_rate = 0.15

    for i, action in enumerate(actions):
        action_dict = (
            action
            if isinstance(action, dict)
            else (
                {
                    "action_id": getattr(action, "action_id", f"step_{i}"),
                    "success": getattr(action, "success", True),
                    "result": getattr(action, "result", {}),
                }
            )
        )

        # Use plan step action as canonical key
        action_key = plan_steps[i].action if i < len(plan_steps) else action_dict.get("action_id", f"step_{i}")

        node_diff = node_diffs.get(action_key)
        if not isinstance(node_diff, dict):
            logger.debug(
                "Transition learning skipped for %s (goal=%s): no Comparator node evidence.",
                action_key,
                goal_key,
            )
            continue

        actual_success = node_diff.get("actual_success")
        if actual_success is None:
            # None means the Comparator has no observation for this node at
            # all (never executed, e.g. blocked by a failed dependency) --
            # per the task's own explicit requirement, an unexecuted step
            # must NEVER be learned as a failure. Skip entirely: no
            # positive or negative evidence.
            logger.debug(
                "Transition learning skipped for %s (goal=%s): node was not executed.",
                action_key,
                goal_key,
            )
            continue

        if (goal_key, action_key) in already_learned:
            logger.debug(
                "Transition learning skipped for %s (goal=%s): already learned for execution_id=%s (resumed).",
                action_key,
                goal_key,
                execution_id,
            )
            continue

        # `actual_success` is the Comparator's own verified record of what
        # was genuinely observed for this node -- that alone is the
        # learning signal. Earlier this pass, this also required
        # `expected_success is True and match is True` (the outcome had to
        # match the PREDICTION), which is a different question (prediction
        # calibration, already captured separately by the Comparator's
        # UNEXPECTED_SUCCESS/UNEXPECTED_FAILURE classification and
        # epistemic_loss) -- gating learning on it meant a genuinely
        # successful COLD-START action (no prior prediction to match, so
        # expected_success reads False) was recorded as a FAILURE the
        # first time it ever worked, actively teaching the model the
        # opposite of what was observed. Confirmed live via a two-tick
        # functional test: a cold-start success made the very next tick's
        # prediction WORSE, not better.
        success = actual_success is True

        if policy_store is not None:
            # Minimal, honest Q-value update: +1/-1 terminal reward per
            # verified observation (no next_state -- these are independent
            # per-tick observations, not a chained MDP episode, matching
            # TransitionModel's own per-(goal,action) design rather than
            # inventing state-chaining this pipeline doesn't have).
            policy_store.update(state=goal_key, action=action_key, reward=1.0 if success else -1.0)
            _obs.counter("learn.policy_store_updates.total", success=str(success))

        # Learning-inspection pass: capture the pre-update transition (if
        # any) before learn_from_execution replaces it -- learn_from_execution
        # returns a NEW TransitionModel each call (frozen dataclass), so this
        # is the only point where "previous" is still reachable.
        previous_tuple = current_model.known_transitions.get((goal_key, action_key), ())
        previous = previous_tuple[-1].to_dict() if previous_tuple else None

        current_model = current_model.learn_from_execution(
            action_key=action_key,
            success=success,
            confidence=confidence,
            world_delta=world_delta,
            learning_rate=learning_rate,
            goal_key=goal_key,
        )
        # Lemon metrics (previously zero telemetry on Learn/LearnTransitions
        # despite this being a real, persisted learning-state mutation —
        # see save_transition_model below and _apply_transition_learning's
        # own docstring). One real, Comparator-gated observation per count.
        _obs.counter("learn.transitions.total", success=str(success))

        # Record this real, Comparator-verified update for inspection
        # (kernel/pipeline/learning_event_store.py). Placed after both
        # `continue` gates above (no evidence / actual_success is None), so
        # an unexecuted step never produces an event either -- same
        # invariant _learn_transitions already enforces for the model itself.
        from src.monkey_brain.kernel.pipeline.learning_event_store import LearningEvent, record_learning_event

        updated = current_model.known_transitions[(goal_key, action_key)][-1].to_dict()
        record_learning_event(
            LearningEvent(
                execution_id=execution_id,
                actor_id=getattr(state.actor, "actor_id", "") if getattr(state, "actor", None) else "",
                goal_key=goal_key,
                action_key=action_key,
                success=success,
                previous=previous,
                updated=updated,
            )
        )

        if success:
            logger.debug(
                "Learned transition: %s (goal=%s) -> success (p=%.2f)",
                action_key,
                goal_key,
                current_model.known_transitions[(goal_key, action_key)][-1].probability,
            )
        else:
            logger.debug(
                "Learned transition: %s (goal=%s) -> failure (p=%.2f)",
                action_key,
                goal_key,
                current_model.known_transitions[(goal_key, action_key)][-1].probability,
            )

    # Real, accumulated learning-state size (this policy's TransitionModel,
    # loaded from persistence at actor-registration time and saved again
    # by the caller below) — not per-tick, so a gauge, not a counter.
    _obs.gauge("learn.known_transitions", float(len(current_model.known_transitions)))
    state.transition_model = current_model


def _prediction_to_graph(prediction: Any, plan_steps: tuple = (), execution_id: str = "") -> dict[str, Any]:
    """Convert PredictionResult to a graph for comparison.

    Uses plan step actions as canonical node IDs so prediction and
    execution graphs match node-for-node.

    Comparator-hardening pass: "expected" now comes from the single
    SELECTED candidate (PredictionResult.selected -- the scenario that
    was actually chosen, matching the plan that went on to execute), not
    every candidate merged together. Comparing execution against a blend
    of every considered scenario (including rejected counterfactuals like
    "what if the store is closed") was never a meaningful expectation --
    only the selected one describes what the plan-that-ran was predicted
    to do. Falls back to candidates[0] when nothing was selected
    (preserves prior lenient behavior for a malformed/partial
    PredictionResult) rather than producing an empty graph.

    predicted_state/predicted_reward are now sourced from the selected
    candidate's real Prediction.world_snapshot/expected_utility (confirmed
    present on kernel/pipeline/prediction/domain.py::Prediction, verified
    via prediction_result_to_dict's real serialized shape) instead of
    hardcoded {}/0.0 -- previously every actor-tick comparison's
    state_diff/reward_diff compared real execution against nothing,
    which silently pinned epistemic_loss's state term to worst-case and
    policy_loss to "raw reward" rather than a real prediction error.
    events/artifacts stay empty -- confirmed nothing in Prediction
    populates them yet; left honest rather than fabricated.
    """
    if isinstance(prediction, dict) and "nodes" in prediction:
        return prediction

    # Canonical IDs from plan steps
    step_ids = [step.action for step in plan_steps] if plan_steps else []

    candidates = prediction.get("candidates", []) if isinstance(prediction, dict) else []
    selected = prediction.get("selected") if isinstance(prediction, dict) else None
    if selected is None:
        selected = candidates[0] if candidates else (prediction if isinstance(prediction, dict) else {})

    pred = selected.get("prediction", selected) if isinstance(selected, dict) else {}
    outcomes = pred.get("predicted_outcomes", [])
    grounding_score = selected.get("probability", 0.5) if isinstance(selected, dict) else 0.5
    predicted_state = pred.get("world_snapshot") or {}
    predicted_reward = pred.get("expected_utility", 0.0)

    nodes = []
    edges = []
    execution_order = []
    for j, outcome in enumerate(outcomes):
        # Use canonical step ID if available
        node_id = step_ids[j] if j < len(step_ids) else f"step_{j}"
        nodes.append(
            {
                "id": node_id,
                "label": outcome.get("description", node_id),
                "type": "step",
                "predicted_success": outcome.get("success", True),
                "predicted_probability": outcome.get("probability", 1.0),
            }
        )
        execution_order.append([node_id])
        if j > 0 and nodes:
            edges.append(
                {
                    "from": step_ids[j - 1] if j - 1 < len(step_ids) else f"step_{j - 1}",
                    "to": node_id,
                    "type": "depends_on",
                }
            )

    return {
        "graph_id": execution_id,
        "timestamp": time.time(),
        "nodes": nodes,
        "edges": edges,
        "execution_order": execution_order,
        "metadata": {
            "summary": {
                "predicted_reward": predicted_reward,
                "grounding_score": grounding_score,
                "predicted_state": predicted_state,
                "operations": [n["id"] for n in nodes],
                "events": [],
                "artifacts": [],
                "latency_ms": 0.0,
            }
        },
    }


def _execution_to_graph(execution: Any, plan_steps: tuple = (), execution_id: str = "") -> dict[str, Any]:
    """Convert ExecutionResult to a graph for comparison.

    Uses plan step actions as canonical node IDs so prediction and
    execution graphs match node-for-node.

    Comparator-hardening pass: sets graph_id/timestamp (execution_id and
    now, respectively) and each node's real action_id -- previously
    neither the top-level envelope nor any node carried any identity, so
    every actor-tick ComparisonResult/persisted snapshot was untraceable
    back to the real observation it came from (canonical_graph_envelope
    reads these fields if present but never fabricates them).
    """
    if isinstance(execution, dict) and "nodes" in execution:
        return execution

    step_ids = [step.action for step in plan_steps] if plan_steps else []

    nodes = []
    edges = []
    execution_order = []
    total_latency = 0.0
    reward = 0.0
    success_count = 0
    failure_count = 0

    actions = []
    if hasattr(execution, "actions"):
        actions = list(execution.actions) if execution.actions else []
    elif isinstance(execution, dict):
        actions = execution.get("actions", [])

    for i, action in enumerate(actions):
        action_dict = (
            action
            if isinstance(action, dict)
            else (
                {
                    "action_id": getattr(action, "action_id", f"step_{i}"),
                    "success": getattr(action, "success", True),
                    "error": getattr(action, "error", ""),
                    "result": getattr(action, "result", {}),
                    "latency_ms": getattr(action, "latency_ms", 0.0),
                }
            )
        )
        # A step ActionExecutor.execute() never actually dispatched because
        # a dependency failed first (result={"blocked_by_dependency": ...},
        # see execute()'s own depends_on gating) is neither a success nor a
        # genuine failure — it's NO OBSERVATION at all. Reporting it as a
        # node with success=False made it indistinguishable, by the time it
        # reached _compare_node_outcomes (comparator_runtime.py) and then
        # _learn_transitions below, from a step that genuinely ran and
        # failed — even though _learn_transitions already has explicit,
        # correct handling for "no observation" (skips node_diff entries
        # whose actual_success is None) that this simply never fed.
        #
        # Setting success=None on an included node does NOT achieve that:
        # _compare_node_outcomes only treats a MISSING node as "no
        # observation" (exec_node_map.get(node_id, None)) — a node that IS
        # present but has success=None instead falls through its own
        # "infer from status" branch, which reads a "status" key this graph
        # never sets, always resolving to False. The only way to make
        # exec_node_map genuinely lack an entry (so actual_success really
        # comes back None) is to never add the node at all — a blocked
        # step contributes no edge/execution_order entry either, since it
        # never actually ran.
        action_result = action_dict.get("result")
        # not_attempted: the same "no real observation" exclusion as
        # blocked_by_dependency below, for outcomes belief_runtime.py's
        # _reject_plan (Decision/PlanValidator/compile rejection) and the
        # permission-denied path produce — neither ever invoked the real
        # capability, so success=False there is not a genuine failure
        # observation and must not reach _learn_transitions as one.
        was_blocked = isinstance(action_result, dict) and (
            "blocked_by_dependency" in action_result or action_result.get("not_attempted")
        )
        if was_blocked:
            continue

        success = action_dict.get("success", True)
        error = action_dict.get("error", "")

        if success:
            success_count += 1
        else:
            failure_count += 1

        total_latency += action_dict.get("latency_ms", 0.0)

        # Use canonical step ID
        node_id = step_ids[i] if i < len(step_ids) else action_dict.get("action_id", f"step_{i}")

        nodes.append(
            {
                "id": node_id,
                "label": node_id,
                "type": "step",
                "success": success,
                "error": error,
                "action_id": action_dict.get("action_id", ""),
            }
        )
        execution_order.append([node_id])
        if i > 0 and nodes:
            edges.append(
                {
                    "from": step_ids[i - 1] if i - 1 < len(step_ids) else f"step_{i - 1}",
                    "to": node_id,
                    "type": "depends_on",
                }
            )

    if hasattr(execution, "goal_achieved") and execution.goal_achieved:
        reward = 1.0
    elif success_count > 0:
        reward = success_count / max(1, success_count + failure_count) * 0.5

    return {
        "graph_id": execution_id,
        "timestamp": time.time(),
        "nodes": nodes,
        "edges": edges,
        "execution_order": execution_order,
        "latency_ms": total_latency,
        "reward": reward,
        "confidence": success_count / max(1, len(actions)),
        "operations": [n["id"] for n in nodes],
        "events": [],
        "artifacts": [],
        "state": {
            "success_count": success_count,
            "failure_count": failure_count,
        },
    }


def _extract_capability(description: str) -> str:
    """Extract capability name from a prediction description."""
    import re

    m = re.search(r"'(Process|Achieve|Execute)\s+(\w+)", description)
    if m:
        return m.group(2).lower()
    m = re.search(r"'(\w+)'", description)
    if m:
        return m.group(1).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")
    return slug[:60] if slug else ""


def build_comparison_integrated_runtime(
    *,
    learning_policy: LearningPolicy = LearningPolicy(),
    transition_model: TransitionModel | None = None,
    counterfactual_assumptions: tuple[CounterfactualAssumption, ...] = (),
    rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD,
    time_horizon: float = 0.0,
    observation_provider: Any = None,  # only this is passed
    belief_fusion: Any = None,
    planning_engine: Any = None,
    plan_validator: Any = None,
    execution_engine: Any = None,
    event_bus: Any = None,
    society_activation: Any = None,
    context_engine: Any = None,
    current_plans: dict[str, Any] | None = None,
):
    """Convenience factory: a CognitiveRuntime with Learn, Compile-Φ,
    Predict, and Compare all enhanced via ComparisonIntegratedPolicy.

    society_activation (Society as Organizational Context refactor): an
    optional kernel/society/activation.py::SocietyActivationEngine, threaded
    down to ReasoningRuntime so this actor's reasoning tick selects relevant
    societies for its goal — see SocietyRuntime.register_actor()'s use of
    this factory.

    context_engine (Context-Aware Personalized Planning refactor): an
    optional kernel/pipeline/planning/context_engine.py::
    ContextConstructionEngine, threaded straight to CognitiveRuntime (not
    through ComparisonIntegratedPolicy/ReasoningRuntime — _generate_plan
    lives on CognitiveRuntime itself)."""
    from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime as PipelineCognitiveRuntime

    # NOTE: only observations passed others will be none and must use the default fallback
    return PipelineCognitiveRuntime(
        observation_provider=observation_provider,
        belief_fusion=belief_fusion,
        planning_engine=planning_engine,
        plan_validator=plan_validator,
        execution_engine=execution_engine,
        policy=ComparisonIntegratedPolicy(
            learning_policy=learning_policy,
            transition_model=transition_model,
            counterfactual_assumptions=counterfactual_assumptions,
            rejection_threshold=rejection_threshold,
            time_horizon=time_horizon,
            society_activation=society_activation,
            capability_runtime=execution_engine,
            current_plans=current_plans,
        ),
        event_bus=event_bus,
        context_engine=context_engine,
    )
