"""Generic, test-only deterministic fault-injection registry.

Sibling to mutation_hooks.py, same in-memory/one-shot/actor_id-keyed shape
— deliberately a separate module rather than merged in, since a mutation
changes the *world* (KnowledgeGraph) while a forced failure changes an
*action's own outcome*: different data, different call site
(ActionExecutor._execute_action, before the capability_bus is ever
invoked, vs. ActionExecutor.execute's post-outcome loop).

ActionExecutor already has one fault-injection lever — `failure_rate`
(constructor param, random-chance) — but it is a flat probability on a
SINGLE, GLOBALLY SHARED ActionExecutor instance (every actor/society gets
the same one, see PlanetaryRuntime.__init__ / _attach_society), so it can
neither target one specific action nor stay scoped to one test. This
registry is keyed by data (actor_id + a predicate over the Action) instead
of by which executor instance runs it — same fix shape Phase 1's
mutation_hooks.py already established for the identical problem.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.monkey_brain.kernel.pipeline.execution import Action

Predicate = Callable[["Action"], bool]

_lock = threading.Lock()
_registry: dict[str, list[tuple[Predicate, str, bool]]] = {}


def register_forced_failure(actor_id: str, trigger: Predicate, error: str, recoverable: bool = False) -> None:
    """Queue a one-shot forced failure for actor_id: the first action this
    actor attempts for which trigger(action) is True is never actually
    dispatched to the capability bus — it gets a real ActionOutcome(success=
    False, error=error) instead, exactly as if the real capability had
    failed. Consumed (popped) on match; never fires twice.

    recoverable (Qualification Gap Closure, Phase 4): opt-in, defaults to
    False so every pre-existing forced failure (FAULT-001/002's own honest,
    no-recovery-attempted assertions) is completely unaffected. When True,
    the forced failure carries the SAME generic "recoverable" signal a real
    capability's own result can carry (see action_executor.py::execute's
    one-shot recovery hook) — the forced failure genuinely models "this
    provider failed" rather than "this action is fundamentally broken."
    """
    with _lock:
        _registry.setdefault(actor_id, []).append((trigger, error, recoverable))


def consume_forced_failure(actor_id: str, action: "Action") -> tuple[str, bool] | None:
    """Called by ActionExecutor._execute_action before invoking the real
    capability. Returns (error_message, recoverable) (and removes the
    registration) if one of actor_id's pending triggers matches this
    action, else None. A plain dict lookup for any actor_id with no
    registrations — free for real, non-test traffic."""
    with _lock:
        pending = _registry.get(actor_id)
        if not pending:
            return None
        for i, (trigger, error, recoverable) in enumerate(pending):
            try:
                matched = trigger(action)
            except Exception:
                matched = False
            if matched:
                pending.pop(i)
                if not pending:
                    _registry.pop(actor_id, None)
                return error, recoverable
    return None


def clear_forced_failures(actor_id: str | None = None) -> None:
    """Test teardown: drop all pending registrations for one actor, or
    every actor when actor_id is None."""
    with _lock:
        if actor_id is None:
            _registry.clear()
        else:
            _registry.pop(actor_id, None)
