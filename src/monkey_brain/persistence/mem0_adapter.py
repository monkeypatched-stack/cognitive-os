"""Mem0Adapter — IStoreAdapter implementation backed by mem0ai.

Routes:
  MEMORY_*    events → mem0 (upsert)
  EPISTEMIC_* events → mem0 (upsert, keyed by agent_id + entity_type)

Delete semantics:
  KNOWLEDGE_ITEM_RETIRED + MEMORY_FORGOTTEN → mem0 delete by memory_id stored in data
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from src.monkey_brain.persistence.adapters import IStoreAdapter
from src.monkey_brain.persistence.events import EventType
from src.monkey_brain.kernel.resource_manager import ResourceConfig

logger = logging.getLogger(__name__)

# mem0's client (embedding calls to Ollama, vector ops against Qdrant) is
# synchronous with no built-in timeout. Every call is offloaded to a thread
# and bounded here so a hung/unreachable Ollama can't block the event loop
# for every other concurrent request — see mongodb_adapter/redis_adapter,
# which bound their own drivers for the same reason (client_options.py).
_MEM0_CALL_TIMEOUT_SEC = float(os.environ.get("MEM0_CALL_TIMEOUT_SEC", "15"))


def _default_config() -> dict[str, Any]:
    """Default mem0 config: in-memory Qdrant + Ollama for embeddings/LLM."""
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    embed_model = os.environ.get("MEM0_EMBED_MODEL", "nomic-embed-text:latest")
    llm_model = os.environ.get("MEM0_LLM_MODEL", "gemma3:latest")

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "monkeybrain_memory",
                "path": os.environ.get("MEM0_QDRANT_PATH", "/tmp/monkeybrain-qdrant"),
                "embedding_model_dims": 768,
            },
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": llm_model,
                "temperature": 0,
                "max_tokens": 2000,
                "ollama_base_url": ollama_url,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": embed_model,
                "ollama_base_url": ollama_url,
            },
        },
    }


_EPISTEMIC_EVENT_TYPES = {
    EventType.EPISTEMIC_STATE_UPDATED,
    EventType.BELIEF_STATE_UPDATED,
    EventType.KNOWLEDGE_ITEM_EVOLVED,
    EventType.KNOWLEDGE_ITEM_RETIRED,
    EventType.GOAL_STATE_UPDATED,
    EventType.MESH_STATE_UPDATED,
    EventType.SIMULATION_LOSS_RECORDED,
}

_DELETE_EVENT_TYPES = {
    EventType.KNOWLEDGE_ITEM_RETIRED,
    EventType.MEMORY_FORGOTTEN,
}


class Mem0Adapter(IStoreAdapter):
    """Persist epistemic and memory events to mem0ai Memory store.

    mem0 uses agent_id as the semantic partition key — all memories
    for a given agent are retrievable by agent_id query.

    add() is always called with infer=False to avoid mem0 running
    LLM inference on raw state dicts.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or _default_config()
        self._mem0: Any = None

    @property
    def name(self) -> str:
        return "mem0"

    async def connect(self) -> None:
        try:
            from mem0 import Memory

            init = (lambda: Memory.from_config(self._config)) if self._config else Memory
            self._mem0 = await asyncio.wait_for(asyncio.to_thread(init), timeout=_MEM0_CALL_TIMEOUT_SEC)
            logger.info("mem0 adapter connected")
        except ImportError:
            logger.warning("mem0ai not installed — adapter is a no-op")
        except Exception as exc:
            logger.warning(f"mem0 connect failed: {exc}")

    async def disconnect(self) -> None:
        self._mem0 = None

    async def health(self) -> dict[str, Any]:
        if self._mem0 is None:
            return {"status": "disconnected"}
        return {"status": "connected"}

    def is_connected(self) -> bool:
        return self._mem0 is not None

    async def persist(self, event: Any) -> dict[str, Any]:
        if self._mem0 is None:
            return {"status": "no_mem0", "event_id": getattr(event, "event_id", "?")}

        event_type = event.event_type
        data = event.data or {}
        agent_id = data.get("agent_id") or event.entity_id or "monkeybrain"
        entity_id = event.entity_id

        # ── Delete path ────────────────────────────────────────────────────────
        if event_type in _DELETE_EVENT_TYPES:
            memory_id = data.get("mem0_memory_id") or data.get("item_id")
            if memory_id:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self._mem0.delete, memory_id),
                        timeout=_MEM0_CALL_TIMEOUT_SEC,
                    )
                    return {"status": "deleted", "memory_id": memory_id}
                except Exception as exc:
                    return {"status": "delete_error", "error": str(exc)}
            return {"status": "no_memory_id"}

        # ── Upsert path ─────────────────────────────────────────────────────────
        payload = json.dumps(
            {"event_type": str(event_type), "entity": entity_id, **data},
            default=str,
        )
        messages = [{"role": "system", "content": payload}]
        metadata = {
            "event_type": str(event_type),
            "entity_type": event.entity_type,
            "entity_id": entity_id,
            "event_id": event.event_id,
            "source": event.source or "monkeybrain",
        }

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._mem0.add,
                    messages,
                    user_id=agent_id,
                    metadata=metadata,
                    infer=False,
                ),
                timeout=_MEM0_CALL_TIMEOUT_SEC,
            )
            memory_id = None
            if isinstance(result, dict):
                memory_id = result.get("id") or result.get("memory_id")
            elif isinstance(result, list) and result:
                memory_id = result[0].get("id") if isinstance(result[0], dict) else None
            return {"status": "persisted", "memory_id": memory_id, "agent_id": agent_id}
        except Exception as exc:
            logger.error(f"mem0 add failed: {exc}")
            return {"status": "error", "error": str(exc)}

    async def query(
        self,
        entity_type: str,
        entity_id: str | None = None,
    ) -> Any:
        if self._mem0 is None:
            return []
        agent_id = entity_id or "monkeybrain"
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._mem0.search,
                    query=entity_type,
                    filters={"user_id": agent_id},
                    limit=50,
                ),
                timeout=_MEM0_CALL_TIMEOUT_SEC,
            )
            if isinstance(result, dict):
                return result.get("results", [])
            if isinstance(result, list):
                return result
            return []
        except Exception as exc:
            logger.error(f"mem0 query failed: {exc}")
            return []

    async def get_all(self, agent_id: str) -> list[dict]:
        """Return all memories for an agent — used by persistence_integration."""
        if self._mem0 is None:
            return []
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._mem0.get_all, filters={"user_id": agent_id}),
                timeout=_MEM0_CALL_TIMEOUT_SEC,
            )
            if isinstance(result, dict):
                return result.get("results", [])
            if isinstance(result, list):
                return result
            return []
        except Exception as exc:
            logger.error(f"mem0 get_all failed: {exc}")
            return []


class Mem0Resource:
    """ResourceManager-compatible wrapper for Mem0Adapter.

    Exposes the Resource protocol so mem0 participates in the kernel's
    health tracking, retry, and graceful degradation.
    """

    def __init__(self, adapter: Mem0Adapter | None = None):
        self._adapter = adapter or Mem0Adapter()
        self._config = ResourceConfig(
            name="mem0",
            required=False,
            enabled=True,
            max_retries=2,
            initial_backoff_sec=2.0,
            max_backoff_sec=30.0,
            timeout_sec=15.0,
        )

    @property
    def name(self) -> str:
        return "mem0"

    @property
    def config(self) -> ResourceConfig:
        return self._config

    @property
    def adapter(self) -> Mem0Adapter:
        return self._adapter

    async def initialize(self):
        from src.monkey_brain.kernel.resource_manager import (
            ResourceHealth,
            ResourceState,
            ErrorCategory,
        )

        try:
            await self._adapter.connect()
            if self._adapter.is_connected():
                return ResourceHealth(name="mem0", state=ResourceState.READY)
            return ResourceHealth(
                name="mem0",
                state=ResourceState.UNAVAILABLE,
                reason="mem0ai not installed or connect failed",
                category=ErrorCategory.DEPENDENCY_MISSING,
            )
        except ImportError:
            return ResourceHealth(
                name="mem0",
                state=ResourceState.DISABLED,
                reason="mem0ai not installed",
                category=ErrorCategory.DEPENDENCY_MISSING,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "auth" in msg or "credential" in msg or "401" in msg:
                category = ErrorCategory.AUTHENTICATION
            elif "connect" in msg or "timeout" in msg:
                category = ErrorCategory.NETWORK
            else:
                category = ErrorCategory.INTERNAL
            return ResourceHealth(
                name="mem0",
                state=ResourceState.UNAVAILABLE,
                reason=str(exc)[:200],
                category=category,
            )

    async def health(self):
        from src.monkey_brain.kernel.resource_manager import (
            ResourceHealth,
            ResourceState,
            ErrorCategory,
        )

        if self._adapter.is_connected():
            return ResourceHealth(name="mem0", state=ResourceState.READY)
        return ResourceHealth(
            name="mem0",
            state=ResourceState.UNAVAILABLE,
            reason="not connected",
            category=ErrorCategory.OPTIONAL_MISSING,
        )

    async def shutdown(self) -> None:
        await self._adapter.disconnect()
