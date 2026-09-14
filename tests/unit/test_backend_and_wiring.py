"""Verification for the math backend and the runtime wiring (ADR 0021).

Backend: the numpy (CPU-vectorized) path must produce results IDENTICAL to the pure
Python fallback — that equivalence is what makes the cupy (GPU) drop-in trustworthy,
since cupy mirrors the numpy API. GPU itself can't be exercised without a GPU host.

Wiring: observe_execution folds a completed execution into the world tensor, but only
when MB_WORLD_TENSOR is enabled (inert by default).
"""

from __future__ import annotations

import os

import pytest

from src.monkey_brain.kernel.compile import backend
from src.monkey_brain.kernel.compile import world_tensor


def _force(name: str) -> None:
    backend._xp = None
    backend._name = None
    os.environ["MB_MATH_BACKEND"] = name
    backend._detect()


@pytest.fixture(autouse=True)
def _restore_backend():
    yield
    backend._xp = None
    backend._name = None
    os.environ.pop("MB_MATH_BACKEND", None)


# ── backend equivalence (verifies the vectorized path) ───────────────────────────


def test_numpy_spmv_matches_pure_python():
    # diamond: A→B(.5) A→C(.5) B→D C→D ; propagate a unit source from A
    indptr = [0, 2, 3, 4, 4]
    indices = [1, 2, 3, 3]
    data = [0.5, 0.5, 1.0, 1.0]
    x = [1.0, 0.0, 0.0, 0.0]

    _force("python")
    py = backend.spmv_forward(indptr, indices, data, x)
    assert backend.backend_name() == "python"

    _force("numpy")
    np_ = backend.spmv_forward(indptr, indices, data, x)
    assert backend.backend_name() == "numpy"

    assert [round(v, 12) for v in py] == [round(v, 12) for v in np_]
    assert [round(v, 6) for v in py] == [0.0, 0.5, 0.5, 0.0]  # B and C each get 0.5


def test_numpy_l1_diff_matches_pure_python():
    a = [1.0, 0.5, 0.0, 2.0]
    b = [0.0, 0.5, 1.0, 1.0]
    _force("python")
    py = backend.l1_diff(a, b)
    _force("numpy")
    np_ = backend.l1_diff(a, b)
    assert round(py, 12) == round(np_, 12) == 3.0  # |1|+|0|+|1|+|1|


def test_empty_operator_is_safe():
    _force("numpy")
    assert backend.spmv_forward([0, 0], [], [], [0.0]) == [0.0]


# ── runtime wiring (observe_execution) ───────────────────────────────────────────


def test_observe_execution_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("MB_WORLD_TENSOR", raising=False)
    world_tensor.reset_for_test()
    n = world_tensor.observe_execution(["A", "B"], [("A", "B")])
    assert n == 0  # inert by default


def test_observe_execution_folds_into_world_when_enabled(monkeypatch):
    monkeypatch.setenv("MB_WORLD_TENSOR", "1")
    # Isolate from any ambient persistence config (e.g. a developer .env that sets
    # MB_WORLD_TENSOR_PATH) — otherwise reset_for_test() rebuilds the world by RELOADING
    # that file, and this test's exact nnz()==2 assertion sees the file's transitions too.
    monkeypatch.delenv("MB_WORLD_TENSOR_PATH", raising=False)
    monkeypatch.delenv("AGENTOS_WORLD_SHARD_DIR", raising=False)
    world_tensor.reset_for_test()
    n = world_tensor.observe_execution(
        ["Inspect", "Detect", "Treat"],
        [("Inspect", "Detect"), ("Detect", "Treat")],
        domain="beekeeping",
        reward=1.0,
    )
    assert n == 2
    w = world_tensor.get_world()
    assert w.nnz() == 2
    assert w.knows("Inspect", "Detect") if hasattr(w, "knows") else ("Inspect", "Detect") in set(w)
    world_tensor.reset_for_test()


def test_observe_execution_defensive_on_bad_input(monkeypatch):
    monkeypatch.setenv("MB_WORLD_TENSOR", "1")
    world_tensor.reset_for_test()
    # malformed edges must not raise — the hot path is defensive
    assert world_tensor.observe_execution(["A"], [("A",), None, ("A", "B")]) == 1
    world_tensor.reset_for_test()
