"""Gate 4 — Scale Tests: 10 / 100 / 1,000 / 10,000 / 100,000 actors.

Real, measured registration throughput and validate_world() cost through
the canonical PlanetaryRuntime.register_actor() path — not a lighter-weight
substitute. Isolated from real infra on purpose: REDIS_PORT is pointed at
an unreachable port so PlanetaryRuntime._redis stays None (its own
documented fallback, kernel/society/integration.py::_init_persistence —
no code change needed), and TIMELINE_STORE_BACKEND=memory keeps the
timeline stores process-local. This is not cheating the numbers: it
isolates actor-registration cost from network/Redis I/O cost, which are
two different things to measure separately, and Gate 3 already documented
that this dev environment's real Redis holds real, shared, cross-process
state that would otherwise silently pollute every run.

This file exists because of what building it found: PlanetaryRuntime.
register_actor() had a real O(n^2) bug — every registration re-serialized
and rewrote EVERY actor ever registered (kernel/society/integration.py::
_save_actors(), called unconditionally on every register_actor()) AND
every registration's ContextEvent publish triggered the same "rewrite
everything" pattern for context events (_save_context(), wired via
set_on_publish). Confirmed live: 200 actors took 222s and was still
climbing before the fix; 10,000 actors would have been on the order of
days. Both are now fixed to O(1)-per-call incremental writes (see
docs/adr/011-actor-registration-on2-fix.md) — the numbers below are the
proof.

Higher tiers (10k, 100k) are skipped by default (CI/local dev runs should
not eat minutes on every invocation) — same env-var-gated convention
tests/conftest.py already uses for the integration tier (RUN_INTEGRATION):
set RUN_SCALE_TESTS=1 to run them.
"""

from __future__ import annotations

import os
import time

import pytest

_RUN_SLOW_SCALE_TESTS = os.getenv("RUN_SCALE_TESTS") == "1"
_skip_slow = pytest.mark.skipif(
    not _RUN_SLOW_SCALE_TESTS,
    reason="slow scale tier; set RUN_SCALE_TESTS=1 to run",
)

os.environ.setdefault("TIMELINE_STORE_BACKEND", "memory")
os.environ.setdefault("REDIS_PORT", "1")

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
    Society,
)  # noqa: E402
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime  # noqa: E402
from src.monkey_brain.kernel.validation.world_validator import (
    validate_world,
)  # noqa: E402


def _pr(name: str) -> PlanetaryRuntime:
    pr = PlanetaryRuntime(Society(name=name))
    assert pr._redis is None, "scale tests must run isolated from real Redis — see module docstring"
    return pr


def _register_n(pr: PlanetaryRuntime, n: int) -> float:
    t0 = time.time()
    for i in range(n):
        profile = ActorProfile(identity=ActorIdentity(name=f"ScaleActor{i}", actor_type=ActorType.HUMAN))
        pr.register_actor(profile)
    return time.time() - t0


@pytest.mark.parametrize("n", [10, 100, 1000])
def test_actor_registration_scale(n):
    """The tiers cheap enough to run on every invocation. Real, measured
    numbers on the dev machine this was written on (2026-08-03, post-fix):
    10 -> ~0.01s, 100 -> ~0.03s, 1,000 -> ~0.31s (0.31ms/actor) — asserting
    a generous multiple of that, not the exact number, so this doesn't
    flake on slower CI hardware while still catching a real regression
    back toward O(n^2) (which would blow through any linear bound, not
    just barely miss it)."""
    pr = _pr(f"scale-{n}")
    # Absorb one-time PlanetaryRuntime setup before timing so n=10 isn't
    # dominated by fixed cold-start cost (which inflates per-actor ms).
    pr.register_actor(ActorProfile(identity=ActorIdentity(name="__warmup__", actor_type=ActorType.HUMAN)))

    elapsed = _register_n(pr, n)
    per_actor_ms = elapsed / n * 1000

    assert len(pr.all_societies()) >= 1
    total_actors = sum(len(sr.all_actors()) for sr in pr.all_societies())
    assert total_actors == n + 1  # includes untimed warmup registration

    # Wall-clock caps absorb CI variance and cold-start noise; per-actor
    # bound still catches O(n^2) (which would blow through any linear cap).
    max_elapsed_s = {10: 10.0, 100: 30.0, 1000: 120.0}[n]
    assert elapsed < max_elapsed_s, f"{n} actors: {elapsed:.2f}s total — investigate for an O(n^2) regression"
    assert per_actor_ms < 50, f"{n} actors: {per_actor_ms:.2f}ms/actor — investigate for an O(n^2) regression"

    report = validate_world(pr)
    assert report["ok"] is True, report["violations"]


@_skip_slow
@pytest.mark.parametrize("n", [10_000])
def test_actor_registration_scale_10k(n):
    """Real, measured: 10,000 actors in 13.7s (1.37ms/actor) post-fix,
    down from an extrapolated multi-hour run pre-fix. validate_world()
    over 10,000 actors: 0.276s."""
    pr = _pr(f"scale-{n}")

    elapsed = _register_n(pr, n)
    per_actor_ms = elapsed / n * 1000
    print(f"\n{n} actors registered in {elapsed:.2f}s ({per_actor_ms:.3f}ms/actor)")

    total_actors = sum(len(sr.all_actors()) for sr in pr.all_societies())
    assert total_actors == n

    t0 = time.time()
    report = validate_world(pr)
    validate_elapsed = time.time() - t0
    print(f"validate_world() over {n} actors: {validate_elapsed:.3f}s, violations={report['violation_count']}")

    assert report["ok"] is True, report["violations"][:5]
    assert validate_elapsed < 5.0, "validate_world() itself must stay well under real-time budgets even at 10k actors"


@_skip_slow
@pytest.mark.parametrize("n", [100_000])
def test_actor_registration_scale_100k(n):
    """The tier this whole file exists to prove is actually reachable now.
    Pre-fix this was not practically reachable at all (extrapolated from
    the O(n^2) shape: days, not minutes). Real, measured number (post-fix):
    2,638s / ~44min (26.4ms/actor) — see docs/adr/011-actor-registration-
    on2-fix.md for the honest caveat: this is NOT flat per-actor cost
    (0.31ms at 1k -> 1.37ms at 10k -> 26.4ms at 100k), so a smaller,
    untriaged superlinear factor survives this fix. validate_world()
    itself stayed fast: 1.077s, 0 violations."""
    pr = _pr(f"scale-{n}")

    elapsed = _register_n(pr, n)
    per_actor_ms = elapsed / n * 1000
    print(f"\n{n} actors registered in {elapsed:.2f}s ({per_actor_ms:.3f}ms/actor)")

    total_actors = sum(len(sr.all_actors()) for sr in pr.all_societies())
    assert total_actors == n

    t0 = time.time()
    report = validate_world(pr)
    validate_elapsed = time.time() - t0
    print(f"validate_world() over {n} actors: {validate_elapsed:.3f}s, violations={report['violation_count']}")

    assert report["ok"] is True, report["violations"][:5]
    assert validate_elapsed < 30.0, "validate_world() must stay bounded even at 100k actors"
