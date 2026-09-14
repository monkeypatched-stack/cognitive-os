"""_TrackingExpansionPolicy — observes SelfHealingPolicy's retry exhaustion
without modifying it, so ProcessManager can tell "still retrying" apart from
"budget exhausted, needs compensation" for a FAILED node.
"""

from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.execute.graph import ExecutionGraph


class _TrackingExpansionPolicy:
    """Wraps SelfHealingPolicy (or any expansion policy with the same
    on_failure(graph, node) -> dict|None interface) to observe retry
    exhaustion without modifying SelfHealingPolicy itself. GraphScheduler
    calls on_failure() through this wrapper exactly as it would call the
    inner policy directly — the wrapper is transparent to it.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.attempts: dict[str, int] = {}
        self.exhausted: set[str] = set()

    def on_failure(self, graph: ExecutionGraph, failed_node: Any) -> dict[str, Any] | None:
        node_id = failed_node.id
        self.attempts[node_id] = self.attempts.get(node_id, 0) + 1
        expansion = self._inner.on_failure(graph, failed_node)
        if expansion is None:
            self.exhausted.add(node_id)
        else:
            self.exhausted.discard(node_id)
        return expansion
