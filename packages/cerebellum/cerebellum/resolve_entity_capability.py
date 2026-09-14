"""ResolveEntityCapability — Entity Resolution as a first-class capability.

This module converts the existing entity resolution logic into a standard
capability that conforms to the ICapability interface.

Key Principles:
- No special preprocessing stage
- Treated identically to other capabilities
- Augments execution state
- Contributes to policy learning
- Selected by Bellman optimizer
"""

from __future__ import annotations

import time
from typing import Any

try:
    from src.monkey_brain.kernel.capability_interface import EntityResolutionCapability
    from src.monkey_brain.kernel.execution_state import ExecutionState, CapabilityResult
except ImportError:
    EntityResolutionCapability = object
    ExecutionState = None
    CapabilityResult = None


class ResolveEntityCapability(EntityResolutionCapability):
    """Entity Resolution as a standard capability.

    Responsibilities:
    - Parse question
    - Identify entities
    - Identify entity types
    - Populate execution state
    - Never invoke planners directly
    - Simply augment execution state
    """

    def __init__(self, db: Any = None):
        self.db = db

    @property
    def capability_name(self) -> str:
        return "resolve_entity"

    @property
    def capability_type(self) -> str:
        return "entity_resolution"

    async def execute(self, state: ExecutionState, **kwargs: Any) -> CapabilityResult:
        """Execute entity resolution and return updated state."""
        start_time = time.time()

        # Create a copy of the state to avoid mutation
        updated_state = ExecutionState(question=state.question, intent=state.intent, phase="entity_resolution")

        # Copy over existing data
        updated_state.entities = state.entities.copy()
        updated_state.resolved_entities = state.resolved_entities.copy()
        updated_state.hierarchy = state.hierarchy.copy()
        updated_state.relationships = state.relationships.copy()
        updated_state.graph_entities = state.graph_entities.copy()
        updated_state.documents = state.documents.copy()
        updated_state.web_results = state.web_results.copy()
        updated_state.execution_stats = state.execution_stats.copy()
        updated_state.observations = state.observations.copy()
        updated_state.policy_updates = state.policy_updates.copy()
        updated_state.capability_history = state.capability_history.copy()
        updated_state.execution_trace = state.execution_trace.copy()
        updated_state.current_answer = state.current_answer
        updated_state.answer_quality = state.answer_quality
        updated_state.confidence = state.confidence
        updated_state.errors = state.errors.copy()
        updated_state.warnings = state.warnings.copy()

        # Execute entity resolution
        try:
            # Determine entity types to resolve based on question and intent
            entity_types = self._determine_entity_types(state.question, state.intent)

            # Resolve each entity type
            for entity_type in entity_types:
                resolved_entity = await self._resolve_entity(entity_type, state.question)
                if resolved_entity:
                    updated_state.add_entity(entity_type, resolved_entity)

                    # Add observation for policy learning
                    updated_state.add_observation(
                        {
                            "type": "entity_resolution",
                            "entity_type": entity_type,
                            "entity_id": resolved_entity.get("id", ""),
                            "entity_name": resolved_entity.get("name", ""),
                            "confidence": 0.9,  # High confidence for resolved entities
                        }
                    )

            # Record capability execution
            updated_state.record_capability_execution(
                self.capability_name,
                {
                    "entity_types": entity_types,
                    "resolved_count": len(updated_state.entities),
                    "timestamp": self._now(),
                },
            )

            # Set phase and confidence
            updated_state.phase = "entity_resolution"
            updated_state.confidence = self.compute_confidence(state)

            # Compute reward based on resolution success
            reward = self._compute_reward(len(entity_types), len(updated_state.entities))

            # Add policy update observation
            updated_state.add_policy_update(
                {
                    "capability": self.capability_name,
                    "reward": reward,
                    "execution_cost": 0.3,  # Low cost for entity resolution
                    "confidence": updated_state.confidence,
                }
            )

            end_time = time.time()
            latency = end_time - start_time

            return CapabilityResult(
                updated_state=updated_state,
                capability_name=self.capability_name,
                success=True,
                latency=latency,
                timestamp=self._now(),
                observations=[
                    {
                        "type": "entity_resolution_completed",
                        "resolved_entities": len(updated_state.entities),
                        "entity_types": entity_types,
                        "latency": latency,
                    }
                ],
                transition_reward=reward,
                execution_cost=0.3,
                confidence=updated_state.confidence,
                execution_metadata={
                    "entity_types_attempted": entity_types,
                    "entities_resolved": [e.get("id") for e in updated_state.entities],
                    "resolution_method": "ontology_grounded",
                },
            )

        except Exception as e:
            end_time = time.time()
            latency = end_time - start_time

            updated_state.add_error(f"Entity resolution failed: {str(e)}")
            updated_state.phase = "failed"

            return CapabilityResult(
                updated_state=updated_state,
                capability_name=self.capability_name,
                success=False,
                latency=latency,
                timestamp=self._now(),
                observations=[
                    {
                        "type": "entity_resolution_failed",
                        "error": str(e),
                        "latency": latency,
                    }
                ],
                transition_reward=0.0,
                execution_cost=0.3,
                confidence=0.1,
                execution_metadata={
                    "error": str(e),
                    "entity_types_attempted": self._determine_entity_types(state.question, state.intent),
                },
            )

    def can_execute(self, state: ExecutionState) -> bool:
        """Determine if this capability can execute given current state."""
        # Can execute if we have a question and haven't already resolved entities
        return (
            bool(state.question.strip())
            and len(state.entities) == 0
            and not state.has_errors()
            and not state.is_complete()
        )

    def estimate_reward(self, state: ExecutionState) -> float:
        """Estimate expected reward for executing this capability."""
        # High reward if no entities resolved yet
        if len(state.entities) == 0:
            return 0.8
        return 0.2

    def estimate_cost(self, state: ExecutionState) -> float:
        """Estimate execution cost for this capability."""
        # Low cost - entity resolution is fast
        return 0.2

    def compute_confidence(self, state: ExecutionState) -> float:
        """Compute confidence that this capability will succeed."""
        # High confidence if we have a reasonable question
        if len(state.question.strip()) > 10:
            return 0.9
        return 0.5

    def _determine_entity_types(self, question: str, intent: str) -> list[str]:
        """Determine which entity types to resolve based on question and intent."""
        # Common entity types in the system
        common_entity_types = [
            "line",
            "plant",
            "stage",
            "workstation",
            "machine",
            "equipment",
        ]

        # Add intent-specific entity types
        intent_entity_map = {
            "line_question": ["line", "plant"],
            "stage_question": ["stage", "line", "plant"],
            "workstation_question": ["workstation", "stage", "line"],
            "machine_question": ["machine", "workstation", "stage"],
            "plant_question": ["plant"],
            "hierarchy_question": ["plant", "line", "stage", "workstation", "machine"],
        }

        # Get entity types based on intent
        intent_specific = intent_entity_map.get(intent.lower(), [])

        # Combine and deduplicate
        entity_types = list(set(common_entity_types + intent_specific))

        return entity_types

    async def _resolve_entity(self, entity_type: str, question: str) -> dict[str, Any] | None:
        """Resolve a single entity using the existing resolve_entity function."""
        if self.db is None:
            return None

        # Use the existing resolve_entity function
        from src.monkey_brain.kernel.plan.intents.helpers import resolve_entity

        try:
            return await resolve_entity(db=self.db, entity_type=entity_type, question=question)
        except Exception:
            # Log error but don't fail the entire capability.
            # The caller decides whether to surface this as a warning.
            return None

    def _compute_reward(self, attempted: int, resolved: int) -> float:
        """Compute reward based on resolution success."""
        if attempted == 0:
            return 0.0

        success_rate = resolved / attempted

        # High reward for successful resolution
        if success_rate >= 0.8:
            return 0.9
        elif success_rate >= 0.5:
            return 0.7
        elif success_rate > 0:
            return 0.5
        else:
            return 0.2

    def _now(self) -> str:
        """Get current timestamp in ISO format."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
