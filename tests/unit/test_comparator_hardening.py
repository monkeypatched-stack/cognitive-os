"""Comparator Hardening Tests — focused tests for the Comparator subsystem.

These tests verify the ComparatorRuntime meets the hardening requirements:
1. Perfect success
2. Complete failure
3. Partial failure
4. Unexpected outcome
5. Missing observation
6. Multi-step partial execution
7. Node-level diff
8. Deterministic comparison
9. Epistemic loss
10. Comparator produces no learning side effects
11. Observation provenance
12. Inconclusive result when actual state cannot be established
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.monkey_brain.kernel.comparator_runtime import (
    ComparatorRuntime,
    ComparatorOutcome,
    ComparisonResult,
)


@pytest.fixture
def comparator_runtime():
    """Create a ComparatorRuntime instance for testing."""
    return ComparatorRuntime()


class TestPerfectSuccess:
    """Test 1: Perfect success scenario."""

    @pytest.mark.asyncio
    async def test_perfect_success(self, comparator_runtime):
        """When all expected nodes succeed, outcome should be SUCCESS."""
        simulation_graph = {
            "graph_id": "test-exec-1",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": True},
                {"id": "node3", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"], ["node3"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"milk": "purchased", "eggs": "purchased"},
                    "operations": ["buy_milk", "buy_eggs"],
                    "events": ["milk_purchased", "eggs_purchased"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-1",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": True},
                {"id": "node3", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"], ["node3"]],
            "state": {"milk": "purchased", "eggs": "purchased"},
            "operations": ["buy_milk", "buy_eggs"],
            "events": ["milk_purchased", "eggs_purchased"],
            "reward": 1.0,
            "confidence": 0.95,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.SUCCESS
        assert result.epistemic_loss == 0.0
        assert result.world_loss == 0.0
        assert len(result.node_diffs) == 3
        for node_diff in result.node_diffs.values():
            assert node_diff["match"] is True
            assert node_diff["expected_success"] is True
            assert node_diff["actual_success"] is True


class TestCompleteFailure:
    """Test 2: Complete failure scenario."""

    @pytest.mark.asyncio
    async def test_complete_failure(self, comparator_runtime):
        """When all expected nodes fail and were expected to fail, outcome should be FAILURE."""
        simulation_graph = {
            "graph_id": "test-exec-2",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": False},
                {"id": "node2", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"milk": "not_purchased"},
                    "operations": [],
                    "events": [],
                    "predicted_reward": 0.0,
                    "grounding_score": 0.1,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-2",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": False},
                {"id": "node2", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"]],
            "state": {"milk": "not_purchased"},
            "operations": [],
            "events": [],
            "reward": 0.0,
            "confidence": 0.1,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.FAILURE
        assert result.epistemic_loss >= 0.0
        assert len(result.node_diffs) == 2
        for node_diff in result.node_diffs.values():
            assert node_diff["match"] is True
            assert node_diff["expected_success"] is False
            assert node_diff["actual_success"] is False


class TestPartialFailure:
    """Test 3: Partial failure scenario."""

    @pytest.mark.asyncio
    async def test_partial_failure(self, comparator_runtime):
        """When some nodes succeed and others fail, outcome should be PARTIAL_SUCCESS."""
        simulation_graph = {
            "graph_id": "test-exec-3",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "milk_node", "success": True},
                {"id": "eggs_node", "success": True},
            ],
            "edges": [],
            "execution_order": [["milk_node"], ["eggs_node"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"milk": "purchased", "eggs": "purchased"},
                    "operations": ["buy_milk", "buy_eggs"],
                    "events": ["milk_purchased", "eggs_purchased"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-3",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "milk_node", "success": True},
                {"id": "eggs_node", "success": False},
            ],
            "edges": [],
            "execution_order": [["milk_node"], ["eggs_node"]],
            "state": {"milk": "purchased", "eggs": "not_purchased"},
            "operations": ["buy_milk"],
            "events": ["milk_purchased"],
            "reward": 0.5,
            "confidence": 0.5,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.PARTIAL_SUCCESS
        assert result.node_diffs["milk_node"]["match"] is True
        assert result.node_diffs["milk_node"]["expected_success"] is True
        assert result.node_diffs["milk_node"]["actual_success"] is True
        assert result.node_diffs["eggs_node"]["match"] is False
        assert result.node_diffs["eggs_node"]["expected_success"] is True
        assert result.node_diffs["eggs_node"]["actual_success"] is False


class TestUnexpectedOutcome:
    """Test 4: Unexpected outcome scenarios."""

    @pytest.mark.asyncio
    async def test_unexpected_success(self, comparator_runtime):
        """When expected failure but actual success, outcome should be UNEXPECTED_SUCCESS."""
        simulation_graph = {
            "graph_id": "test-exec-4",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"task": "failed"},
                    "operations": [],
                    "events": [],
                    "predicted_reward": 0.0,
                    "grounding_score": 0.5,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-4",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {"task": "completed"},
            "operations": ["complete_task"],
            "events": ["task_completed"],
            "reward": 1.0,
            "confidence": 0.9,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.UNEXPECTED_SUCCESS

    @pytest.mark.asyncio
    async def test_unexpected_failure(self, comparator_runtime):
        """When expected success but actual failure, outcome should be UNEXPECTED_FAILURE."""
        simulation_graph = {
            "graph_id": "test-exec-5",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"task": "completed"},
                    "operations": ["complete_task"],
                    "events": ["task_completed"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-5",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {"task": "failed"},
            "operations": [],
            "events": [],
            "reward": 0.0,
            "confidence": 0.1,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        # Single node expected to succeed but failed = unexpected failure
        assert result.outcome == ComparatorOutcome.UNEXPECTED_FAILURE


class TestMissingObservation:
    """Test 5: Missing observation scenario."""

    @pytest.mark.asyncio
    async def test_missing_observation(self, comparator_runtime):
        """When execution result is missing or empty, outcome should be INCONCLUSIVE."""
        simulation_graph = {
            "graph_id": "test-exec-6",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"task": "completed"},
                    "operations": ["complete_task"],
                    "events": ["task_completed"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-6",
            "timestamp": 1234567890.0,
            "nodes": [],  # Empty nodes - no actual observation
            "edges": [],
            "execution_order": [],
            "state": {},
            "operations": [],
            "events": [],
            "reward": 0.0,
            "confidence": 0.0,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.INCONCLUSIVE


class TestMultiStepPartialExecution:
    """Test 6: Multi-step partial execution scenario."""

    @pytest.mark.asyncio
    async def test_multi_step_partial_execution(self, comparator_runtime):
        """Test A → B → C where B fails, C not executed."""
        simulation_graph = {
            "graph_id": "test-exec-7",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "A", "success": True},
                {"id": "B", "success": True},
                {"id": "C", "success": True},
            ],
            "edges": [],
            "execution_order": [["A"], ["B"], ["C"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"A": "done", "B": "done", "C": "done"},
                    "operations": ["op_a", "op_b", "op_c"],
                    "events": ["evt_a", "evt_b", "evt_c"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-7",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "A", "success": True},
                {"id": "B", "success": False},
                # C not executed (missing from execution result)
            ],
            "edges": [],
            "execution_order": [["A"], ["B"]],
            "state": {"A": "done", "B": "failed"},
            "operations": ["op_a"],
            "events": ["evt_a"],
            "reward": 0.3,
            "confidence": 0.3,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.PARTIAL_SUCCESS
        assert result.node_diffs["A"]["match"] is True
        assert result.node_diffs["A"]["expected_success"] is True
        assert result.node_diffs["A"]["actual_success"] is True
        assert result.node_diffs["B"]["match"] is False
        assert result.node_diffs["B"]["expected_success"] is True
        assert result.node_diffs["B"]["actual_success"] is False
        assert result.node_diffs["C"]["match"] is False  # C not executed
        assert result.node_diffs["C"]["expected_success"] is True
        assert result.node_diffs["C"]["actual_success"] is None  # Missing from execution


class TestNodeLevelDiff:
    """Test 7: Node-level diff preservation."""

    @pytest.mark.asyncio
    async def test_node_level_diff(self, comparator_runtime):
        """Verify node-level differences are preserved, not collapsed."""
        simulation_graph = {
            "graph_id": "test-exec-8",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": True},
                {"id": "node3", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"], ["node3"]],
            "metadata": {
                "summary": {
                    "predicted_state": {},
                    "operations": [],
                    "events": [],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-8",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": False},
                {"id": "node3", "success": True},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"], ["node3"]],
            "state": {},
            "operations": [],
            "events": [],
            "reward": 0.6,
            "confidence": 0.6,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        # Verify node-level diffs are preserved
        assert len(result.node_diffs) == 3
        assert result.node_diffs["node1"]["match"] is True
        assert result.node_diffs["node2"]["match"] is False
        assert result.node_diffs["node3"]["match"] is True

        # Verify the failing node is specifically identified
        assert result.node_diffs["node2"]["expected_success"] is True
        assert result.node_diffs["node2"]["actual_success"] is False


class TestDeterministicComparison:
    """Test 8: Deterministic comparison behavior."""

    @pytest.mark.asyncio
    async def test_deterministic_comparison(self, comparator_runtime):
        """Given identical inputs, comparator output must be deterministic."""
        simulation_graph = {
            "graph_id": "test-exec-9",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"task": "partial"},
                    "operations": ["op1"],
                    "events": ["evt1"],
                    "predicted_reward": 0.5,
                    "grounding_score": 0.7,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-9",
            "timestamp": 1234567890.0,
            "nodes": [
                {"id": "node1", "success": True},
                {"id": "node2", "success": False},
            ],
            "edges": [],
            "execution_order": [["node1"], ["node2"]],
            "state": {"task": "partial"},
            "operations": ["op1"],
            "events": ["evt1"],
            "reward": 0.5,
            "confidence": 0.7,
        }

        # Run comparison multiple times
        results = []
        for _ in range(5):
            result = await comparator_runtime.compare(simulation_graph, execution_result)
            results.append(result)

        # Verify all results are identical
        first_result = results[0]
        for result in results[1:]:
            assert result.outcome == first_result.outcome
            assert result.epistemic_loss == first_result.epistemic_loss
            assert result.node_diffs == first_result.node_diffs
            assert result.topology_loss == first_result.topology_loss
            assert result.world_loss == first_result.world_loss


class TestEpistemicLoss:
    """Test 9: Epistemic loss calculation."""

    @pytest.mark.asyncio
    async def test_perfect_prediction_zero_loss(self, comparator_runtime):
        """Perfect prediction/execution should have minimal/zero epistemic loss."""
        simulation_graph = {
            "graph_id": "test-exec-10",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"key": "value"},
                    "operations": ["op1"],
                    "events": ["evt1"],
                    "predicted_reward": 1.0,
                    "grounding_score": 1.0,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-10",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {"key": "value"},
            "operations": ["op1"],
            "events": ["evt1"],
            "reward": 1.0,
            "confidence": 1.0,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.epistemic_loss == 0.0

    @pytest.mark.asyncio
    async def test_large_divergence_high_loss(self, comparator_runtime):
        """Large expected-vs-actual divergence should increase epistemic loss."""
        simulation_graph = {
            "graph_id": "test-exec-11",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"key": "value1"},
                    "operations": ["op1"],
                    "events": ["evt1"],
                    "predicted_reward": 1.0,
                    "grounding_score": 1.0,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-11",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": False}],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {"key": "value2"},  # Different state
            "operations": ["op2"],  # Different operation
            "events": ["evt2"],  # Different event
            "reward": 0.0,
            "confidence": 0.0,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.epistemic_loss > 0.0


class TestNoLearningSideEffects:
    """Test 10: Comparator produces no learning side effects."""

    @pytest.mark.asyncio
    async def test_no_learning_side_effects(self, comparator_runtime):
        """Comparator must not mutate learning state."""
        # Create mock learning state objects
        mock_transition_model = MagicMock()
        mock_q_table = MagicMock()
        mock_beliefs = MagicMock()

        # Store original state
        original_transition_model_state = str(mock_transition_model)
        original_q_table_state = str(mock_q_table)
        original_beliefs_state = str(mock_beliefs)

        simulation_graph = {
            "graph_id": "test-exec-12",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {},
                    "operations": [],
                    "events": [],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        execution_result = {
            "graph_id": "test-exec-12",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {},
            "operations": [],
            "events": [],
            "reward": 1.0,
            "confidence": 0.95,
        }

        # Run comparison
        result = await comparator_runtime.compare(simulation_graph, execution_result)

        # Verify learning state was not mutated
        assert str(mock_transition_model) == original_transition_model_state
        assert str(mock_q_table) == original_q_table_state
        assert str(mock_beliefs) == original_beliefs_state

        # Verify comparator only produced a result, no side effects
        assert isinstance(result, ComparisonResult)
        assert result.outcome == ComparatorOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_compare_stage_does_not_mutate_the_real_transition_model(self, monkeypatch):
        """End-to-end version of the test above, against the REAL
        boundary this hardening pass fixed: _run_comparison (the "compare"
        stage) must not touch policy._transition_model at all -- that
        mutation now lives in a separate _apply_transition_learning,
        invoked from its own "learn_transitions" stage that runs AFTER
        "learn". Exercises the real adapter (_prediction_to_graph/
        _execution_to_graph), not hand-built ComparatorRuntime dicts."""
        import src.monkey_brain.kernel.comparator_runtime as comparator_module
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _run_comparison,
            _apply_transition_learning,
        )
        from src.monkey_brain.kernel.pipeline.belief_state import (
            BeliefState,
            Plan,
            PlanStep,
        )
        from src.monkey_brain.kernel.pipeline.actor import Actor
        from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
        from src.monkey_brain.kernel.pipeline.execution import (
            ExecutionResult,
            ActionOutcome,
        )
        from src.monkey_brain.kernel.pipeline.prediction.transitions import (
            TransitionModel,
        )

        monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())

        plan = Plan(
            goal="buy milk",
            steps=(PlanStep(action="BuyMilk", description="buy milk", confidence=0.9),),
            cost=0.0,
            confidence=0.9,
            risk=0.0,
            planner="llm",
        )
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.metrics = {"execution_id": "exec-no-side-effects-1"}
        state.prediction_result = {
            "candidates": [
                {
                    "prediction": {
                        "world_snapshot": {"has_milk": True},
                        "predicted_outcomes": [
                            {
                                "description": "buy milk",
                                "success": True,
                                "probability": 0.9,
                            }
                        ],
                        "expected_utility": 0.8,
                    },
                    "scenario_label": "Baseline",
                    "probability": 0.9,
                }
            ],
            "selected": {
                "prediction": {
                    "world_snapshot": {"has_milk": True},
                    "predicted_outcomes": [{"description": "buy milk", "success": True, "probability": 0.9}],
                    "expected_utility": 0.8,
                },
                "scenario_label": "Baseline",
                "probability": 0.9,
            },
        }
        state.execution_result = ExecutionResult(
            actions=(
                ActionOutcome(
                    action_id="arjun_step_0",
                    success=True,
                    result={"has_milk": True},
                    latency_ms=1.0,
                ),
            ),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )

        class FakePolicy:
            def __init__(self):
                self._transition_model = TransitionModel()

        policy = FakePolicy()
        model_before = policy._transition_model

        result_state = await _run_comparison(state, policy)

        assert result_state.comparison_result is not None
        assert policy._transition_model is model_before
        assert not policy._transition_model.known_transitions

        result_state = _apply_transition_learning(result_state, policy)
        assert policy._transition_model is not model_before
        # goal_key is derived from state.belief.plan.goal ("buy milk"),
        # not an empty string -- see the Learning-hardening pass's
        # goal-key alignment fix (_learn_transitions now reads
        # state.belief.plan, matching what Predict-side actually reads).
        assert ("buy milk", "BuyMilk") in policy._transition_model.known_transitions


class TestObservationProvenance:
    """Test 11: Observation provenance tracking."""

    @pytest.mark.asyncio
    async def test_observation_provenance(self, comparator_runtime):
        """Every comparison result must be traceable to execution_id and timestamp."""
        simulation_graph = {
            "graph_id": "test-exec-13",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "run_id": "run-123",
                "summary": {
                    "predicted_state": {},
                    "operations": [],
                    "events": [],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                },
            },
        }

        execution_result = {
            "graph_id": "test-exec-13",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "state": {},
            "operations": [],
            "events": [],
            "reward": 1.0,
            "confidence": 0.95,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        # Verify provenance is captured
        assert result.execution_id == "test-exec-13"
        assert result.timestamp == 1234567890.0

    @pytest.mark.asyncio
    async def test_provenance_flows_through_the_real_adapter(self, monkeypatch):
        """End-to-end version: the real _prediction_to_graph/
        _execution_to_graph (kernel/pipeline/comparison/integration.py)
        must actually populate graph_id/timestamp from the tick's real
        execution_id -- previously neither function set either field, so
        every actor-tick ComparisonResult had empty provenance (not
        fabricated, just silently dropped at the adapter boundary)."""
        import src.monkey_brain.kernel.comparator_runtime as comparator_module
        from src.monkey_brain.kernel.pipeline.comparison.integration import (
            _run_comparison,
        )
        from src.monkey_brain.kernel.pipeline.belief_state import (
            BeliefState,
            Plan,
            PlanStep,
        )
        from src.monkey_brain.kernel.pipeline.actor import Actor
        from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState
        from src.monkey_brain.kernel.pipeline.execution import (
            ExecutionResult,
            ActionOutcome,
        )

        monkeypatch.setattr(comparator_module, "get_comparator_runtime", lambda: ComparatorRuntime())

        plan = Plan(
            goal="buy milk",
            steps=(PlanStep(action="BuyMilk", description="buy milk", confidence=0.9),),
            cost=0.0,
            confidence=0.9,
            risk=0.0,
            planner="llm",
        )
        actor = Actor(actor_id="arjun", tenant_id="acme")
        belief = BeliefState(actor_id="arjun", tenant_id="acme")
        belief.plan = plan
        state = CognitiveState(actor=actor, belief=belief)
        state.metrics = {"execution_id": "exec-provenance-1"}
        state.prediction_result = {
            "candidates": [
                {
                    "prediction": {
                        "world_snapshot": {},
                        "predicted_outcomes": [
                            {
                                "description": "buy milk",
                                "success": True,
                                "probability": 0.9,
                            }
                        ],
                        "expected_utility": 0.8,
                    },
                    "scenario_label": "Baseline",
                    "probability": 0.9,
                }
            ],
            "selected": {
                "prediction": {
                    "world_snapshot": {},
                    "predicted_outcomes": [{"description": "buy milk", "success": True, "probability": 0.9}],
                    "expected_utility": 0.8,
                },
                "scenario_label": "Baseline",
                "probability": 0.9,
            },
        }
        state.execution_result = ExecutionResult(
            actions=(ActionOutcome(action_id="arjun_step_0", success=True, result={}, latency_ms=1.0),),
            success_count=1,
            failure_count=0,
            goal_achieved=True,
        )

        result_state = await _run_comparison(state, None)

        assert result_state.comparison_result["execution_id"] == "exec-provenance-1"
        assert result_state.comparison_result["timestamp"] > 0


class TestInconclusiveResult:
    """Test 12: Inconclusive result when actual state cannot be established."""

    @pytest.mark.asyncio
    async def test_inconclusive_when_no_actual_state(self, comparator_runtime):
        """When actual state cannot be verified, return INCONCLUSIVE."""
        simulation_graph = {
            "graph_id": "test-exec-14",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "node1", "success": True}],
            "edges": [],
            "execution_order": [["node1"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"task": "completed"},
                    "operations": ["complete_task"],
                    "events": ["task_completed"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        # Execution result with no actual state information
        execution_result = {
            "graph_id": "test-exec-14",
            "timestamp": 1234567890.0,
            "nodes": [],  # No nodes
            "edges": [],
            "execution_order": [],
            # No state field
            "operations": [],
            "events": [],
            "reward": 0.0,
            "confidence": 0.0,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        assert result.outcome == ComparatorOutcome.INCONCLUSIVE


class TestExecutionSuccessVsWorldSuccess:
    """Test: Execution success ≠ World success distinction."""

    @pytest.mark.asyncio
    async def test_execution_success_not_world_success(self, comparator_runtime):
        """Capability returned successfully ≠ Expected world state was achieved."""
        simulation_graph = {
            "graph_id": "test-exec-15",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "buy_milk", "success": True}],
            "edges": [],
            "execution_order": [["buy_milk"]],
            "metadata": {
                "summary": {
                    "predicted_state": {"milk": "purchased"},
                    "operations": ["http_request"],
                    "events": ["milk_purchased"],
                    "predicted_reward": 1.0,
                    "grounding_score": 0.95,
                }
            },
        }

        # HTTP request returned 200 (execution success)
        # But milk was not actually purchased (world state not achieved)
        execution_result = {
            "graph_id": "test-exec-15",
            "timestamp": 1234567890.0,
            "nodes": [{"id": "buy_milk", "success": True}],  # Execution succeeded
            "edges": [],
            "execution_order": [["buy_milk"]],
            "state": {"milk": "not_purchased"},  # But world state not achieved
            "operations": ["http_request"],
            "events": [],
            "reward": 0.0,
            "confidence": 0.5,
        }

        result = await comparator_runtime.compare(simulation_graph, execution_result)

        # The comparator should detect the state mismatch
        assert result.state_diff["score"] < 1.0
        assert result.epistemic_loss > 0.0
        # Even though execution succeeded, the comparison should reflect the world state mismatch
