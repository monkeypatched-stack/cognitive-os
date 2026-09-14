"""Tests for transition module."""

import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.fix.policy.transition import Transition, TransitionTable


def test_transition_creation():
    t = Transition(state={"a": 1}, action="move", reward=0.5, next_state={"a": 2})
    assert t.state == {"a": 1}
    assert t.action == "move"
    assert t.reward == 0.5


def test_transition_table_store():
    tt = TransitionTable()
    tt.store(Transition(state={"a": 1}, action="move", reward=0.5, next_state={"a": 2}))
    assert tt.count() == 1


def test_transition_table_query():
    tt = TransitionTable()
    tt.store(Transition(state={"a": 1}, action="move", reward=0.5, next_state={"a": 2}))
    tt.store(Transition(state={"a": 1}, action="jump", reward=0.3, next_state={"a": 3}))
    results = tt.query({"a": 1})
    assert len(results) == 2


def test_transition_table_sample():
    tt = TransitionTable()
    for i in range(10):
        tt.store(Transition(state={"i": i}, action="a", reward=0.1, next_state={"i": i + 1}))
    samples = tt.sample(5)
    assert len(samples) == 5


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in [
        ("creation", test_transition_creation),
        ("store", test_transition_table_store),
        ("query", test_transition_table_query),
        ("sample", test_transition_table_sample),
    ]:
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"Transition: {ok}/4 passed")
    for e in f:
        print(f"  FAIL: {e}")
