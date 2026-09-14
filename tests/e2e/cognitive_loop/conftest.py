"""Local override for the minimal CognitiveOS E2E suite (E2E-01..05).

These tests are deliberately NOT hermetic in the way the rest of tests/
is: they enter through the real, already-running HTTP boundary
(AGENTOS_URL, default http://localhost:8031) instead of constructing
PlanetaryRuntime/CognitiveActor/etc. in-process, and they depend on the
world already seeded via scripts/seed_world.py (Priya Sharma and family)
plus fresh actors they create themselves through the real POST /actors
endpoint. That means they must run AGAINST the live dev server's real
Redis, Mongo, Neo4j, ES, and Ollama — the opposite of every other test
in this tree.

tests/conftest.py's autouse `_flush_shared_redis` fixture FLUSHDBs the
one real Redis instance the live dev server also uses (its own
docstring says so explicitly: "never run pytest against the same Redis
a live dev server is using — it will wipe the dev server's persisted
state"). Running that fixture here would wipe out the just-restarted,
just-reseeded world these tests are supposed to exercise, out from
under the live server, mid-test. Overriding it as a no-op for this one
subtree is the intended, supported way to opt a directory out (nearest
conftest.py wins for same-named fixtures) — every other autouse fixture
in tests/conftest.py (tenant context, world-tensor env, timeline
singleton reset, fake LLM backend) only mutates state inside the pytest
process itself, which these HTTP-only tests never share with the
separate live server process, so none of those need overriding.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _flush_shared_redis():
    """No-op override — see module docstring. Deliberately does NOT flush
    the shared Redis the live dev server (and this suite's HTTP calls)
    both depend on."""
    yield
