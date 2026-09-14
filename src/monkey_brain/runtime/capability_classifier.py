"""Capability Classifier — classifies user intent into canonical capabilities.

The classifier determines:
- primary capability
- confidence
- alternative candidate capabilities
- supporting evidence

It never returns agent names, provider names, or implementation details.

Canonical capabilities are implementation-independent:
- task.create, task.update, task.delete, task.list
- blog.write, blog.edit
- document.summarize, document.translate
- email.send
- calendar.schedule
- travel.hotel_book, travel.hotel_search
- robot.move, robot.inspect
- knowledge.search
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.capability_classifier")


@dataclass
class CapabilityClassification:
    """Result of capability classification."""

    capability: str
    confidence: float
    candidates: list[str] = field(default_factory=list)
    reasoning: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "confidence": self.confidence,
            "candidates": self.candidates,
            "reasoning": self.reasoning,
        }


# ── Canonical Capability Patterns ─────────────────────────────────────────────

CAPABILITY_PATTERNS: list[tuple[str, float, list[str], list[str]]] = [
    # task.* capabilities
    (
        r"\b(add|create|new|make)\b.*\b(task|todo|item|reminder)\b",
        0.95,
        ["task.create"],
        ["task", "create"],
    ),
    (
        r"\b(task|todo|item|reminder)\b.*\b(add|create|new|make)\b",
        0.95,
        ["task.create"],
        ["task", "create"],
    ),
    (
        r"\b(update|edit|modify|change)\b.*\b(task|todo|item)\b",
        0.90,
        ["task.update"],
        ["task", "update"],
    ),
    (
        r"\b(task|todo|item)\b.*\b(update|edit|modify|change)\b",
        0.90,
        ["task.update"],
        ["task", "update"],
    ),
    (
        r"\b(delete|remove|drop|clear)\b.*\b(task|todo|item)\b",
        0.90,
        ["task.delete"],
        ["task", "delete"],
    ),
    (
        r"\b(task|todo|item)\b.*\b(delete|remove|drop|clear)\b",
        0.90,
        ["task.delete"],
        ["task", "delete"],
    ),
    (
        r"\b(show|list|get|find|search|what)\b.*\b(task|todo|item|reminder)s?\b",
        0.85,
        ["task.list"],
        ["task", "list"],
    ),
    (
        r"\b(task|todo|item|reminder)s?\b.*\b(show|list|get|find|status)\b",
        0.85,
        ["task.list"],
        ["task", "list"],
    ),
    # blog.* capabilities
    (
        r"\b(write|draft|create|compose)\b.*\b(blog|post|article|essay)\b",
        0.95,
        ["blog.write"],
        ["blog", "write"],
    ),
    (
        r"\b(blog|post|article|essay)\b.*\b(write|draft|create|compose)\b",
        0.95,
        ["blog.write"],
        ["blog", "write"],
    ),
    (
        r"\b(edit|revise|update|modify)\b.*\b(blog|post|article)\b",
        0.90,
        ["blog.edit"],
        ["blog", "edit"],
    ),
    (
        r"\b(blog|post|article)\b.*\b(edit|revise|update|modify)\b",
        0.90,
        ["blog.edit"],
        ["blog", "edit"],
    ),
    # document.* capabilities
    (
        r"\b(summarize|summary|summarise)\b.*\b(document|file|text|report)\b",
        0.90,
        ["document.summarize"],
        ["document", "summarize"],
    ),
    (
        r"\b(document|file|text|report)\b.*\b(summarize|summary|summarise)\b",
        0.90,
        ["document.summarize"],
        ["document", "summarize"],
    ),
    (
        r"\b(translate|translation)\b.*\b(document|file|text)\b",
        0.90,
        ["document.translate"],
        ["document", "translate"],
    ),
    # email.* capabilities
    (
        r"\b(send|email|mail)\b.*\b(email|mail|message)\b",
        0.90,
        ["email.send"],
        ["email", "send"],
    ),
    (r"\b(email|mail|message)\b.*\b(send)\b", 0.90, ["email.send"], ["email", "send"]),
    # calendar.* capabilities
    (
        r"\b(schedule|calendar|appointment|meeting)\b",
        0.85,
        ["calendar.schedule"],
        ["calendar", "schedule"],
    ),
    # travel.* capabilities
    (
        r"\b(book|reserve|reservation|make)\b.*\b(hotel|room|lodging|accommodation|stay)\b",
        0.92,
        ["travel.hotel_book"],
        ["travel", "hotel", "book"],
    ),
    (
        r"\b(hotel|room|lodging|accommodation|stay)\b.*\b(book|reserve|reservation)\b",
        0.92,
        ["travel.hotel_book"],
        ["travel", "hotel", "book"],
    ),
    (
        r"\b(find|search|look\s+for)\b.*\b(hotel|room|lodging|accommodation|stay)\b",
        0.88,
        ["travel.hotel_search"],
        ["travel", "hotel", "search"],
    ),
    (
        r"\b(hotel|room|lodging|accommodation|stay)\b.*\b(find|search|availability|available)\b",
        0.88,
        ["travel.hotel_search"],
        ["travel", "hotel", "search"],
    ),
    # robot.* capabilities
    (
        r"\b(move|navigate|go|drive)\b.*\b(robot|drone|vehicle|bot)\b",
        0.85,
        ["robot.move"],
        ["robot", "move"],
    ),
    (
        r"\b(robot|drone|vehicle|bot)\b.*\b(move|navigate|go|drive)\b",
        0.85,
        ["robot.move"],
        ["robot", "move"],
    ),
    (
        r"\b(inspect|scan|check|examine)\b.*\b(robot|drone|vehicle|bot)\b",
        0.85,
        ["robot.inspect"],
        ["robot", "inspect"],
    ),
    # knowledge.* capabilities
    (
        r"\b(search|find|lookup|query)\b.*\b(knowledge|info|information|data)\b",
        0.85,
        ["knowledge.search"],
        ["knowledge", "search"],
    ),
    (
        r"\b(knowledge|info|information|data)\b.*\b(search|find|lookup|query)\b",
        0.85,
        ["knowledge.search"],
        ["knowledge", "search"],
    ),
]


class CapabilityClassifier:
    """Classifies user intent into canonical capabilities.

    The classifier determines:
    - primary capability
    - confidence
    - alternative candidate capabilities
    - supporting evidence

    It never returns agent names, provider names, or implementation details.
    """

    def classify(self, question: str) -> CapabilityClassification:
        """Classify a question into canonical capabilities."""
        q_lower = question.lower()

        matches: list[tuple[str, float, list[str], list[str]]] = []

        for pattern, confidence, candidates, entities in CAPABILITY_PATTERNS:
            if re.search(pattern, q_lower):
                matches.append((candidates[0], confidence, candidates, entities))

        if not matches:
            # No pattern matched — use generic classification
            return CapabilityClassification(
                capability="unknown",
                confidence=0.3,
                candidates=[],
                reasoning={"matched_entities": [], "pattern": "none"},
            )

        # Sort by confidence
        matches.sort(key=lambda x: x[1], reverse=True)

        best = matches[0]
        all_candidates = []
        for m in matches:
            all_candidates.extend(m[2])
        all_candidates = list(dict.fromkeys(all_candidates))  # dedupe preserving order

        # Extract matched entities
        all_entities = []
        for m in matches:
            all_entities.extend(m[3])
        all_entities = list(dict.fromkeys(all_entities))

        return CapabilityClassification(
            capability=best[0],
            confidence=best[1],
            candidates=all_candidates[:5],
            reasoning={"matched_entities": all_entities, "pattern": "regex"},
        )
