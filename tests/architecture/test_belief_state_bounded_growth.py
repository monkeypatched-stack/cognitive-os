"""Actor Runtime review, Phase 6 (scalability, actor-specific): "an Actor
is a PERSISTENT autonomous computational entity" implies it may tick
thousands or millions of times over its lifetime. BeliefState.facts/
hypotheses/observations/learned_updates/predictions/working_memory all
only ever grow via their own add_*() methods, with no cap of their own --
the ONLY thing preventing unbounded growth is BeliefState.decay_and_prune(),
called every tick from belief_runtime.py::_commit() (the real, live
Commit stage of the cognitive loop).

This mechanism is real, already correctly wired (confirmed: `belief.
decay_and_prune()` at belief_runtime.py:1676, inside the actual _commit()
method every real tick reaches), and its own docstring documents a real,
previously-measured incident (1500 ticks -> 18,000 unbounded prediction
entries -> 61ms per-tick deep-copy cost, the dominant share of a ~35x
per-tick latency regression). What was missing: ZERO existing test
anywhere references decay_and_prune() at all -- this file closes that
gap, proving the bound holds over a simulated long actor lifetime, not
merely that the method exists.
"""
from __future__ import annotations

import time

from src.monkey_brain.kernel.pipeline.belief_state import (
    BeliefState,
    Fact,
    Hypothesis,
    LearnedUpdate,
    Observation,
    Prediction,
    WorkingMemoryEntry,
)


class TestBeliefGrowthStaysBoundedOverManyTicks:
    def test_facts_hypotheses_observations_stay_bounded_after_1000_simulated_ticks(self):
        belief = BeliefState(actor_id="long-lived-actor")

        for tick in range(1000):
            # Every tick re-observes the SAME entity (the realistic,
            # common case -- a standing fact like "milk price is $3.99"
            # re-confirmed every cycle) -- the exact shape that grows
            # facts unboundedly if nothing prunes it.
            belief.add_fact(entity="product:milk", attribute="price", value=3.99, confidence=0.9)
            belief.add_hypothesis(claim=f"hypothesis-{tick}", confidence=0.6)
            belief.observations.append(Observation(entity="e", description=f"obs-{tick}"))
            belief.learned_updates.append(LearnedUpdate(what=f"learned-{tick}"))
            belief.predictions.append(Prediction(description=f"pred-{tick}"))
            belief.decay_and_prune()

        assert len(belief.facts) < 1000, "facts must not grow linearly with tick count when pruning runs every tick"
        assert len(belief.hypotheses) <= 50, "hypotheses must respect max_hypotheses"
        assert len(belief.observations) <= 200, "observations must respect max_observations"
        assert len(belief.learned_updates) <= 200, "learned_updates must respect max_learned_updates"
        assert len(belief.predictions) <= 200, "predictions must respect max_predictions"

    def test_a_fresh_high_confidence_fact_survives_pruning(self):
        belief = BeliefState(actor_id="a")
        belief.add_fact(entity="e", attribute="a", value=1, confidence=0.95)

        belief.decay_and_prune()

        assert len(belief.facts) == 1
        assert belief.facts[0].value == 1

    def test_an_old_low_confidence_fact_is_pruned(self):
        belief = BeliefState(actor_id="a")
        old_fact = Fact(
            entity="e", attribute="a", value="stale", confidence=0.05,
            observed_at=time.time() - 100_000,
        )
        belief.facts.append(old_fact)

        belief.decay_and_prune()

        assert old_fact not in belief.facts

    def test_expired_working_memory_entries_are_removed(self):
        belief = BeliefState(actor_id="a")
        now = time.time()
        belief.working_memory.append(WorkingMemoryEntry(key="expired", value=1, expires_at=now - 10))
        belief.working_memory.append(WorkingMemoryEntry(key="alive", value=2, expires_at=now + 10_000))

        belief.decay_and_prune()

        keys = {w.key for w in belief.working_memory}
        assert keys == {"alive"}

    def test_returns_a_report_of_what_was_pruned(self):
        belief = BeliefState(actor_id="a")
        belief.facts.append(Fact(entity="e", attribute="a", value=1, confidence=0.01, observed_at=time.time() - 100_000))

        report = belief.decay_and_prune()

        assert report["pruned_facts"] == 1


class TestDecayAndPruneIsWiredIntoTheRealCommitStage:
    """The specific historical failure mode this file guards against: the
    method existing but nothing in the pipeline calling it."""

    def test_commit_stage_calls_decay_and_prune(self):
        import inspect

        from src.monkey_brain.kernel.pipeline.belief_runtime import CognitiveRuntime

        source = inspect.getsource(CognitiveRuntime._commit)
        assert "decay_and_prune()" in source
