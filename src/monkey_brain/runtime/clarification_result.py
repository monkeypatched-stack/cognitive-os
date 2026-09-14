"""ClarificationResult — standard result when clarification is needed.

When ambiguity is detected, the runtime returns a ClarificationResult
instead of executing. No repository operations are executed.
No world state changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.runtime.intent_resolver import IntentCandidate


@dataclass
class ClarificationResult:
    """Standard result when clarification is required.

    The runtime returns this instead of executing when ambiguity is detected.
    No repository operations are executed.
    No world state changes.
    """

    question: str
    candidates: list[IntentCandidate] = field(default_factory=list)
    original_question: str = ""
    intent_type: str = ""
    requires_clarification: bool = True
    success: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "requires_clarification": self.requires_clarification,
            "question": self.question,
            "candidates": [c.to_dict() for c in self.candidates],
            "original_question": self.original_question,
            "intent_type": self.intent_type,
            "metadata": self.metadata,
        }
