"""StateReducer — pure-function interface and registry for the state workload.

A reducer is a deterministic, side-effect-free function:
    (current_state: dict, payload: dict) → next_state: dict

The ReducerRegistry maps action_type strings to implementations and owns
the StateNode construction so reducers stay free of node-management concerns.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.monkey_brain.kernel.execute.state.node import StateNode


class UnknownActionError(KeyError):
    """Raised when dispatch receives an action_type with no registered reducer."""


class StateReducer(Protocol):
    """Pure reducer contract.

    Implementations must be deterministic and produce no side effects.
    The returned dict becomes the immutable state payload of the next StateNode.
    """

    action_type: str

    def reduce(
        self,
        current_state: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


class ReducerRegistry:
    """Maps action_type strings to StateReducer implementations.

    Registration order does not affect dispatch; lookup is O(1).
    The registry is the single authority over which actions are legal —
    dispatching an unregistered action_type raises UnknownActionError.
    """

    def __init__(self) -> None:
        self._reducers: dict[str, StateReducer] = {}

    def register(self, reducer: StateReducer) -> None:
        self._reducers[reducer.action_type] = reducer

    def dispatch(
        self,
        current: StateNode | None,
        action_type: str,
        payload: dict[str, Any],
        ttl: float = 300.0,
    ) -> StateNode:
        """Run the reducer for action_type and wrap the result in a new StateNode.

        The new node's parent_id is wired to current.node_id automatically,
        forming the immutable provenance chain.
        """
        reducer = self._reducers.get(action_type)
        if not reducer:
            raise UnknownActionError(
                f"No reducer registered for action_type={action_type!r}. Registered types: {sorted(self._reducers)}"
            )
        current_state = current.state if current else {}
        next_state = reducer.reduce(current_state, payload)
        return StateNode(
            parent_id=current.node_id if current else None,
            action_type=action_type,
            state=next_state,
            ttl=ttl,
        )

    def registered_types(self) -> list[str]:
        return sorted(self._reducers)
