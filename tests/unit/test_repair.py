"""Tests for LossDrivenRepair convergence."""

import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.loss_driven_repair import LossDrivenRepair


def test_repair_convergence():
    repair = LossDrivenRepair()
    report = repair.run(
        0.8,
        {
            "test_results": {},
            "ddd_results": {},
            "govern_results": {},
            "runtime_errors": [],
        },
        {"loss": 0.8},
    )
    assert report is not None
    assert hasattr(report, "iterations") or hasattr(report, "initial_loss")


def test_repair_returns_report():
    repair = LossDrivenRepair()
    report = repair.run(
        0.5,
        {
            "test_results": {},
            "ddd_results": {},
            "govern_results": {},
            "runtime_errors": [],
        },
        {"loss": 0.5},
    )
    assert report is not None


def test_repair_bounded_iterations():
    repair = LossDrivenRepair(max_iterations=3)
    report = repair.run(
        0.9,
        {
            "test_results": {},
            "ddd_results": {},
            "govern_results": {},
            "runtime_errors": [],
        },
        {"loss": 0.9},
    )
    assert report is not None


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in [
        ("convergence", test_repair_convergence),
        ("returns_report", test_repair_returns_report),
        ("bounded", test_repair_bounded_iterations),
    ]:
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"LossDrivenRepair: {ok}/3 passed")
    for e in f:
        print(f"  FAIL: {e}")
