"""Policy singleton — shared Bellman policy across all executor requests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy

# IMPROVEMENT: module-level singleton is not thread-safe under concurrent first-call;
# protect with threading.Lock or use dependency injection if multi-threaded startup is required.
_policy_singleton: Optional[Any] = None


def get_policy() -> "BellmanPolicy":
    global _policy_singleton
    if _policy_singleton is None:
        from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy

        _policy_singleton = BellmanPolicy()
    return _policy_singleton
