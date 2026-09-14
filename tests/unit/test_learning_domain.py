"""Tests for the learning domain model (Step 10.1).

Validates construction, defaults, and immutability for all 9 learning
domain types (the 8 named in the spec plus Provenance, a necessary
supporting type — the same pattern Step 8.1's ValidationStatus and Step
9.1's RetryPolicy/RetryStrategy used). Pure data — no algorithm, no mocking
needed.
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.monkey_brain.kernel.pipeline.learning import (
    LearningEvent,
    LearningObservation,
    LearningSignal,
    LearningOutcome,
    Provenance,
    LearningExperience,
    LearningPolicy,
    LearningContext,
    LearningResult,
)

# ═══════════════════════════════════════════════════════════════════════════
# LearningEvent
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningEvent:
    def test_construction(self):
        event = LearningEvent(event_type="plan_selected", description="walk_to_store chosen")
        assert event.event_type == "plan_selected"
        assert event.event_id  # auto-generated

    def test_event_ids_are_unique(self):
        assert LearningEvent().event_id != LearningEvent().event_id

    def test_frozen(self):
        event = LearningEvent()
        with pytest.raises(FrozenInstanceError):
            event.event_type = "x"


# ═══════════════════════════════════════════════════════════════════════════
# LearningObservation
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningObservation:
    def test_construction(self):
        obs = LearningObservation(
            entity="Store A",
            attribute="stocks_whole_milk",
            value=True,
            confidence=0.9,
            source="execution_outcome",
        )
        assert obs.entity == "Store A"
        assert obs.value is True
        assert obs.confidence == 0.9

    def test_defaults(self):
        obs = LearningObservation()
        assert obs.confidence == 1.0
        assert obs.value is None

    def test_frozen(self):
        obs = LearningObservation()
        with pytest.raises(FrozenInstanceError):
            obs.entity = "x"


# ═══════════════════════════════════════════════════════════════════════════
# LearningSignal
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningSignal:
    def test_construction(self):
        signal = LearningSignal(
            kind="belief",
            subject="Store A.stocks_whole_milk",
            direction=1.0,
            strength=0.8,
        )
        assert signal.kind == "belief"
        assert signal.direction == 1.0
        assert signal.signal_id  # auto-generated

    def test_signal_ids_are_unique(self):
        assert LearningSignal().signal_id != LearningSignal().signal_id

    def test_defaults_are_neutral(self):
        signal = LearningSignal()
        assert signal.direction == 0.0
        assert signal.strength == 0.5

    def test_frozen(self):
        signal = LearningSignal()
        with pytest.raises(FrozenInstanceError):
            signal.direction = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# LearningOutcome
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningOutcome:
    def test_construction(self):
        outcome = LearningOutcome(goal_achieved=True, partial=False, cost=0.25, duration_seconds=360.0)
        assert outcome.goal_achieved is True
        assert outcome.duration_seconds == 360.0

    def test_defaults(self):
        outcome = LearningOutcome()
        assert outcome.goal_achieved is False
        assert outcome.errors == ()

    def test_frozen(self):
        outcome = LearningOutcome()
        with pytest.raises(FrozenInstanceError):
            outcome.goal_achieved = True


# ═══════════════════════════════════════════════════════════════════════════
# Provenance
# ═══════════════════════════════════════════════════════════════════════════


class TestProvenance:
    def test_construction(self):
        prov = Provenance(
            actor_id="alice",
            tenant_id="acme",
            run_id="run-001",
            source="execution_outcome",
        )
        assert prov.actor_id == "alice"
        assert prov.source == "execution_outcome"

    def test_defaults(self):
        prov = Provenance()
        assert prov.tenant_id == "default"
        assert prov.recorded_at > 0

    def test_frozen(self):
        prov = Provenance()
        with pytest.raises(FrozenInstanceError):
            prov.actor_id = "x"


# ═══════════════════════════════════════════════════════════════════════════
# LearningExperience — the central type; matches the acceptance-criteria example
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningExperience:
    def test_construction_minimal(self):
        experience = LearningExperience()
        assert isinstance(experience.outcome, LearningOutcome)
        assert isinstance(experience.provenance, Provenance)
        assert experience.observations == ()
        assert experience.events == ()
        assert experience.signals == ()
        assert experience.experience_id  # auto-generated

    def test_experience_ids_are_unique(self):
        assert LearningExperience().experience_id != LearningExperience().experience_id

    def test_goal_plan_execution_accept_arbitrary_types(self):
        """Deliberately Any-typed — must work for both belief_state.Plan-shaped
        data (the default LLMPlanner path) and planning.domain.Plan-
        shaped data (the IntegratedPlanningEngine path), or anything else."""
        experience = LearningExperience(
            goal="acquire_milk",
            plan={"strategy": "walk_to_store"},
            execution={"success_count": 4},
        )
        assert experience.goal == "acquire_milk"
        assert experience.plan == {"strategy": "walk_to_store"}
        assert experience.execution == {"success_count": 4}

    def test_captures_the_acceptance_example_scenario(self):
        """'Successfully purchased milk from Store A in 6 minutes' — the
        exact illustrative example from Step 10's acceptance criteria."""
        observation = LearningObservation(
            entity="Store A",
            attribute="stocks_whole_milk",
            value=True,
            confidence=0.9,
            source="execution_outcome",
        )
        outcome = LearningOutcome(goal_achieved=True, partial=False, cost=0.25, duration_seconds=360.0)
        provenance = Provenance(
            actor_id="alice",
            tenant_id="acme",
            run_id="run-001",
            source="execution_outcome",
        )
        signal = LearningSignal(
            kind="belief",
            subject="Store A.stocks_whole_milk",
            direction=1.0,
            strength=0.8,
        )
        event = LearningEvent(event_type="goal_achieved", description="Purchased milk from Store A")

        experience = LearningExperience(
            goal="acquire_milk",
            observations=(observation,),
            outcome=outcome,
            reward=0.92,
            confidence=0.85,
            provenance=provenance,
            events=(event,),
            signals=(signal,),
            metadata={"summary": "Successfully purchased milk from Store A in 6 minutes."},
        )

        assert experience.reward == 0.92
        assert experience.outcome.goal_achieved is True
        assert experience.outcome.duration_seconds == 360.0
        assert experience.provenance.actor_id == "alice"
        assert experience.signals[0].subject == "Store A.stocks_whole_milk"
        assert experience.observations[0].entity == "Store A"

    def test_frozen(self):
        experience = LearningExperience()
        with pytest.raises(FrozenInstanceError):
            experience.reward = 0.9


# ═══════════════════════════════════════════════════════════════════════════
# LearningPolicy
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningPolicy:
    def test_construction(self):
        policy = LearningPolicy(
            name="default_rl",
            strategy="reinforcement",
            parameters={"learning_rate": 0.1},
        )
        assert policy.strategy == "reinforcement"
        assert policy.parameters["learning_rate"] == 0.1

    def test_defaults(self):
        policy = LearningPolicy()
        assert policy.name == "default"
        assert policy.strategy == "reinforcement"

    def test_frozen(self):
        policy = LearningPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.strategy = "bayesian"


# ═══════════════════════════════════════════════════════════════════════════
# LearningContext / LearningResult
# ═══════════════════════════════════════════════════════════════════════════


class TestLearningContext:
    def test_composes_experience_and_policy(self):
        experience = LearningExperience(goal="acquire_milk")
        policy = LearningPolicy(strategy="bayesian")
        context = LearningContext(experience=experience, policy=policy)

        assert context.experience.goal == "acquire_milk"
        assert context.policy.strategy == "bayesian"

    def test_defaults(self):
        context = LearningContext()
        assert isinstance(context.experience, LearningExperience)
        assert isinstance(context.policy, LearningPolicy)

    def test_frozen(self):
        context = LearningContext()
        with pytest.raises(FrozenInstanceError):
            context.policy = LearningPolicy()


class TestLearningResult:
    def test_construction(self):
        signal = LearningSignal(kind="belief")
        result = LearningResult(
            experience_id="exp-1",
            reward=0.92,
            signals_applied=(signal,),
            belief_updated=True,
            world_updated=True,
            rationale="Goal achieved quickly and within budget.",
        )
        assert result.reward == 0.92
        assert result.belief_updated is True
        assert len(result.signals_applied) == 1

    def test_defaults(self):
        result = LearningResult()
        assert result.reward == 0.0
        assert result.belief_updated is False
        assert result.world_updated is False

    def test_frozen(self):
        result = LearningResult()
        with pytest.raises(FrozenInstanceError):
            result.reward = 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — model-only, no coupling to runtime/execution engine
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
    """Actual `import X` / `from X import ...` module names — not a bare
    substring scan, so an explanatory docstring mentioning a filename by
    name (e.g. "Do not modify ... belief_runtime.py") isn't mistaken for a
    real dependency. Mirrors the ast-based check test_pipeline_orchestrator.py
    already uses for the same reason."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


class TestOwnershipBoundary:
    def test_no_execution_engine_or_cognitive_runtime_coupling(self):
        """Step 10.1 must not import ExecutionEngine or CognitiveRuntime —
        those stay untouched until Step 10.7."""
        import src.monkey_brain.kernel.pipeline.learning.domain as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
        ):
            assert forbidden not in imports, f"learning/domain.py must not import: {forbidden}"

    def test_pure_stdlib_dependencies_only(self):
        """goal/plan/execution are deliberately Any-typed (see module
        docstring) — domain.py has no reason to import planning.domain or
        execution_runtime.domain at all."""
        import src.monkey_brain.kernel.pipeline.learning.domain as mod

        imports = _imported_modules(mod)
        for module_name in imports:
            assert not module_name.startswith("src.monkey_brain.kernel.pipeline.planning"), module_name
            assert not module_name.startswith("src.monkey_brain.kernel.pipeline.execution_runtime"), module_name
