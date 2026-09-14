"""Prediction — forecast pipeline outcomes without execution.

Predictions use digital twins and historical data to estimate
what would happen if a pipeline were executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline import Pipeline
from src.monkey_brain.kernel.fix.loss.loss import Loss, compute_loss
from cortex.digital_twin import DigitalTwin


@dataclass
class Prediction:
    """A predicted outcome of pipeline execution."""

    prediction_id: str = field(default_factory=lambda: f"pred-{uuid4().hex[:8]}")
    pipeline_id: str = ""
    predicted_state: dict[str, Any] = field(default_factory=dict)
    predicted_answer: str = ""
    confidence: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    twins_used: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)


class PredictionEngine:
    """Predicts outcomes of capability execution.

    Responsibilities:
    - Maintain digital twins
    - Predict pipeline outcomes
    - Track prediction accuracy

    The PredictionEngine never:
    - Executes real capabilities
    - Modifies production state
    - Makes policy decisions
    """

    def __init__(self):
        self._twins: dict[str, DigitalTwin] = {}
        self._predictions: list[Prediction] = []

    def register_twin(self, twin: DigitalTwin) -> None:
        """Register a digital twin."""
        key = f"{twin.entity_type}:{twin.entity_id}"
        self._twins[key] = twin

    def get_twin(self, entity_type: str, entity_id: str) -> DigitalTwin | None:
        """Get a digital twin by type and ID."""
        key = f"{entity_type}:{entity_id}"
        return self._twins.get(key)

    def get_twins(self, entity_type: str | None = None) -> list[DigitalTwin]:
        """Get all twins, optionally filtered by type."""
        if entity_type:
            return [t for t in self._twins.values() if t.entity_type == entity_type]
        return list(self._twins.values())

    def predict(
        self,
        pipeline: Pipeline,
        state: dict[str, Any] | None = None,
    ) -> Prediction:
        """Predict pipeline outcome without execution.

        Uses digital twins to simulate each step.
        """
        prediction = Prediction(
            pipeline_id=pipeline.pipeline_id,
        )

        current_state = dict(state or {})
        twins_used = []

        for step in pipeline.steps:
            # Try to find relevant twin
            entity_type = step.metadata.get("entity_type")
            entity_id = step.metadata.get("entity_id")

            if entity_type and entity_id:
                twin = self.get_twin(entity_type, entity_id)
                if twin:
                    twins_used.append(f"{entity_type}:{entity_id}")
                    # Predict step effect
                    predicted = twin.predict(
                        {
                            "effects": {
                                f"step:{step.step_id}": {
                                    "capability": step.capability_name,
                                    "status": "predicted",
                                }
                            }
                        }
                    )
                    current_state.update(predicted)

            # Record predicted step
            prediction.predicted_state[f"step:{step.step_id}"] = {
                "capability": step.capability_name,
                "outputs": step.outputs,
            }

        prediction.predicted_state.update(current_state)
        prediction.twins_used = twins_used
        prediction.confidence = self._calculate_confidence(twins_used, pipeline)

        self._predictions.append(prediction)
        return prediction

    def _calculate_confidence(
        self,
        twins_used: list[str],
        pipeline: Pipeline,
    ) -> float:
        """Calculate prediction confidence based on twin coverage."""
        if not pipeline.steps:
            return 0.0

        coverage = len(twins_used) / len(pipeline.steps)
        return min(1.0, coverage * 0.8 + 0.2)

    def get_prediction(self, prediction_id: str) -> Prediction | None:
        for pred in self._predictions:
            if pred.prediction_id == prediction_id:
                return pred
        return None

    def get_predictions(self) -> list[Prediction]:
        return list(self._predictions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "twins": len(self._twins),
            "predictions": len(self._predictions),
        }
