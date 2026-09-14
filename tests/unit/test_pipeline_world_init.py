"""_pipeline_world_init() — get_world_tensor() must be the single source of truth
for the pipeline's world layer: no _engine.world fallback, no _gpu_world branch,
one cached WorldView/World pair, no actor worlds created during init.
"""

from __future__ import annotations

import threading

import pytest

from src.monkey_brain.kernel.compile import world_tensor
from src.monkey_brain.kernel.compile.world_tensor import get_world_tensor
from src.monkey_brain.kernel.cognitive_runtime import CognitiveRuntime


@pytest.fixture(autouse=True)
def _fresh_world():
    world_tensor.reset_for_test()
    yield
    world_tensor.reset_for_test()


def _seeded_runtime() -> CognitiveRuntime:
    """A tensor with a couple of observed transitions, wired into a fresh runtime."""
    tensor = get_world_tensor("default")
    tensor.observe("start", "middle", domain="task")
    tensor.observe("middle", "end", domain="task")
    return CognitiveRuntime()


def test_singleton_world_initialization():
    runtime = _seeded_runtime()
    tensor = get_world_tensor("default")

    layer = runtime._pipeline_world_init()

    assert layer.transitions == tensor.nnz()
    assert layer.domains == tensor.domains()
    assert layer.states == tensor.states()
    assert layer.built is True
    assert runtime._world._world is tensor  # WorldView wraps the singleton, no copy


def test_repeated_initialization_returns_cached_layer():
    runtime = _seeded_runtime()

    first = runtime._pipeline_world_init()
    second = runtime._pipeline_world_init()

    assert first is second
    assert runtime._world_layer is first


def test_build_if_needed_invoked_once(monkeypatch):
    runtime = _seeded_runtime()
    tensor = get_world_tensor("default")

    calls = []
    original = tensor._build_if_needed

    def _counting_build():
        calls.append(1)
        return original()

    monkeypatch.setattr(tensor, "_build_if_needed", _counting_build)

    runtime._pipeline_world_init()
    runtime._pipeline_world_init()
    runtime._pipeline_world_init()

    assert len(calls) == 1  # cached after the first real call


def test_world_tensor_build_if_needed_is_idempotent():
    tensor = get_world_tensor("default")
    assert tensor.is_built() is False

    tensor._build_if_needed()
    assert tensor.is_built() is True

    tensor._build_if_needed()  # second call is a no-op, not an error
    assert tensor.is_built() is True


def test_world_layer_metadata_correct():
    tensor = get_world_tensor("default")
    tensor.observe("a", "b", domain="d1")
    tensor.observe("b", "c", domain="d2")
    runtime = CognitiveRuntime()

    layer = runtime._pipeline_world_init()

    assert layer.transitions == tensor.nnz() == 2
    assert set(layer.domains) == {"d1", "d2"}
    assert set(layer.states) == {"a", "b", "c"}


def test_no_dependency_on_engine_world():
    """A poisoned _engine.world must never be touched — get_world_tensor() is the
    only source of truth."""
    runtime = _seeded_runtime()

    class _PoisonedWorld:
        def __getattr__(self, name):
            raise AssertionError(f"_engine.world.{name} accessed — must not be used")

    class _PoisonedEngine:
        @property
        def world(self):
            raise AssertionError("_engine.world accessed — must not be used")

    runtime._engine = _PoisonedEngine()
    runtime._gpu_world = _PoisonedWorld()

    layer = runtime._pipeline_world_init()

    assert layer is not None


def test_actor_worlds_not_created_during_pipeline_init():
    runtime = _seeded_runtime()

    runtime._pipeline_world_init()

    assert runtime._actor_worlds == {}


def test_initialization_is_idempotent_across_many_calls():
    runtime = _seeded_runtime()

    layers = [runtime._pipeline_world_init() for _ in range(10)]

    assert all(layer is layers[0] for layer in layers)


def test_concurrent_initialization():
    runtime = _seeded_runtime()
    results: list = []
    errors: list = []

    def _init():
        try:
            results.append(runtime._pipeline_world_init())
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=_init) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 16
    # Every thread must observe the same cached world layer / metadata.
    first = results[0]
    assert all(r.transitions == first.transitions for r in results)
    assert all(r.domains == first.domains for r in results)
    assert all(r.states == first.states for r in results)
