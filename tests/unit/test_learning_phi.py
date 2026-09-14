"""Tests for the Φ (Phi) Compiler (Step 10.8).

PhiCompiler.compile() is a pure function over (LearningExperience,
LearningResult) — no CognitiveState, no mocking. The runtime-wired half
(the Compile-Φ stage override) is covered in test_learning_integration.py's
companion assertions, added alongside this file.
"""

from __future__ import annotations

import json

import pytest
from src.monkey_brain.kernel.pipeline.learning.domain import (
    LearningExperience,
    LearningObservation,
    LearningOutcome,
    LearningResult,
    LearningSignal,
    Provenance,
)
from src.monkey_brain.kernel.pipeline.learning.phi import (
    PhiArtifact,
    PhiCompiler,
    compile_phi,
    phi_to_dict,
)
from src.monkey_brain.kernel.pipeline.learning.policies import ReinforcementLearningPolicy
from src.monkey_brain.kernel.pipeline.learning.reward import ExperienceRewardEngine


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
    def test_compiles_the_milk_scenario_into_a_phi_artifact(self):
        """'Φ Artifact: Structured summary of the cognitive cycle, ready
        for persistence, sharing, or future prediction.' from Step 10's
        acceptance criteria, chained through the real reward+policy engines."""
        experience = _milk_experience()
        result = ReinforcementLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0)).learn(
            experience
        )

        artifact = compile_phi(experience, result)

        assert isinstance(artifact, PhiArtifact)
        assert artifact.goal_signature == "acme::acquire_milk"
        assert "Achieved" in artifact.outcome_summary
        assert artifact.reward == pytest.approx(0.92)
        assert artifact.belief_updated is True
        assert artifact.world_updated is True

    def test_top_signal_summary_names_the_reinforced_relationship(self):
        experience = _milk_experience()
        result = ReinforcementLearningPolicy(reward_engine=ExperienceRewardEngine(duration_budget_seconds=900.0)).learn(
            experience
        )

        artifact = compile_phi(experience, result)

        assert "Store A.stocks_whole_milk" in artifact.top_signal_summary


# ═══════════════════════════════════════════════════════════════════════════
# goal_signature — reuses capture.py's metadata convention
# ═══════════════════════════════════════════════════════════════════════════


class TestGoalSignature:
    def test_reads_goal_name_from_metadata_when_present(self):
        experience = LearningExperience(goal="some_object", metadata={"goal_name": "acquire_milk"})
        artifact = compile_phi(experience, LearningResult())
        assert artifact.goal_signature == "default::acquire_milk"

    def test_falls_back_to_goal_string_when_no_metadata(self):
        experience = LearningExperience(goal="raw_goal_string")
        artifact = compile_phi(experience, LearningResult())
        assert artifact.goal_signature == "default::raw_goal_string"

    def test_falls_back_to_unknown_when_goal_is_none(self):
        experience = LearningExperience(goal=None)
        artifact = compile_phi(experience, LearningResult())
        assert artifact.goal_signature == "default::unknown_goal"

    def test_scopes_goal_signature_by_tenant(self):
        """Two tenants pursuing a goal with the same name must not collide
        in CapabilityPromotionTracker's streaks — see domain.py's
        scoped_goal_signature docstring."""
        acme = LearningExperience(
            goal="g", metadata={"goal_name": "acquire_milk"}, provenance=Provenance(tenant_id="acme")
        )
        globex = LearningExperience(
            goal="g", metadata={"goal_name": "acquire_milk"}, provenance=Provenance(tenant_id="globex")
        )
        assert compile_phi(acme, LearningResult()).goal_signature == "acme::acquire_milk"
        assert compile_phi(globex, LearningResult()).goal_signature == "globex::acquire_milk"


# ═══════════════════════════════════════════════════════════════════════════
# outcome_summary — reflects success/partial/failure
# ═══════════════════════════════════════════════════════════════════════════


class TestOutcomeSummary:
    def test_success_mentions_achieved(self):
        outcome = LearningOutcome(goal_achieved=True, duration_seconds=120.0)
        experience = LearningExperience(outcome=outcome, metadata={"goal_name": "x"})
        artifact = compile_phi(experience, LearningResult(reward=0.9))
        assert "Achieved" in artifact.outcome_summary
        assert "120" in artifact.outcome_summary

    def test_partial_mentions_partially_achieved(self):
        outcome = LearningOutcome(goal_achieved=False, partial=True)
        experience = LearningExperience(outcome=outcome, metadata={"goal_name": "x"})
        artifact = compile_phi(experience, LearningResult(reward=0.3))
        assert "Partially achieved" in artifact.outcome_summary

    def test_failure_mentions_failed(self):
        outcome = LearningOutcome(goal_achieved=False, partial=False)
        experience = LearningExperience(outcome=outcome, metadata={"goal_name": "x"})
        artifact = compile_phi(experience, LearningResult(reward=0.0))
        assert "Failed" in artifact.outcome_summary


# ═══════════════════════════════════════════════════════════════════════════
# top_signal_summary
# ═══════════════════════════════════════════════════════════════════════════


class TestTopSignalSummary:
    def test_no_signals_yields_empty_summary(self):
        artifact = compile_phi(LearningExperience(), LearningResult(signals_applied=()))
        assert artifact.top_signal_summary == ""

    def test_picks_the_highest_strength_signal(self):
        weak = LearningSignal(kind="world", subject="a", direction=1.0, strength=0.1)
        strong = LearningSignal(kind="belief", subject="b", direction=1.0, strength=0.9)
        result = LearningResult(signals_applied=(weak, strong))

        artifact = compile_phi(LearningExperience(), result)

        assert "b" in artifact.top_signal_summary
        assert "belief" in artifact.top_signal_summary

    def test_negative_direction_says_weakens(self):
        signal = LearningSignal(kind="belief", subject="x", direction=-1.0, strength=0.5)
        artifact = compile_phi(LearningExperience(), LearningResult(signals_applied=(signal,)))
        assert "weakens" in artifact.top_signal_summary

    def test_positive_direction_says_reinforces(self):
        signal = LearningSignal(kind="belief", subject="x", direction=1.0, strength=0.5)
        artifact = compile_phi(LearningExperience(), LearningResult(signals_applied=(signal,)))
        assert "reinforces" in artifact.top_signal_summary


# ═══════════════════════════════════════════════════════════════════════════
# Field pass-through
# ═══════════════════════════════════════════════════════════════════════════


class TestFieldPassthrough:
    def test_experience_id_prefers_result_over_experience(self):
        experience = LearningExperience()
        result = LearningResult(experience_id="result-id")
        artifact = compile_phi(experience, result)
        assert artifact.experience_id == "result-id"

    def test_experience_id_falls_back_to_experience_when_result_blank(self):
        experience = LearningExperience()
        result = LearningResult(experience_id="")
        artifact = compile_phi(experience, result)
        assert artifact.experience_id == experience.experience_id

    def test_confidence_comes_from_experience(self):
        experience = LearningExperience(confidence=0.77)
        artifact = compile_phi(experience, LearningResult())
        assert artifact.confidence == 0.77

    def test_provenance_carried_through(self):
        provenance = Provenance(actor_id="alice", tenant_id="acme")
        experience = LearningExperience(provenance=provenance)
        artifact = compile_phi(experience, LearningResult())
        assert artifact.provenance == provenance

    def test_metadata_carried_through(self):
        experience = LearningExperience(metadata={"goal_name": "x", "intent_type": "shopping"})
        artifact = compile_phi(experience, LearningResult())
        assert artifact.metadata == {"goal_name": "x", "intent_type": "shopping"}

    def test_signal_count_matches_signals_applied_length(self):
        signals = (LearningSignal(), LearningSignal(), LearningSignal())
        artifact = compile_phi(LearningExperience(), LearningResult(signals_applied=signals))
        assert artifact.signal_count == 3


# ═══════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestPhiToDict:
    def test_json_serializable(self):
        experience = _milk_experience()
        result = ReinforcementLearningPolicy().learn(experience)
        artifact = compile_phi(experience, result)

        d = phi_to_dict(artifact)
        json.dumps(d)  # must not raise

    def test_all_fields_present(self):
        artifact = compile_phi(LearningExperience(), LearningResult())
        d = phi_to_dict(artifact)
        for key in (
            "phi_id",
            "experience_id",
            "goal_signature",
            "outcome_summary",
            "reward",
            "confidence",
            "belief_updated",
            "world_updated",
            "signal_count",
            "top_signal_summary",
            "provenance",
            "compiled_at",
            "metadata",
        ):
            assert key in d

    def test_provenance_serializes_as_nested_dict(self):
        experience = LearningExperience(provenance=Provenance(actor_id="alice"))
        artifact = compile_phi(experience, LearningResult())
        d = phi_to_dict(artifact)
        assert d["provenance"]["actor_id"] == "alice"


# ═══════════════════════════════════════════════════════════════════════════
# PhiArtifact — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestPhiArtifactImmutability:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        artifact = PhiArtifact()
        with pytest.raises(FrozenInstanceError):
            artifact.reward = 1.0

    def test_phi_ids_are_unique(self):
        assert (
            PhiCompiler().compile(LearningExperience(), LearningResult()).phi_id
            != PhiCompiler().compile(LearningExperience(), LearningResult()).phi_id
        )


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
        import src.monkey_brain.kernel.pipeline.learning.phi as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in ("belief_runtime", "belief_state", "execution_state", "action_executor"):
            assert forbidden not in imports, f"phi.py must not import: {forbidden}"

    def test_depends_only_on_learning_domain(self):
        import src.monkey_brain.kernel.pipeline.learning.phi as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        for module_name in project_imports:
            assert module_name.startswith("src.monkey_brain.kernel.pipeline.learning"), module_name
