"""Regression coverage for the adaptive-planning-loop refactor of
TransactionCoordinator (kernel/society/transaction.py + the new
kernel/society/negotiation_planning.py).

Before the refactor, the coordinator behaved as an exhaustive search: the
LLM's next-action choice was the *only* thing that could end a round, so
an LLM that kept suggesting "contact_another_affiliate" (as the real
Ollama backend was observed doing, even after an affiliate had already
achieved the objective and a Nash-equilibrium-stable "complete_objective"
suggestion was available) walked every eligible affiliate regardless of
outcome, only stopping at max_steps. These tests pin down the replacement
behavior: TerminalStateEvaluator (fully deterministic, no LLM) decides
whether a round ended the transaction, and the LLM is consulted only when
it has NOT -- so it structurally cannot override a terminal condition.

Every scenario below drives the real `TransactionCoordinator.execute()`
through a `_ScriptedCoordinator` subclass that overrides only the methods
that would otherwise need a live PlanetaryRuntime (society/affiliation
resolution, actor ticking, LLM calls) with test-controlled scripts --
the negotiation-loop control flow under test is exercised unmodified.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from src.monkey_brain.kernel.society.transaction import (
    NegotiationTrace,
    TransactionCoordinator,
    TransactionStatus,
)


class _ScriptedCoordinator(TransactionCoordinator):
    """A TransactionCoordinator with every PlanetaryRuntime-dependent hook
    replaced by a script the test controls directly, so `execute()`'s
    control flow (planner selection -> negotiate -> terminal evaluation
    -> LLM-only-if-not-terminal) runs for real without booting a society.
    """

    def __init__(
        self,
        *,
        candidates: tuple[str, ...],
        traces: dict[str, Any],
        strategic_contexts: dict[str, dict] | None = None,
        decisions: dict[str, Any] | None = None,
        trust: dict[str, float] | None = None,
        policy_gate=None,
    ) -> None:
        super().__init__(planetary=None, policy_gate=policy_gate)
        self._candidates = tuple(candidates)
        self._traces = traces
        self._strategic_contexts = strategic_contexts or {}
        self._decisions = decisions or {}
        self._trust = dict(trust or {})
        self.contacted_log: list[str] = []
        self.decide_calls = 0

    # ── Society/affiliation resolution -- scripted, no PlanetaryRuntime ──

    def _relevant_societies(self, actor_id: str, objective: str) -> set[str]:
        return set()

    def _eligible_affiliates(self, actor_id: str, relevant_society_ids: set[str]) -> tuple[str, ...]:
        return self._candidates

    def _current_trust(self, originating_actor_id: str, target_actor_id: str) -> float:
        return self._trust.get(target_actor_id, 0.0)

    # ── Negotiation -- scripted per-affiliate outcome ───────────────────

    async def _send_message(self, originating_actor_id: str, target_actor_id: str, message: str):
        self.contacted_log.append(target_actor_id)
        scripted = self._traces.get(target_actor_id)
        if isinstance(scripted, list):
            return scripted.pop(0) if scripted else None
        return scripted

    def _strategic_context(self, originating_actor_id, target_actor_id, trace, remaining_candidates):
        return self._strategic_contexts.get(target_actor_id)

    async def _decide_next_action(
        self,
        originating_actor_id,
        objective,
        prior_steps,
        last_target,
        last_trace,
        remaining_candidates,
        strategic_context,
    ) -> dict[str, Any]:
        self.decide_calls += 1
        scripted = self._decisions.get(
            last_target,
            {
                "next_action": "contact_another_affiliate",
                "reason": "default",
                "target_actor_id": None,
            },
        )
        if isinstance(scripted, list):
            scripted = scripted.pop(0)
        return {**scripted, "strategic_context": strategic_context}

    # ── Side effects irrelevant to this loop's control flow -- no-ops ───

    def _update_trust_from_trace(self, originating_actor_id, target_actor_id, trace) -> None:
        pass

    def _apply_trust_outcome(self, originating_actor_id, target_actor_id, *, goal_achieved: bool) -> None:
        pass

    def _publish_belief_perturbation(self, target_actor_id, trace) -> None:
        pass


def _run(
    coordinator: TransactionCoordinator,
    objective: str = "ask my roommate if we need to buy more milk",
    **kwargs,
):
    return asyncio.run(coordinator.execute("originator", objective, **kwargs))


def _achieved(actor_id: str) -> NegotiationTrace:
    return NegotiationTrace(
        actor_id=actor_id,
        execution_outcome="goal_achieved",
        explanation=f"{actor_id} achieved it",
    )


def _failed(actor_id: str) -> NegotiationTrace:
    return NegotiationTrace(actor_id=actor_id, execution_outcome="failed", explanation=f"{actor_id} failed")


def _ambiguous(actor_id: str) -> NegotiationTrace:
    return NegotiationTrace(
        actor_id=actor_id,
        execution_outcome="acted",
        explanation=f"{actor_id} acted, unclear",
    )


_CONTACT_ANOTHER = {
    "next_action": "contact_another_affiliate",
    "reason": "keep going",
    "target_actor_id": None,
}


class TestFirstAffiliateSucceeds(unittest.TestCase):
    """Test 1: Trader Joe's satisfies the objective on the first round --
    Safeway and Alice must never be contacted."""

    def test_terminates_after_first_success(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={"trader_joes": _achieved("trader_joes")},
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["trader_joes"])
        self.assertEqual(result.status, TransactionStatus.COMPLETED)
        self.assertEqual(result.affiliates_contacted, ("trader_joes",))
        self.assertNotIn("safeway", coordinator.contacted_log)
        self.assertNotIn("alice", coordinator.contacted_log)


class TestSecondAffiliateRequired(unittest.TestCase):
    """Test 2: Trader Joe's fails, Safeway succeeds -- Alice is never
    contacted and the transaction ends immediately after Safeway."""

    def test_terminates_after_second_success(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={
                "trader_joes": _failed("trader_joes"),
                "safeway": _achieved("safeway"),
            },
            decisions={"trader_joes": dict(_CONTACT_ANOTHER)},
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["trader_joes", "safeway"])
        self.assertNotIn("alice", coordinator.contacted_log)
        self.assertEqual(result.status, TransactionStatus.COMPLETED)


class TestThirdAffiliateRequired(unittest.TestCase):
    """Test 3: Trader Joe's and Safeway fail, Alice succeeds -- exactly
    three negotiations, terminating immediately after Alice."""

    def test_terminates_after_third_success(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={
                "trader_joes": _failed("trader_joes"),
                "safeway": _failed("safeway"),
                "alice": _achieved("alice"),
            },
            decisions={
                "trader_joes": dict(_CONTACT_ANOTHER),
                "safeway": dict(_CONTACT_ANOTHER),
            },
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["trader_joes", "safeway", "alice"])
        self.assertEqual(result.status, TransactionStatus.COMPLETED)


class TestNoAffiliateCanSolve(unittest.TestCase):
    """Test 4: every affiliate fails -- the runtime exhausts the search
    space and terminates with failure, without exceeding max_steps."""

    def test_exhausts_search_space_without_exceeding_max_steps(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={
                "trader_joes": _failed("trader_joes"),
                "safeway": _failed("safeway"),
                "alice": _failed("alice"),
            },
            decisions={
                "trader_joes": dict(_CONTACT_ANOTHER),
                "safeway": dict(_CONTACT_ANOTHER),
            },
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator, max_steps=8)

        self.assertEqual(coordinator.contacted_log, ["trader_joes", "safeway", "alice"])
        self.assertEqual(result.status, TransactionStatus.TERMINATED)
        self.assertIn("no eligible affiliates remain", result.final_outcome)
        self.assertLessEqual(len(result.steps), 8)


class TestNashEquilibrium(unittest.TestCase):
    """Test 5: the runtime determines equilibrium on the first round --
    the coordinator terminates immediately and never consults the LLM or
    contacts another affiliate."""

    def test_terminates_on_equilibrium_without_llm_call(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={"trader_joes": _ambiguous("trader_joes")},
            strategic_contexts={
                "trader_joes": {
                    "suggested_action": "terminate_transaction",
                    "equilibrium": True,
                },
            },
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["trader_joes"])
        self.assertEqual(coordinator.decide_calls, 0, "LLM must not be consulted once terminal")
        self.assertEqual(result.status, TransactionStatus.TERMINATED)
        self.assertIn("nash equilibrium", result.final_outcome)


class TestPolicyTermination(unittest.TestCase):
    """Test 6: a policy blocks further negotiation before any affiliate is
    selected -- immediate termination, no affiliate selection at all."""

    def test_policy_gate_blocks_before_any_selection(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={},
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
            policy_gate=lambda state: "policy requires termination",
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, [])
        self.assertEqual(coordinator.decide_calls, 0)
        self.assertEqual(result.status, TransactionStatus.TERMINATED)
        self.assertEqual(result.final_outcome, "policy requires termination")


class TestMaxSteps(unittest.TestCase):
    """Test 7: no deterministic terminal condition is ever reached --
    the planner stops exactly at max_steps, no additional negotiations."""

    def test_stops_exactly_at_max_steps(self):
        candidates = ("a", "b", "c", "d", "e")
        coordinator = _ScriptedCoordinator(
            candidates=candidates,
            traces={c: [_ambiguous(c)] for c in candidates},
            decisions={c: dict(_CONTACT_ANOTHER) for c in candidates},
            trust={c: 1.0 - i * 0.1 for i, c in enumerate(candidates)},
        )
        result = _run(coordinator, max_steps=3)

        self.assertEqual(len(coordinator.contacted_log), 3)
        self.assertEqual(len(result.steps), 3)
        self.assertEqual(result.status, TransactionStatus.TERMINATED)
        self.assertIn("max_steps (3)", result.final_outcome)


class TestTrustOrdering(unittest.TestCase):
    """Test 8: the planner selects the highest-trust affiliate first; if
    that one fails, it re-evaluates and selects the best remaining one --
    not simply the next item in a static list."""

    def test_selects_highest_trust_first_then_next_best(self):
        coordinator = _ScriptedCoordinator(
            candidates=("a", "b", "c"),
            traces={"a": _failed("a"), "b": _achieved("b")},
            decisions={"a": dict(_CONTACT_ANOTHER)},
            trust={"a": 0.95, "b": 0.80, "c": 0.60},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["a", "b"])
        self.assertNotIn("c", coordinator.contacted_log)
        self.assertEqual(result.status, TransactionStatus.COMPLETED)


class TestPlannerReplanning(unittest.TestCase):
    """Test 9: an affiliate's response changes the transaction state (here,
    trust) such that the optimal *next* affiliate differs from what a
    static walk of the originally-sorted list would have picked --
    proving the planner re-evaluates every round instead of blindly
    following the original order."""

    def test_reranks_after_trust_changes_mid_transaction(self):
        coordinator = _ScriptedCoordinator(
            # Initial trust order is a(.90) > c(.85) > b(.50) -- a static
            # walk of this list would go to "c" after "a" fails.
            candidates=("a", "b", "c"),
            traces={"a": _failed("a"), "b": _achieved("b")},
            decisions={"a": dict(_CONTACT_ANOTHER)},
            trust={"a": 0.90, "b": 0.50, "c": 0.85},
        )

        original_update = coordinator._update_trust_from_trace

        def _bump_b_after_a_fails(originating_actor_id, target_actor_id, trace):
            original_update(originating_actor_id, target_actor_id, trace)
            if target_actor_id == "a":
                # Something about "a"'s failed round makes "b" the better
                # bet now -- the live signal the planner must react to.
                coordinator._trust["b"] = 0.99

        coordinator._update_trust_from_trace = _bump_b_after_a_fails

        result = _run(coordinator)

        self.assertEqual(
            coordinator.contacted_log,
            ["a", "b"],
            "planner should have re-ranked and picked 'b' (now trust 0.99), not 'c' (a static list walk's next item)",
        )
        self.assertNotIn("c", coordinator.contacted_log)
        self.assertEqual(result.status, TransactionStatus.COMPLETED)


class TestEarlyTerminationRegression(unittest.TestCase):
    """Test 10: reproduces the observed bug directly -- Trader Joe's
    achieves the objective AND the game-theoretic evaluation reports an
    equilibrium-stable "complete_objective" suggestion, while the (scripted,
    stand-in for the real Ollama backend) LLM decision -- if it were ever
    consulted -- would say "contact_another_affiliate" toward Safeway. On
    the pre-refactor exhaustive-search coordinator this test fails (the
    LLM's choice was the only thing that could stop a round, so it would
    walk on to Safeway and Alice). On the adaptive-planning-loop
    coordinator it passes: TerminalStateEvaluator proves the round terminal
    from the trace/strategic_context alone, so the LLM is never consulted
    and Safeway/Alice are never contacted."""

    def test_terminates_immediately_despite_llm_suggesting_continue(self):
        coordinator = _ScriptedCoordinator(
            candidates=("trader_joes", "safeway", "alice"),
            traces={"trader_joes": _achieved("trader_joes")},
            strategic_contexts={
                "trader_joes": {
                    "suggested_action": "complete_objective",
                    "equilibrium": True,
                },
            },
            decisions={
                "trader_joes": {
                    "next_action": "contact_another_affiliate",
                    "reason": "would keep going if consulted",
                    "target_actor_id": "safeway",
                },
            },
            trust={"trader_joes": 0.90, "safeway": 0.85, "alice": 0.80},
        )
        result = _run(coordinator)

        self.assertEqual(coordinator.contacted_log, ["trader_joes"])
        self.assertNotIn("safeway", coordinator.contacted_log)
        self.assertNotIn("alice", coordinator.contacted_log)
        self.assertEqual(coordinator.decide_calls, 0, "LLM must not be consulted once terminal")
        self.assertEqual(result.status, TransactionStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
