"""Generic, test-only world-mutation trigger registry.

A qualification test for "the world changed mid-plan, did the system
correctly detect staleness and replan rather than execute blindly" needs a
way to mutate the live KnowledgeGraph at a precise, deterministic point
*inside* a single cognitive tick — after one specific action has executed,
before the next one runs. Nothing in the production pipeline exposes a
pause/resume point for this (a tick is one synchronous stage loop, see
kernel/pipeline/cognitive_policy.py::run_stages), so this module is the
mechanism: a test registers a (predicate, mutation) pair for an actor_id
before sending the request; ActionExecutor (kernel/pipeline/
action_executor.py) checks the registry once after each action outcome and,
on a match, applies the mutation directly against the SAME live
KnowledgeGraph production code reads — a real world change, not a faked
result.

Same idiom as ActionExecutor's existing `failure_rate` constructor knob:
an inert-by-default, opt-in-only mechanism nothing in production ever
triggers unless a test explicitly registers something. In-memory only,
never persisted, never shared across processes — this is test scaffolding,
not a production feature.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.monkey_brain.kernel.pipeline.execution import Action

Predicate = Callable[["Action"], bool]
Mutation = Callable[[Any], None]  # Any == KnowledgeGraph; avoids an import cycle

_lock = threading.Lock()
_registry: dict[str, list[tuple[Predicate, Mutation]]] = {}


def register_mutation(actor_id: str, trigger: Predicate, mutate: Mutation) -> None:
    """Queue a one-shot world mutation for actor_id: the first action this
    actor executes for which trigger(action) is True gets mutate(kg) applied
    immediately afterward, then this registration is consumed (popped) —
    it never fires twice."""
    with _lock:
        _registry.setdefault(actor_id, []).append((trigger, mutate))


def consume_mutation(actor_id: str, action: "Action") -> Mutation | None:
    """Called by ActionExecutor after every action outcome. Returns the
    matching mutation (and removes it from the registry) if one of
    actor_id's pending triggers matches this action, else None. A plain
    dict lookup for any actor_id with no registrations — the overwhelming
    common case — so this is effectively free for real, non-test traffic."""
    with _lock:
        pending = _registry.get(actor_id)
        if not pending:
            return None
        for i, (trigger, mutate) in enumerate(pending):
            try:
                matched = trigger(action)
            except Exception:
                matched = False
            if matched:
                pending.pop(i)
                if not pending:
                    _registry.pop(actor_id, None)
                return mutate
    return None


def clear_mutations(actor_id: str | None = None) -> None:
    """Test teardown: drop all pending registrations for one actor, or
    every actor when actor_id is None."""
    with _lock:
        if actor_id is None:
            _registry.clear()
        else:
            _registry.pop(actor_id, None)
