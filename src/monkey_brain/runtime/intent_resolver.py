"""Intent Resolver — detects ambiguity before execution.

The runtime should never silently guess the user's intent when multiple
valid interpretations exist. Instead, detect ambiguity before execution
and request clarification when the execution would mutate world state.

This is a Cognitive OS feature, not an agent feature.
Every capability should inherit this behavior automatically.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.intent_resolver")


@dataclass
class IntentCandidate:
    """A single candidate interpretation of user intent."""

    intent: str
    confidence: float
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "parameters": self.parameters,
            "description": self.description,
        }


@dataclass
class IntentResolution:
    """Result of intent resolution."""

    is_ambiguous: bool
    candidates: list[IntentCandidate] = field(default_factory=list)
    best_candidate: IntentCandidate | None = None
    requires_clarification: bool = False
    clarification_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_ambiguous": self.is_ambiguous,
            "candidates": [c.to_dict() for c in self.candidates],
            "best_candidate": (self.best_candidate.to_dict() if self.best_candidate else None),
            "requires_clarification": self.requires_clarification,
            "clarification_question": self.clarification_question,
        }


# ── Execution Thresholds ──────────────────────────────────────────────────────

# Mutating operations require high confidence
MUTATING_THRESHOLD = 0.8
# Read-only operations can proceed with lower confidence
READONLY_THRESHOLD = 0.5
# Below this threshold, always clarify
MIN_CONFIDENCE = 0.3

# Mutating intent types
MUTATING_INTENTS = {
    "create",
    "update",
    "delete",
    "assign",
    "schedule",
    "transfer",
    "approve",
}


class IntentResolver:
    """Resolves intent and detects ambiguity.

    The runtime should decide:
    - Is the intent complete?
    - Are required parameters present?
    - Are multiple interpretations equally plausible?
    - Is clarification required?

    The runtime should never guess.
    """

    def __init__(
        self,
        execution_threshold: float = MUTATING_THRESHOLD,
        readonly_threshold: float = READONLY_THRESHOLD,
    ):
        self.execution_threshold = execution_threshold
        self.readonly_threshold = readonly_threshold

    def resolve(self, question: str, intent_type: str = "") -> IntentResolution:
        """Resolve intent and detect ambiguity.

        Returns IntentResolution with candidates and whether clarification is needed.
        """
        # Generate candidates
        candidates = self._generate_candidates(question, intent_type)

        if not candidates:
            return IntentResolution(
                is_ambiguous=False,
                candidates=[],
                best_candidate=None,
                requires_clarification=False,
            )

        # Sort by confidence
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        # Check for ambiguity
        is_ambiguous = self._is_ambiguous(candidates)

        # Determine if clarification is required
        is_muting = intent_type.lower() in MUTATING_INTENTS
        threshold = self.execution_threshold if is_muting else self.readonly_threshold

        best = candidates[0]
        requires_clarification = is_ambiguous or best.confidence < threshold

        # Generate clarification question if needed
        clarification_question = ""
        if requires_clarification and len(candidates) >= 2:
            clarification_question = self._generate_clarification_question(question, candidates[:3])

        return IntentResolution(
            is_ambiguous=is_ambiguous,
            candidates=candidates,
            best_candidate=best,
            requires_clarification=requires_clarification,
            clarification_question=clarification_question,
        )

    def _generate_candidates(self, question: str, intent_type: str) -> list[IntentCandidate]:
        """Generate candidate interpretations of the question."""
        candidates = []
        q_lower = question.lower()

        # Detect common ambiguity patterns
        # Pattern 1: "for tomm" could be "due tomorrow" or "for Tom"
        if "for tomm" in q_lower or "for tom" in q_lower:
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "create",
                    confidence=0.53,
                    parameters={"due_date": "tomorrow"},
                    description="Task is due tomorrow",
                )
            )
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "create",
                    confidence=0.47,
                    parameters={"assignee": "Tom"},
                    description="Task assigned to Tom",
                )
            )

        # Pattern 2: "at 3" could be "at 3pm" or "at 3 locations"
        elif re.search(r"at \d+$", q_lower):
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "create",
                    confidence=0.6,
                    parameters={"time": "3:00 PM"},
                    description="Scheduled for 3:00 PM",
                )
            )
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "create",
                    confidence=0.4,
                    parameters={"count": 3},
                    description="At 3 locations",
                )
            )

        # Pattern 3: "with John" could be "with John Smith" or "with John Doe"
        elif "with john" in q_lower:
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "update",
                    confidence=0.55,
                    parameters={"person": "John Smith"},
                    description="Collaborate with John Smith",
                )
            )
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "update",
                    confidence=0.45,
                    parameters={"person": "John Doe"},
                    description="Collaborate with John Doe",
                )
            )

        # Pattern 4: "next week" could be "next Mon-Sun" or "starting tomorrow"
        elif "next week" in q_lower:
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "schedule",
                    confidence=0.6,
                    parameters={"start": "next Monday"},
                    description="Starting next Monday",
                )
            )
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "schedule",
                    confidence=0.4,
                    parameters={"start": "tomorrow"},
                    description="Starting tomorrow",
                )
            )

        # No ambiguity detected — single candidate
        else:
            candidates.append(
                IntentCandidate(
                    intent=intent_type or "unknown",
                    confidence=0.9,
                    parameters={"raw": question},
                    description=question,
                )
            )

        return candidates

    def _is_ambiguous(self, candidates: list[IntentCandidate]) -> bool:
        """Check if candidates are ambiguous (similar confidence)."""
        if len(candidates) < 2:
            return False

        # If top two candidates have similar confidence, it's ambiguous
        top_two = candidates[:2]
        confidence_diff = abs(top_two[0].confidence - top_two[1].confidence)
        return confidence_diff < 0.2

    def _generate_clarification_question(
        self,
        question: str,
        candidates: list[IntentCandidate],
    ) -> str:
        """Generate a clarification question based on candidates."""
        if len(candidates) < 2:
            return ""

        options = [c.description for c in candidates[:3]]
        return f"Did you mean: {' or '.join(options)}?"
