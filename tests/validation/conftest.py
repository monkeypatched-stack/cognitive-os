"""Shared fixtures for the Systems Validation suite (tests/validation/).

_FakeRedis merges the two proven fakes already used elsewhere in this
repo (tests/architecture/test_actor_tick_concurrency.py's real SET-NX-EX
+ INCR + compare-and-delete EVAL semantics, and
tests/scenarios/test_actor_scheduler.py's HSET/HGET/HGETALL/HDEL + node-
capacity EVAL semantics) into one place so every validation test file
can share a single, already-verified-correct in-memory Redis substitute
instead of hitting the real local Redis (which IS available in this
environment -- see the baseline report -- but using a fake for anything
that doesn't specifically need real persistence keeps these tests fast,
deterministic, and independent of leftover keys from other test runs).

Real infrastructure IS used elsewhere in this suite (see each file's own
docstring) when the invariant under test specifically requires observing
real persistence/network behavior (e.g. actual MongoDB durability across
a process restart) -- fakes are used only where the invariant under test
is about in-process logic (leases, fences, governance, delegation) that
does not depend on Redis's own durability.
"""
from __future__ import annotations

import json
import threading
import time

import pytest


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def _expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        return exp is not None and time.time() > exp

    def ping(self) -> bool:
        return True

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            if self._expired(key):
                self._store.pop(key, None)
            if nx and key in self._store:
                return False
            self._store[key] = value
            if ex is not None:
                self._expiry[key] = time.time() + ex
            else:
                self._expiry.pop(key, None)
            return True

    def get(self, key):
        if self._expired(key):
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    def incr(self, key: str) -> int:
        with self._lock:
            current = int(self._store.get(key, "0") or 0) + 1
            self._store[key] = str(current)
            return current

    def exists(self, key):
        return 1 if self.get(key) is not None else 0

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        return n

    def hset(self, name, key, value):
        self._hashes.setdefault(name, {})[key] = value

    def hget(self, name, key):
        return self._hashes.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        self._hashes.get(name, {}).pop(key, None)

    def rpush(self, key, value):
        self._hashes.setdefault(f"__list__{key}", {})
        lst = self._hashes[f"__list__{key}"]
        lst[str(len(lst))] = value

    def eval(self, script, numkeys, *args):
        if "cjson" in script:
            key = args[0]
            node_id, delta, ts = args[1], args[2], args[3]
            with self._lock:
                hashes = self._hashes.get(key, {})
                raw = hashes.get(node_id)
                if raw is None:
                    return -1
                node = json.loads(raw)
                delta = int(delta)
                capacity = int(node.get("capacity", 0))
                current = int(node.get("current_actor_count", 0))
                new_count = current + delta
                if delta > 0 and new_count > capacity:
                    return -2
                new_count = max(0, new_count)
                node["current_actor_count"] = new_count
                node["updated_at"] = float(ts)
                hashes[node_id] = json.dumps(node)
                self._hashes[key] = hashes
                return new_count
        # release_actor_lease's compare-and-delete script.
        key = args[0]
        token = args[1] if len(args) > 1 else None
        with self._lock:
            if self._store.get(key) == token and not self._expired(key):
                del self._store[key]
                return 1
            return 0


@pytest.fixture
def fake_redis():
    return FakeRedis()


def register(pr, name, **kwargs):
    from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile, ActorType
    return pr.register_actor(
        ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN)), **kwargs,
    )


def force_redis_authoritative(pr) -> None:
    """Systems Validation finding (Section 3/17, reported separately in
    full): PlanetaryRuntime.locate_actor()/list_registry() call
    _list_registry_from_mongodb() FIRST and, whenever it returns ANY rows
    at all (real Mongo reachable and non-empty -- true for this dev
    environment, which has accumulated hundreds of actors from past
    session work), take the `if rows:` branch UNCONDITIONALLY -- even
    when the specific actor_id being looked up isn't among those rows.
    The Redis-registry fallback (`elif rows is None and self._redis:`)
    only ever runs when Mongo is completely unreachable, never when it's
    merely reachable-but-missing-this-actor. This makes a real, reachable
    Mongo silently shadow Redis for any actor that exists in Redis but
    hasn't (yet, or ever, in a Redis-only/edge deployment) landed in
    Mongo -- exactly the shared-Redis-between-two-nodes scenario every
    cross-node migration/discovery test in this repo (including this
    suite's own) depends on.

    This helper works around that bug for tests that are validating a
    DIFFERENT invariant (e.g. "does the lease fence work") and don't want
    that unrelated bug to make the test un-writable -- it does NOT fix
    the bug itself (see the Systems Validation report's own finding for
    the real fix recommendation)."""
    pr._list_registry_from_mongodb = lambda: None
