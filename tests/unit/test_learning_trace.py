"""Tests for Learning Trace & Explainability (Step 10.9).

build_learning_trace() / learn_with_trace() are pure functions over
(LearningExperience, LearningPolicy) — no CognitiveState, no mocking,
mirroring Step 9.8's ExecutionTrace tests exactly in shape.
"""

from __future__ import annotations

import json

import pytest

from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningOutcome,
    LearningPolicy,
    Provenance,
)
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine
from src.monkey_brain.kernel.pipeline.learning.policies import (
    ReinforcementLearningPolicy,
    resolve_policy,
)
from src.monkey_brain.kernel.pipeline.learning.phi import PhiCompiler
from src.monkey_brain.kernel.pipeline.learning.trace import (
    LearningTrace,
    LearningTraceStage,
    build_learning_trace,
    learn_with_trace,
)


def _milk_experience() -> LearningExperience:
    obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", value=True, confidence=0.9)
    outcome = LearningOutcome(goal_achieved=True, cost=0.0, duration_seconds=360.0)
    provenance = Provenance(actor_id="alice", tenant_id="acme", run_id="run-001")
    return LearningExperience(
        goal="acquire_milk",
        outcome=outcome,
        observations=(obs,),
        provenance=provenance,
        metadata={"goal_name": "acquire_milk"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Acceptance-criteria scenario
# ═══════════════════════════════════════════════════════════════════════════


class TestAcceptanceScenario:
    def test_reproduces_the_acceptance_criteria_flow(self):
        """'Execution Result -> Experience Capture -> Reward Evaluation ->
        Belief Update -> World Model Update -> Learning Policy -> Φ
        Compilation -> Learning Trace' from Step 10's own acceptance
        criteria, reproduced almost verbatim by the real narrative."""
        experience = _milk_experience()
        result = ReinforcementLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0)).learn(
            experience
        )
        trace = build_learning_trace(experience, LearningPolicy(strategy="reinforcement"), result=result)

        narrative = trace.explain()

        assert "+0.92" in narrative
        assert "Increase confidence that Store A stocks whole milk" in narrative
        assert "Store A stocks whole milk relationship reinforced" in narrative

    def test_header_lists_all_stages_in_order_ending_with_learning_trace(self):
        trace = learn_with_trace(_milk_experience(), LearningPolicy(strategy="reinforcement"))
        header = trace.explain().split("\n")[0]

        assert header == (
            "Experience Capture -> Reward Evaluation -> Belief Update -> "
            "World Model Update -> Learning Policy -> Φ Compilation -> Learning Trace"
        )

    def test_stage_names_and_order(self):
        trace = learn_with_trace(_milk_experience())
        assert [s.name for s in trace.stages] == [
            "Experience Capture",
            "Reward Evaluation",
            "Belief Update",
            "World Model Update",
            "Learning Policy",
            "Φ Compilation",
        ]


# ═══════════════════════════════════════════════════════════════════════════
# Ambiguous / no-op stages explain themselves
# ═══════════════════════════════════════════════════════════════════════════


class TestNoOpStagesAreExplained:
    def test_conservative_ambiguous_reward_explains_why_belief_was_skipped(self):
        obs = LearningObservation(entity="Store A", attribute="stocks_whole_milk", confidence=0.9)
        experience = LearningExperience(
            outcome=LearningOutcome(goal_achieved=False, partial=True),
            observations=(obs,),
            metadata={"goal_name": "acquire_milk"},
        )

        trace = learn_with_trace(experience, LearningPolicy(strategy="conservative"))

        belief_stage = next(s for s in trace.stages if s.name == "Belief Update")
        assert "No belief update applied" in belief_stage.summary
        assert "ambiguous" in belief_stage.summary

    def test_passive_policy_explains_no_updates_in_both_stages(self):
        trace = learn_with_trace(_milk_experience(), LearningPolicy(strategy="passive"))

        belief_stage = next(s for s in trace.stages if s.name == "Belief Update")
        world_stage = next(s for s in trace.stages if s.name == "World Model Update")
        assert "No belief update applied" in belief_stage.summary
        assert "No world update applied" in world_stage.summary


# ═══════════════════════════════════════════════════════════════════════════
# Reusing precomputed result/phi (no double computation)
# ═══════════════════════════════════════════════════════════════════════════


class TestAvoidsRecomputation:
    def test_precomputed_result_and_phi_are_reused_not_recomputed(self):
        experience = _milk_experience()
        result = resolve_policy(LearningPolicy(strategy="passive")).learn(experience)
        phi = PhiCompiler().compile(experience, result)

        trace = build_learning_trace(experience, LearningPolicy(strategy="passive"), result=result, phi=phi)

        assert trace.result is result
        assert trace.phi is phi

    def test_omitting_result_and_phi_computes_fresh(self):
        experience = _milk_experience()
        trace = build_learning_trace(experience, LearningPolicy(strategy="reinforcement"))
        assert trace.result.experience_id == experience.experience_id


# ═══════════════════════════════════════════════════════════════════════════
# to_dict — serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestToDict:
    def test_json_serializable(self):
        trace = learn_with_trace(_milk_experience(), LearningPolicy(strategy="reinforcement"))
        d = trace.to_dict()
        json.dumps(d)  # must not raise

    def test_narrative_matches_explain(self):
        trace = learn_with_trace(_milk_experience())
        assert trace.to_dict()["narrative"] == trace.explain()

    def test_stages_serialized_with_all_six_entries(self):
        trace = learn_with_trace(_milk_experience())
        d = trace.to_dict()
        assert len(d["stages"]) == 6
        assert all("name" in s and "summary" in s and "detail" in s for s in d["stages"])

    def test_result_and_phi_present(self):
        trace = learn_with_trace(_milk_experience())
        d = trace.to_dict()
        assert "result" in d and "reward" in d["result"]
        assert "phi" in d and "phi_id" in d["phi"]


# ═══════════════════════════════════════════════════════════════════════════
# LearningTrace / LearningTraceStage — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_trace_frozen(self):
        from dataclasses import FrozenInstanceError

        trace = LearningTrace()
        with pytest.raises(FrozenInstanceError):
            trace.narrative = "x"

    def test_stage_frozen(self):
        from dataclasses import FrozenInstanceError

        stage = LearningTraceStage()
        with pytest.raises(FrozenInstanceError):
            stage.summary = "x"

    def test_trace_ids_are_unique(self):
        assert learn_with_trace(_milk_experience()).trace_id != learn_with_trace(_milk_experience()).trace_id


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
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
    def test_no_runtime_coupling(self):
        import src.monkey_brain.kernel.pipeline.learning.trace as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "belief_state",
            "execution_state",
            "action_executor",
        ):
            assert forbidden not in imports, f"trace.py must not import: {forbidden}"

    def test_depends_only_on_learning_subpackage(self):
        import src.monkey_brain.kernel.pipeline.learning.trace as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
