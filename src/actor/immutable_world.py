"""Read-only WorldView wrapper — enforces world immutability invariant.

Architectural Invariant:
    Global World is immutable from actor cognition.
    Only Context Stream may modify the world.
    Actors may only observe the world (read-only).
"""


class WorldView:
    """Immutable view of the world graph.

    Blocks all mutation methods; only read-only operations allowed.
    """

    def __init__(self, world):
        """Initialize with wrapped world object (private)."""
        object.__setattr__(self, "_world", world)

    def states(self):
        """Read-only: Get all states in world."""
        return self._world.states()

    def successors(self, state):
        """Read-only: Get successor states."""
        return self._world.successors(state)

    def domain_of(self, state):
        """Read-only: Get domain of state."""
        return self._world.domain_of(state)

    def domains(self):
        """Read-only: Get all domains."""
        return self._world.domains()

    def nnz(self):
        """Read-only: Get number of non-zero transitions."""
        return self._world.nnz()

    def feature(self, src, dst, feature_type):
        """Read-only: Get feature for transition."""
        return self._world.feature(src, dst, feature_type)

    def __iter__(self):
        """Read-only: Iterate over (src, dst) pairs."""
        return iter(self._world)

    def clone(self):
        """Create a mutable clone for prediction.

        This is the ONLY way to get a mutable copy.
        Clones are used for execution predictions only.

        CRITICAL: Cloning is non-negotiable. Raises if world doesn't support it.
        Predictions MUST use cloned worlds to prevent corruption of the shared global state.
        """
        if not hasattr(self._world, "clone"):
            raise NotImplementedError(
                f"World {type(self._world).__name__} does not support cloning. "
                f"Predictions require a mutable clone to prevent global world corruption. "
                f"Implement clone() method on the world object."
            )

        cloned = self._world.clone()
        if cloned is self._world:
            raise RuntimeError(
                f"clone() must return a NEW instance, not self. Got same reference; this violates prediction isolation."
            )

        return cloned

    # Read-only methods that WorldView allows
    _READ_ONLY_METHODS = frozenset(
        {
            "states",
            "successors",
            "domain_of",
            "domains",
            "nnz",
            "feature",
            "__iter__",
            "clone",
            "has_edge",
            "__contains__",
            "domains",
            "n_states",
            "index_state",
        }
    )

    # Mutation methods that must never be callable through WorldView
    _BLOCKED_METHODS = frozenset(
        {
            "observe",
            "intern",
            "_add_entity",
            "_drop_cell",
            "batch_update",
            "bellman_update",
            "_new_cell",
            "save",
            "load",
            "compact",
            "evict",
            "mark_dirty",
            "clear_dirty",
        }
    )

    def __getattr__(self, name):
        """Forward only known read-only attributes; block mutations."""
        if name in self._BLOCKED_METHODS:
            raise AttributeError(
                f"WorldView is immutable; cannot call '{name}'. "
                f"Use context_stream to update world, not direct mutation."
            )
        if name in self._READ_ONLY_METHODS:
            return getattr(self._world, name)
        # Allow private/dunder access for Python internals
        if name.startswith("_"):
            raise AttributeError(f"WorldView has no attribute '{name}'")
        # For other attributes, delegate but warn
        return getattr(self._world, name)

    def __setattr__(self, name, value):
        """Block all mutations."""
        if name == "_world":
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(
                f"WorldView is immutable; cannot set '{name}'. Use context_stream to update world, not direct mutation."
            )

    def __delattr__(self, name):
        """Block all deletions."""
        raise AttributeError(f"WorldView is immutable; cannot delete '{name}'.")
