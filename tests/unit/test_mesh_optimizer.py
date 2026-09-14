"""Unit tests for MeshOptimizer.spawn() fix — spawned_for and capabilities populated."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def _make_mesh():
    """Return a minimal AgentMesh mock."""
    mesh = MagicMock()
    mesh.spawn = MagicMock(return_value=None)
    return mesh


def _make_optimizer(registry=None):
    from broca.mesh import MeshOptimizer

    return MeshOptimizer(registry=registry)


@pytest.fixture
def optimizer():
    return _make_optimizer()


@pytest.fixture
def mesh():
    return _make_mesh()


class TestMeshOptimizerSpawn:
    def test_spawn_receives_spawned_for(self, optimizer, mesh):
        """spawn() must be called with a `spawned_for` argument."""
        world_state = {"x": 0.5}

        with (
            patch.object(optimizer, "_estimate_loss", return_value=0.5),
            patch.object(
                optimizer,
                "_find_best_addition",
                side_effect=[("agent_alpha", 0.3), (None, 0.0)],
            ),
        ):
            optimizer.optimize(mesh, world_state, prompt="test")

        assert mesh.spawn.called
        _, kwargs = mesh.spawn.call_args
        assert "spawned_for" in kwargs, "spawned_for missing from spawn() call"
        assert "loss_reduction" in kwargs["spawned_for"]

    def test_spawn_receives_capabilities(self, optimizer, mesh):
        """spawn() must be called with a `capabilities` argument."""
        world_state = {"x": 0.5}

        with (
            patch.object(optimizer, "_estimate_loss", return_value=0.5),
            patch.object(
                optimizer,
                "_find_best_addition",
                side_effect=[("agent_alpha", 0.3), (None, 0.0)],
            ),
        ):
            optimizer.optimize(mesh, world_state, prompt="test")

        _, kwargs = mesh.spawn.call_args
        assert "capabilities" in kwargs, "capabilities missing from spawn() call"
        assert isinstance(kwargs["capabilities"], list)

    def test_spawn_receives_ephemeral(self, optimizer, mesh):
        world_state = {"x": 0.5}

        with (
            patch.object(optimizer, "_estimate_loss", return_value=0.5),
            patch.object(
                optimizer,
                "_find_best_addition",
                side_effect=[("agent_alpha", 0.3), (None, 0.0)],
            ),
        ):
            optimizer.optimize(mesh, world_state, prompt="test")

        _, kwargs = mesh.spawn.call_args
        assert kwargs.get("ephemeral") is True

    def test_optimize_does_not_raise(self, optimizer, mesh):
        world_state = {}

        with (
            patch.object(optimizer, "_estimate_loss", return_value=0.8),
            patch.object(optimizer, "_find_best_addition", side_effect=[(None, 0.0)]),
        ):
            result = optimizer.optimize(mesh, world_state)

        assert "iterations" in result
        assert "agents_added" in result
        assert "loss_initial" in result
        assert "loss_final" in result
        assert "converged" in result

    def test_optimize_with_registry_caps(self, mesh):
        """Registry capabilities flow through to spawn."""
        registry = MagicMock()
        registry.get_capabilities_for = MagicMock(return_value=["cap_x", "cap_y"])
        opt = _make_optimizer(registry=registry)

        with (
            patch.object(opt, "_estimate_loss", return_value=0.5),
            patch.object(
                opt,
                "_find_best_addition",
                side_effect=[("agent_beta", 0.3), (None, 0.0)],
            ),
        ):
            opt.optimize(mesh, {})

        _, kwargs = mesh.spawn.call_args
        assert "cap_x" in kwargs["capabilities"] or "cap_y" in kwargs["capabilities"]

    def test_no_spawn_when_below_convergence(self, optimizer, mesh):
        """No spawn should happen when loss is already below threshold."""
        optimizer.convergence_threshold = 0.90

        with patch.object(optimizer, "_estimate_loss", return_value=0.05):
            result = optimizer.optimize(mesh, {})

        assert not mesh.spawn.called
        assert result["agents_added"] == []
