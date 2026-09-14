"""Tests for MonteCarlo planner module."""

import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.predict.mcts.rl_monte_carlo import MonteCarloPlanner


def test_mc_construction():
    mc = MonteCarloPlanner()
    assert mc is not None


def test_mc_has_plan():
    mc = MonteCarloPlanner()
    assert hasattr(mc, "plan") or hasattr(mc, "simulate") or hasattr(mc, "rollout")


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in [
        ("construction", test_mc_construction),
        ("has_plan", test_mc_has_plan),
    ]:
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"MonteCarlo: {ok}/2 passed")
    for e in f:
        print(f"  FAIL: {e}")
