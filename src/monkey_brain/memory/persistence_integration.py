"""persistence_integration.py — bridge between db.py and the mem0 adapter.

Two public functions expected by services/common/db.py:

    async def snapshot_world_state_to_mem0(state: dict) -> str | None
    async def load_mem0_state(mem0_id: str) -> dict | None

Both go through the existing Reducer → StateMutation path so the event
log and Lemon observability hooks fire identically to all other mutations.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level adapter + reducer — initialised lazily on first call.
_adapter: Any = None
_reducer: Any = None
_manager: Any = None


def _get_adapter():
    global _adapter
    if _adapter is None:
        try:
            from src.monkey_brain.persistence.mem0_adapter import Mem0Adapter

            _adapter = Mem0Adapter()
        except Exception as exc:
            logger.debug(f"Mem0Adapter not available: {exc}")
    return _adapter


def _get_reducer():
    global _reducer
    if _reducer is None:
        try:
            from src.monkey_brain.persistence.reducer import Reducer

            _reducer = Reducer()
        except Exception as exc:
            logger.debug(f"Reducer not available: {exc}")
    return _reducer


def _get_manager():
    """Return an application-level PersistenceManager if one was registered,
    else fall back to a local one backed only by the Mem0Adapter."""
    global _manager
    if _manager is None:
        try:
            from src.monkey_brain.persistence.manager import PersistenceManager

            pm = PersistenceManager()
            adapter = _get_adapter()
            if adapter:
                pm.register_adapter(adapter)
            _manager = pm
        except Exception as exc:
            logger.debug(f"PersistenceManager not available: {exc}")
    return _manager


def register_manager(pm: Any, lemon: Any = None) -> None:
    """Called from main.py lifespan after the global PersistenceManager is wired.

    Replaces the lazy local manager so snapshots use the fully-wired instance
    (with MongoDB, Redis, mem0, and Lemon all registered).
    """
    global _manager
    _manager = pm
    if lemon is not None and hasattr(pm, "set_lemon"):
        pm.set_lemon(lemon)


async def snapshot_world_state_to_mem0(state: dict[str, Any]) -> str | None:
    """Persist a world-state dict to mem0 via the Reducer → StateMutation path.

    Returns the mem0 memory_id string, or None on failure.
    """
    reducer = _get_reducer()
    manager = _get_manager()
    if reducer is None or manager is None:
        return None

    agent_id = state.get("agent_id", "monkeybrain")

    try:
        mutation = reducer.epistemic_state_updated(
            E_t={
                "S": state,
                "B": {},
                "A": [],
                "M": {},
            },
            agent_id=agent_id,
        )

        # Ensure the adapter is connected before first use.
        adapter = _get_adapter()
        if adapter and not adapter.is_connected():
            await adapter.connect()

        result = await manager.apply_mutation(mutation)
        # Pull the first mem0 memory_id from results
        for r in result.get("results", []):
            mid = r.get("memory_id")
            if mid:
                return mid
        return None
    except Exception as exc:
        logger.error(f"snapshot_world_state_to_mem0 failed: {exc}")
        return None


async def load_mem0_state(mem0_id: str) -> dict[str, Any] | None:
    """Retrieve a previously snapshotted world state from mem0 by memory_id.

    Returns {"raw_state": {...}} on success, None on failure / not found.
    """
    adapter = _get_adapter()
    if adapter is None:
        return None

    if not adapter.is_connected():
        try:
            await adapter.connect()
        except Exception:
            return None

    try:
        mem0_obj = adapter._mem0
        if mem0_obj is None:
            return None
        raw = mem0_obj.get(mem0_id)
        if not raw:
            return None
        # mem0 returns either a dict with "memory" or the raw payload string.
        if isinstance(raw, dict):
            memory_str = raw.get("memory") or raw.get("content") or str(raw)
        else:
            memory_str = str(raw)

        import json

        try:
            parsed = json.loads(memory_str)
            raw_state = parsed.get("state", parsed)
        except (json.JSONDecodeError, AttributeError):
            raw_state = {"_raw": memory_str}

        return {"raw_state": raw_state, "mem0_id": mem0_id}
    except Exception as exc:
        logger.error(f"load_mem0_state({mem0_id}) failed: {exc}")
        return None
