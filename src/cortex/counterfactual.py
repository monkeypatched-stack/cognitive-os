"""Counterfactual — execute alternative scenarios.

"What if we used a different capability?"
"What if the state was different?"
"What if we skipped this step?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from monkeypatched_sdk.models import Pipeline, PipelineStep
from src.cortex.prediction import Prediction, PredictionEngine


@dataclass
class Scenario:
    """A counterfactual scenario."""

    scenario_id: str = field(default_factory=lambda: f"scen-{uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    original_pipeline: Pipeline = field(default_factory=Pipeline)
    modified_pipeline: Pipeline = field(default_factory=Pipeline)
    modifications: dict[str, Any] = field(default_factory=dict)
    prediction: Prediction | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class ScenarioCounterfactualEngine:
    """Executes alternative scenarios.

    Responsibilities:
    - Create modified pipelines
    - Predict counterfactual outcomes
    - Compare scenarios

    The ScenarioCounterfactualEngine never:
    - Executes real capabilities
    - Modifies production state
    - Makes policy decisions
    """

    def __init__(self, prediction_engine: PredictionEngine):
        self._prediction_engine = prediction_engine
        self._scenarios: list[Scenario] = []

    def what_if(
        self,
        pipeline: Pipeline,
        state: dict[str, Any] | None,
        modifications: dict[str, Any],
    ) -> Scenario:
        """Create a counterfactual scenario with modifications."""
        modified_steps = []

        for step in pipeline.steps:
            new_step = PipelineStep(
                step_id=step.step_id,
                capability_name=step.capability_name,
                inputs=list(step.inputs),
                outputs=list(step.outputs),
                dependencies=list(step.dependencies),
                metadata=dict(step.metadata),
            )

            # Apply modifications
            if step.step_id in modifications:
                mod = modifications[step.step_id]
                if "capability_name" in mod:
                    new_step.capability_name = mod["capability_name"]
                if "skip" in mod and mod["skip"]:
                    continue

            modified_steps.append(new_step)

        modified_pipeline = Pipeline(
            steps=modified_steps,
            metadata=dict(pipeline.metadata),
        )

        scenario = Scenario(
            name=f"what-if-{len(self._scenarios) + 1}",
            original_pipeline=pipeline,
            modified_pipeline=modified_pipeline,
            modifications=modifications,
        )

        # Predict outcome
        scenario.prediction = self._prediction_engine.predict(
            modified_pipeline,
            state,
        )

        self._scenarios.append(scenario)
        return scenario

    def alternative(
        self,
        original_pipeline: Pipeline,
        alternative_pipeline: Pipeline,
        state: dict[str, Any] | None,
    ) -> tuple[Scenario, Scenario]:
        """Compare two different pipelines."""
        original_scenario = Scenario(
            name="original",
            original_pipeline=original_pipeline,
            modified_pipeline=original_pipeline,
        )
        original_scenario.prediction = self._prediction_engine.predict(
            original_pipeline,
            state,
        )

        alternative_scenario = Scenario(
            name="alternative",
            original_pipeline=original_pipeline,
            modified_pipeline=alternative_pipeline,
        )
        alternative_scenario.prediction = self._prediction_engine.predict(
            alternative_pipeline,
            state,
        )

        self._scenarios.extend([original_scenario, alternative_scenario])
        return original_scenario, alternative_scenario

    def skip_step(
        self,
        pipeline: Pipeline,
        step_id: str,
        state: dict[str, Any] | None,
    ) -> Scenario:
        """Create scenario with a step skipped."""
        return self.what_if(pipeline, state, {step_id: {"skip": True}})

    def swap_capability(
        self,
        pipeline: Pipeline,
        step_id: str,
        new_capability: str,
        state: dict[str, Any] | None,
    ) -> Scenario:
        """Create scenario with a different capability."""
        return self.what_if(pipeline, state, {step_id: {"capability_name": new_capability}})

    def get_scenario(self, scenario_id: str) -> Scenario | None:
        for scen in self._scenarios:
            if scen.scenario_id == scenario_id:
                return scen
        return None

    def get_scenarios(self) -> list[Scenario]:
        return list(self._scenarios)

    def to_dict(self) -> dict[str, Any]:
        return {"scenarios": len(self._scenarios)}
