"""Tests for Learner module."""

import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.learn.epa.learner import Learner


def test_learner_construction():
    l = Learner()
    assert l is not None


def test_learner_update():
    l = Learner()
    assert hasattr(l, "update_q")


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in [
        ("construction", test_learner_construction),
        ("update", test_learner_update),
    ]:
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"Learner: {ok}/2 passed")
    for e in f:
        print(f"  FAIL: {e}")
