"""Tests for PersistenceManager."""

import asyncio
import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.persistence.manager import PersistenceManager
from src.monkey_brain.persistence.reducer import Reducer


def test_pm_construction():
    pm = PersistenceManager()
    assert pm is not None


def test_pm_summary():
    pm = PersistenceManager()
    s = pm.summary()
    assert isinstance(s, dict)


def test_reducer_pure():
    r = Reducer()
    assert r is not None


def test_reducer_reduce():
    r = Reducer()
    mutation = r.reduce({}, {"type": "entity_created", "data": {"id": "x"}})
    assert mutation is not None


if __name__ == "__main__":
    ok = 0
    f = []
    for name, fn in [
        ("construction", test_pm_construction),
        ("summary", test_pm_summary),
        ("reducer", test_reducer_pure),
        ("reduce", test_reducer_reduce),
    ]:
        try:
            fn()
            ok += 1
        except Exception as e:
            f.append(f"{name}: {e}")
    print(f"PersistenceManager: {ok}/4 passed")
    for e in f:
        print(f"  FAIL: {e}")
