"""Separation of Responsibilities — Knowledge, World State, Reasoning.

Clearly separate:
- Knowledge: Documentation, SOPs, Specifications, Architecture, Policies
- World State: Runtime state, User data, Tasks, Work Orders, Sessions
- Reasoning: Planning, Inference, Summarization, Synthesis

Knowledge comes from Knowledge Packs.
World state comes from repositories.
Reasoning comes from the foundation model.

The LLM must never invent world state.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("agentos.responsibilities")


class ResponsibilityType(str, Enum):
    KNOWLEDGE = "knowledge"
    WORLD_STATE = "world_state"
    REASONING = "reasoning"


# ── Knowledge Layer ────────────────────────────────────────────────────────────


@dataclass
class KnowledgeItem:
    """A single piece of knowledge from a Knowledge Pack."""

    id: str
    content: str
    source: str = ""
    modality: str = "document"
    provenance: float = 0.0
    freshness: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "modality": self.modality,
            "provenance": self.provenance,
            "freshness": self.freshness,
        }


class YamlKnowledgeStore:
    """Read-only store for knowledge packs.

    Knowledge is immutable at runtime. It comes from:
    - Documentation
    - SOPs
    - Specifications
    - Architecture docs
    - Policies
    """

    def __init__(self):
        self._packs: dict[str, list[KnowledgeItem]] = {}
        self._load_packs()

    def _load_packs(self) -> None:
        """Load knowledge packs from disk."""
        try:
            import yaml

            kp_dir = Path(__file__).parents[3] / "somatic" / "knowledge_packs"
            if not kp_dir.exists():
                return

            for kp_file in kp_dir.glob("*.yaml"):
                try:
                    with open(kp_file) as f:
                        data = yaml.safe_load(f)
                    pack_name = data.get("pack", {}).get("name", kp_file.stem)
                    items = []
                    for item in data.get("items", []):
                        items.append(
                            KnowledgeItem(
                                id=item.get("id", ""),
                                content=item.get("content", ""),
                                source=pack_name,
                                modality=item.get("modality", "document"),
                                provenance=item.get("provenance", 0.0),
                                freshness=item.get("freshness", 1.0),
                            )
                        )
                    self._packs[pack_name] = items
                except Exception as e:
                    logger.debug("[knowledge] Failed to load %s: %s", kp_file, e)
        except ImportError:
            logger.debug("_load_packs: suppressed exception", exc_info=True)

    def query(self, question: str, limit: int = 5) -> list[KnowledgeItem]:
        """Query knowledge packs for relevant items."""
        results = []
        q_lower = question.lower()

        for pack_name, items in self._packs.items():
            for item in items:
                if any(word in item.content.lower() for word in q_lower.split()):
                    results.append(item)

        results.sort(key=lambda x: x.provenance, reverse=True)
        return results[:limit]

    def get_all(self) -> list[KnowledgeItem]:
        """Get all knowledge items."""
        return [item for items in self._packs.values() for item in items]

    @property
    def pack_names(self) -> list[str]:
        return list(self._packs.keys())


# ── World State Layer ──────────────────────────────────────────────────────────


@dataclass
class WorldStateEntry:
    """A single entry in world state."""

    key: str
    value: Any
    updated_at: float = field(default_factory=time.time)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
            "source": self.source,
        }


class WorldStateStore:
    """Mutable store for world state.

    World state comes from repositories:
    - Runtime state
    - User data
    - Tasks
    - Work Orders
    - Sessions
    - Process state
    """

    def __init__(self, state_dir: Path | None = None):
        self._state_dir = state_dir or Path.home() / ".monkeybrain" / "world_state"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, WorldStateEntry] = {}

    def get(self, key: str, agent: str = "") -> Any:
        """Get a world state value."""
        cache_key = f"{agent}:{key}" if agent else key
        entry = self._cache.get(cache_key)
        if entry:
            return entry.value

        # Try loading from disk
        path = self._state_dir / f"{agent}_{key}.json" if agent else self._state_dir / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                entry = WorldStateEntry(key=key, value=data.get("value"), source=data.get("source", ""))
                self._cache[cache_key] = entry
                return entry.value
            except Exception:
                logger.debug(
                    "World state file %s unreadable/corrupt, treating as absent",
                    path,
                    exc_info=True,
                )
        return None

    def set(self, key: str, value: Any, agent: str = "", source: str = "") -> None:
        """Set a world state value."""
        cache_key = f"{agent}:{key}" if agent else key
        entry = WorldStateEntry(key=key, value=value, source=source)
        self._cache[cache_key] = entry

        # Persist to disk
        path = self._state_dir / f"{agent}_{key}.json" if agent else self._state_dir / f"{key}.json"
        try:
            path.write_text(json.dumps({"value": value, "source": source}, default=str))
        except Exception as e:
            logger.warning("[world_state] Failed to persist %s: %s", key, e)

    def list_keys(self, agent: str = "") -> list[str]:
        """List all world state keys."""
        prefix = f"{agent}:" if agent else ""
        return [k[len(prefix) :] for k in self._cache if k.startswith(prefix)]


# ── Reasoning Layer ────────────────────────────────────────────────────────────


@runtime_checkable
class Reasoner(Protocol):
    """Interface for reasoning engines."""

    async def reason(self, question: str, context: dict[str, Any]) -> str: ...


class LLMReasoner:
    """Reasoning via foundation model.

    Reasoning comes from the foundation model.
    The LLM must never invent world state.
    """

    def __init__(self, llm=None):
        self._llm = llm

    async def reason(self, question: str, context: dict[str, Any]) -> str:
        """Generate reasoning via LLM."""
        if self._llm is None:
            return f"Reasoning not available for: {question}"

        try:
            prompt = self._build_prompt(question, context)
            response = await self._llm.generate(
                prompt,
                system="You are a helpful assistant. Base your reasoning on provided context.",
            )
            return response
        except Exception as e:
            logger.error("[reasoning] LLM reasoning failed: %s", e)
            return f"Reasoning error: {e}"

    def _build_prompt(self, question: str, context: dict[str, Any]) -> str:
        """Build prompt with knowledge and state context."""
        parts = [f"Question: {question}"]

        if context.get("knowledge"):
            parts.append(f"\nKnowledge context:\n{context['knowledge']}")

        if context.get("state"):
            parts.append(f"\nCurrent state:\n{json.dumps(context['state'], indent=2)}")

        return "\n".join(parts)


# ── Responsibility Manager ─────────────────────────────────────────────────────


class ResponsibilityManager:
    """Manages separation of Knowledge, World State, and Reasoning."""

    def __init__(self):
        self.knowledge = YamlKnowledgeStore()
        self.world_state = WorldStateStore()
        self.reasoner = LLMReasoner()

    def resolve(self, question: str) -> dict[str, Any]:
        """Resolve a question using the three layers.

        Order:
        1. Knowledge Pack
        2. World State (repositories)
        3. Reasoning (LLM)
        """
        # 1. Query knowledge
        knowledge_items = self.knowledge.query(question)
        knowledge_context = "\n".join(item.content for item in knowledge_items[:3])

        # 2. Query world state
        state_context = {}
        for key in self.world_state.list_keys():
            value = self.world_state.get(key)
            if value is not None:
                state_context[key] = value

        return {
            "knowledge_items": [item.to_dict() for item in knowledge_items],
            "knowledge_context": knowledge_context,
            "state_context": state_context,
            "sources": list(set(item.source for item in knowledge_items)),
        }

    async def answer(self, question: str) -> dict[str, Any]:
        """Answer a question using all three layers."""
        context = self.resolve(question)

        # Reasoning with context
        reasoning = await self.reasoner.reason(
            question,
            {
                "knowledge": context["knowledge_context"],
                "state": context["state_context"],
            },
        )

        return {
            "answer": reasoning,
            "knowledge_sources": context["sources"],
            "state_keys": list(context["state_context"].keys()),
            "responsibility": "reasoning",
        }
