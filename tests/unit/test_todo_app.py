"""Simple Todo App — demonstrates actor-to-actor communication in a world.

Two actors collaborate on a shared todo list:
- User: creates and assigns todos
- Assistant: completes todos

They share a world of todo transitions and learn from each other's actions.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork
from src.monkey_brain.kernel.policy.store import PolicyStore

# ═══════════════════════════════════════════════════════════════════════════
# The World: Todo Transitions
# ═══════════════════════════════════════════════════════════════════════════


def create_todo_world() -> SparseTransitionTensor:
    """Create a world with todo lifecycle transitions."""
    world = SparseTransitionTensor()

    # Todo lifecycle
    world.observe("todo_created", "todo_assigned", domain="todos")
    world.observe("todo_assigned", "todo_in_progress", domain="todos")
    world.observe("todo_in_progress", "todo_completed", domain="todos")
    world.observe("todo_completed", "todo_archived", domain="todos")

    # Block/unblock
    world.observe("todo_in_progress", "todo_blocked", domain="todos")
    world.observe("todo_blocked", "todo_in_progress", domain="todos")

    # Priority
    world.observe("todo_created", "todo_urgent", domain="todos")
    world.observe("todo_urgent", "todo_in_progress", domain="todos")

    return world


# ═══════════════════════════════════════════════════════════════════════════
# Actors: User and Assistant
# ═══════════════════════════════════════════════════════════════════════════


def create_todo_actors(world: SparseTransitionTensor) -> ActorNetwork:
    """Create a user and assistant that collaborate on todos."""
    from src.monkey_brain.kernel.compile.trust import Relationship

    network = ActorNetwork(world)

    user = network.add("user")
    assistant = network.add("assistant")
    # Connect with COMMUNITY relationship (includes PUBLISH_OBSERVATIONS + SUBSCRIBE_OBSERVATIONS)
    network.connect("user", "assistant", Relationship.COMMUNITY)

    return network


# ═══════════════════════════════════════════════════════════════════════════
# Test: Basic Todo Flow
# ═══════════════════════════════════════════════════════════════════════════


class TestTodoApp:
    """Simple todo app with actor-to-actor communication."""

    def test_user_creates_todo(self):
        """User creates a todo and discovers the lifecycle."""
        world = create_todo_world()
        network = create_todo_actors(world)

        # User creates a todo
        deltas = network.ask("user", "todo_created")

        # User should discover: created → assigned, created → urgent
        user = network.actors["user"]
        assert user.world.nnz() >= 2

    def test_assistant_discovers_through_gossip(self):
        """Assistant learns about todo lifecycle through gossip."""
        world = create_todo_world()
        network = create_todo_actors(world)

        # User asks about todo creation
        network.ask("user", "todo_created")

        # Assistant should receive gossip
        assistant = network.actors["assistant"]
        assert assistant.world.nnz() >= 1

    def test_user_and_assistant_have_same_knowledge(self):
        """After both ask, they have the same view of the world."""
        world = create_todo_world()
        network = create_todo_actors(world)

        network.ask("user", "todo_created")
        network.ask("assistant", "todo_created")

        user = network.actors["user"]
        assistant = network.actors["assistant"]

        # Same number of transitions known
        assert user.world.nnz() == assistant.world.nnz()

    def test_user_prefers_assigning(self):
        """User learns that assigning todos is effective."""
        world = create_todo_world()
        network = create_todo_actors(world)

        network.ask("user", "todo_created")

        # User assigns todo
        user = network.actors["user"]
        user.policy.update("todo_created", "todo_assigned", reward=0.9)

        assert user.policy.value("todo_created", "todo_assigned") > 0.5

    def test_assistant_prefers_completing(self):
        """Assistant learns that completing todos is effective."""
        world = create_todo_world()
        network = create_todo_actors(world)

        network.ask("assistant", "todo_in_progress")

        # Assistant completes todo
        assistant = network.actors["assistant"]
        assistant.policy.update("todo_in_progress", "todo_completed", reward=0.9)

        assert assistant.policy.value("todo_in_progress", "todo_completed") > 0.5

    def test_policies_are_isolated(self):
        """User and assistant have different policies."""
        world = create_todo_world()
        network = create_todo_actors(world)

        user = network.actors["user"]
        assistant = network.actors["assistant"]

        # User learns assigning
        user.policy.update("todo_created", "todo_assigned", reward=0.9)

        # Assistant learns completing
        assistant.policy.update("todo_in_progress", "todo_completed", reward=0.9)

        # Different policies
        assert user.policy.value("todo_created", "todo_assigned") > 0.5
        assert assistant.policy.value("todo_created", "todo_assigned") == 0.5

        assert assistant.policy.value("todo_in_progress", "todo_completed") > 0.5
        assert user.policy.value("todo_in_progress", "todo_completed") == 0.5

    def test_full_todo_lifecycle(self):
        """Complete lifecycle: create → assign → complete → archive."""
        world = create_todo_world()
        network = create_todo_actors(world)

        # User creates todo
        network.ask("user", "todo_created")

        # Assistant completes todo
        network.ask("assistant", "todo_completed")

        # Both have lifecycle knowledge
        user = network.actors["user"]
        assistant = network.actors["assistant"]

        assert user.world.nnz() >= 2
        assert assistant.world.nnz() >= 2

        # User learns: creating todos leads to work
        user.policy.update("todo_created", "todo_assigned", reward=0.8)

        # Assistant learns: completing todos is rewarding
        assistant.policy.update("todo_in_progress", "todo_completed", reward=0.9)

        # Both adapted to their roles
        assert user.policy.value("todo_created", "todo_assigned") > 0.5
        assert assistant.policy.value("todo_in_progress", "todo_completed") > 0.5


# ═══════════════════════════════════════════════════════════════════════════
# Test: Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestTodoEdgeCases:
    """Edge cases in todo actor communication."""

    def test_ask_unknown_state(self):
        """Asking about a state with no transitions returns nothing."""
        world = create_todo_world()
        network = create_todo_actors(world)

        deltas = network.ask("user", "nonexistent_state")
        assert len(deltas) == 0

    def test_multiple_asks_same_state(self):
        """Asking about the same state twice doesn't duplicate knowledge."""
        world = create_todo_world()
        network = create_todo_actors(world)

        network.ask("user", "todo_created")
        nnz_after_first = network.actors["user"].world.nnz()

        network.ask("user", "todo_created")
        nnz_after_second = network.actors["user"].world.nnz()

        # No duplication
        assert nnz_after_second == nnz_after_first

    def test_network_summary(self):
        """Summary shows current network state."""
        world = create_todo_world()
        network = create_todo_actors(world)

        summary = network.summary()
        assert summary["actors"] == 2
        assert summary["global_transitions"] == 8  # 8 todo transitions
        assert summary["links"] == 1  # user ↔ assistant
