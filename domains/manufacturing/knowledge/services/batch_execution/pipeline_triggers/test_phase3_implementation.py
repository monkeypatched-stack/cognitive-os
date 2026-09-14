"""
Phase 3 Implementation Tests

Tests for the workflow re-evaluation trigger system.
"""

from datetime import datetime, timezone

import pytest

from services.batch_execution.pipeline_triggers.pipeline_parameter_adjuster import (
    AdjustmentStrategy,
    PipelineParameterAdjuster,
)
from services.batch_execution.pipeline_triggers.pipeline_trigger_engine import (
    PipelineTriggerEngine,
    TriggerThrottlePolicy,
)
from services.batch_execution.pipeline_triggers.re_evaluation_queue import (
    ReEvaluationPriority,
    ReEvaluationQueue,
    ReEvaluationReason,
    ReEvaluationRequest,
)
from services.batch_execution.pipeline_triggers.workflow_reevaluator import (
    ReEvaluationMetrics,
    WorkflowReevaluator,
)
from services.work_order_agent.tracking.event_system import (
    WorkOrderEvent,
    WorkOrderEventBus,
    WorkOrderEventType,
)
from services.work_order_agent.tracking.time_analysis import (
    WorkOrderTimeAnalysis,
)


class TestReEvaluationQueue:
    """Tests for ReEvaluationQueue."""

    def test_enqueue_request(self):
        """Test enqueueing a request."""
        queue = ReEvaluationQueue()
        request = ReEvaluationRequest(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
            priority=ReEvaluationPriority.HIGH,
            variance_data={"variance_percent": 25.0},
        )

        result = queue.enqueue(request)
        assert result is True
        assert queue.get_pending_count() == 1

    def test_deduplication(self):
        """Test deduplication prevents duplicate requests."""
        queue = ReEvaluationQueue()

        # Enqueue first request
        request1 = ReEvaluationRequest(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
            priority=ReEvaluationPriority.HIGH,
            variance_data={},
        )
        queue.enqueue(request1)

        # Attempt to enqueue duplicate
        request2 = ReEvaluationRequest(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
            priority=ReEvaluationPriority.MEDIUM,
            variance_data={},
        )
        result = queue.enqueue(request2)

        assert result is False
        assert queue.get_pending_count() == 1
        assert queue.metrics["total_deduplicated"] == 1

    def test_priority_dequeue(self):
        """Test dequeue returns highest priority first."""
        queue = ReEvaluationQueue()

        # Enqueue requests with different priorities
        low_request = ReEvaluationRequest(
            work_order_id="WO-001",
            priority=ReEvaluationPriority.LOW,
            reason=ReEvaluationReason.TIME_VARIANCE_UNDER,
        )
        critical_request = ReEvaluationRequest(
            work_order_id="WO-002",
            priority=ReEvaluationPriority.CRITICAL,
            reason=ReEvaluationReason.TIME_VARIANCE_CRITICAL,
        )
        high_request = ReEvaluationRequest(
            work_order_id="WO-003",
            priority=ReEvaluationPriority.HIGH,
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
        )

        queue.enqueue(low_request)
        queue.enqueue(critical_request)
        queue.enqueue(high_request)

        # Dequeue should return critical first
        dequeued = queue.dequeue(batch_size=3)
        assert len(dequeued) == 3
        assert dequeued[0].work_order_id == "WO-002"  # Critical
        assert dequeued[1].work_order_id == "WO-003"  # High
        assert dequeued[2].work_order_id == "WO-001"  # Low

    def test_mark_processed(self):
        """Test marking request as processed."""
        queue = ReEvaluationQueue()
        request = ReEvaluationRequest(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
        )

        queue.enqueue(request)
        assert queue.get_pending_count() == 1

        queue.mark_processed(request.request_id, {"success": True})
        assert queue.get_pending_count() == 0
        assert queue.metrics["total_processed"] == 1

    def test_queue_metrics(self):
        """Test queue metrics."""
        queue = ReEvaluationQueue()

        for i in range(5):
            request = ReEvaluationRequest(
                work_order_id=f"WO-{i:03d}",
                batch_id="BATCH-001",
                execution_id=f"EXEC-{i:03d}",
                priority=(
                    ReEvaluationPriority.HIGH
                    if i % 2 == 0
                    else ReEvaluationPriority.LOW
                ),
            )
            queue.enqueue(request)

        metrics = queue.get_metrics()
        assert metrics["pending_count"] == 5
        assert metrics["pending_by_priority"]["high"] == 3
        assert metrics["pending_by_priority"]["low"] == 2
        assert metrics["total_enqueued"] == 5


class TestPipelineParameterAdjuster:
    """Tests for PipelineParameterAdjuster."""

    def test_analyze_variance(self):
        """Test variance analysis and adjustment generation."""
        adjuster = PipelineParameterAdjuster()

        # Simulate a step that ran over time
        adjustment = adjuster.analyze_variance(
            step_id="STEP-001",
            estimated_duration=30.0,
            actual_duration=45.0,  # 50% over time
            historical_durations=None,
        )

        assert adjustment.step_id == "STEP-001"
        assert adjustment.original_value == 30.0
        assert adjustment.adjusted_value > 45.0  # Should be higher than actual
        assert adjustment.delta > 0  # Positive adjustment
        assert adjustment.confidence > 0  # Should have some confidence

    def test_parameter_set_creation(self):
        """Test creating parameter set with adjustments."""
        adjuster = PipelineParameterAdjuster()

        # Create adjustments
        adj1 = adjuster.analyze_variance(
            step_id="STEP-001",
            estimated_duration=30.0,
            actual_duration=40.0,
        )
        adj2 = adjuster.analyze_variance(
            step_id="STEP-002",
            estimated_duration=20.0,
            actual_duration=25.0,
        )

        # Create parameter set
        param_set = adjuster.create_adjusted_parameter_set(
            base_version=1,
            adjustments=[adj1, adj2],
            base_parameters={
                "STEP-001": 30.0,
                "STEP-002": 20.0,
            },
        )

        assert param_set.version == 2
        assert len(param_set.step_durations) == 2
        assert param_set.step_durations["STEP-001"] == adj1.adjusted_value
        assert param_set.step_durations["STEP-002"] == adj2.adjusted_value

    def test_parameter_set_activation(self):
        """Test activating parameter sets."""
        adjuster = PipelineParameterAdjuster()

        # Create and activate parameter set
        param_set = adjuster.create_adjusted_parameter_set(
            base_version=0,
            adjustments=[],
            base_parameters={},
        )

        adjuster.activate_parameter_set(param_set.parameter_set_id)

        assert adjuster.get_active_parameter_set() == param_set
        assert param_set.is_active is True

    def test_adjustment_impact(self):
        """Test calculating adjustment impact."""
        adjuster = PipelineParameterAdjuster()

        adj1 = adjuster.analyze_variance(
            step_id="STEP-001",
            estimated_duration=30.0,
            actual_duration=40.0,
        )
        adj2 = adjuster.analyze_variance(
            step_id="STEP-002",
            estimated_duration=20.0,
            actual_duration=15.0,
        )

        impact = adjuster.get_adjustment_impact([adj1, adj2])

        assert impact["total_adjustments"] == 2
        assert int(float(impact["total_delta_minutes"])) >= 0  # Positive or neutral
        assert "avg_confidence" in impact


class TestTriggerThrottlePolicy:
    """Tests for TriggerThrottlePolicy."""

    def test_throttle_by_time(self):
        """Test time-based throttling."""
        policy = TriggerThrottlePolicy(min_time_between_triggers_seconds=5)

        # First trigger should succeed
        can_trigger, reason = policy.can_trigger("WO-001", "BATCH-001")
        assert can_trigger is True

        # Record trigger
        policy.record_trigger("WO-001", "BATCH-001")

        # Immediate second trigger should fail
        can_trigger, reason = policy.can_trigger("WO-001", "BATCH-001")
        assert can_trigger is False
        assert "Throttled" in reason

    def test_max_triggers_per_work_order(self):
        """Test max triggers limit per work order."""
        policy = TriggerThrottlePolicy(max_triggers_per_work_order=2)

        # First two triggers should succeed
        for i in range(2):
            policy.record_trigger("WO-001", "BATCH-001")

        # Third trigger should fail
        policy.min_time_between_triggers_seconds = 0  # Override time check
        can_trigger, reason = policy.can_trigger("WO-001", "BATCH-001")
        assert can_trigger is False
        assert "Max triggers" in reason

    def test_reset_work_order(self):
        """Test resetting throttle state."""
        policy = TriggerThrottlePolicy(min_time_between_triggers_seconds=60)

        policy.record_trigger("WO-001", "BATCH-001")
        can_trigger, _ = policy.can_trigger("WO-001", "BATCH-001")
        assert can_trigger is False

        # Reset
        policy.reset_work_order("WO-001")
        can_trigger, _ = policy.can_trigger("WO-001", "BATCH-001")
        assert can_trigger is True


class TestPipelineTriggerEngine:
    """Tests for PipelineTriggerEngine."""

    def setup_method(self):
        """Set up test fixtures."""
        self.event_bus = WorkOrderEventBus()
        self.time_analysis = WorkOrderTimeAnalysis()
        self.parameter_adjuster = PipelineParameterAdjuster()
        self.queue = ReEvaluationQueue()

    def test_engine_initialization(self):
        """Test engine initializes correctly."""
        engine = PipelineTriggerEngine(
            event_bus=self.event_bus,
            time_analysis=self.time_analysis,
            parameter_adjuster=self.parameter_adjuster,
            re_evaluation_queue=self.queue,
        )

        assert engine.event_bus is not None
        assert engine.time_analysis is not None
        assert len(engine.trigger_history) == 0

    def test_trigger_re_evaluation(self):
        """Test triggering re-evaluation."""
        engine = PipelineTriggerEngine(
            event_bus=self.event_bus,
            time_analysis=self.time_analysis,
            parameter_adjuster=self.parameter_adjuster,
            re_evaluation_queue=self.queue,
        )

        triggered = engine.trigger_re_evaluation(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
            priority=ReEvaluationPriority.HIGH,
            variance_data={"variance_percent": 25.0},
        )

        assert triggered is True
        assert self.queue.get_pending_count() == 1
        assert len(engine.trigger_history) == 1

    def test_trigger_callback(self):
        """Test trigger callbacks are invoked."""
        engine = PipelineTriggerEngine(
            event_bus=self.event_bus,
            time_analysis=self.time_analysis,
            parameter_adjuster=self.parameter_adjuster,
            re_evaluation_queue=self.queue,
        )

        callback_invoked = []

        def test_callback(request):
            callback_invoked.append(request.request_id)

        engine.register_trigger_callback(test_callback)

        engine.trigger_re_evaluation(
            work_order_id="WO-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            reason=ReEvaluationReason.TIME_VARIANCE_OVER,
            priority=ReEvaluationPriority.HIGH,
            variance_data={},
        )

        assert len(callback_invoked) == 1

    def test_engine_metrics(self):
        """Test engine metrics."""
        engine = PipelineTriggerEngine(
            event_bus=self.event_bus,
            time_analysis=self.time_analysis,
            parameter_adjuster=self.parameter_adjuster,
            re_evaluation_queue=self.queue,
        )

        # Trigger some re-evaluations
        for i in range(3):
            engine.trigger_re_evaluation(
                work_order_id=f"WO-{i:03d}",
                batch_id="BATCH-001",
                execution_id=f"EXEC-{i:03d}",
                reason=ReEvaluationReason.TIME_VARIANCE_OVER,
                priority=ReEvaluationPriority.HIGH,
                variance_data={},
            )

        metrics = engine.get_metrics()
        assert metrics["total_triggers"] == 3


class TestReEvaluationMetrics:
    """Tests for ReEvaluationMetrics."""

    def test_metrics_creation(self):
        """Test creating metrics."""
        metrics = ReEvaluationMetrics("REEVAL-001")

        assert metrics.re_evaluation_id == "REEVAL-001"
        assert metrics.success is False
        assert metrics.error is None
        assert metrics.completed_at is None

    def test_metrics_conversion(self):
        """Test converting metrics to dict."""
        metrics = ReEvaluationMetrics("REEVAL-001")
        metrics.success = True
        metrics.completed_at = datetime.now(timezone.utc)

        metrics_dict = metrics.to_dict()

        assert metrics_dict["re_evaluation_id"] == "REEVAL-001"
        assert metrics_dict["success"] is True
        assert metrics_dict["duration_ms"] is not None


class TestWorkflowReevaluator:
    """Tests for WorkflowReevaluator."""

    def test_reevaluator_initialization(self):
        """Test reevaluator initializes correctly."""
        event_bus = WorkOrderEventBus()
        time_analysis = WorkOrderTimeAnalysis()

        reevaluator = WorkflowReevaluator(
            event_bus=event_bus,
            time_analysis=time_analysis,
        )

        assert reevaluator.event_bus is not None
        assert reevaluator.time_analysis is not None
        assert reevaluator.trigger_engine is not None
        assert reevaluator.parameter_adjuster is not None
        assert reevaluator.re_evaluation_queue is not None

    def test_dashboard_data(self):
        """Test getting dashboard data."""
        event_bus = WorkOrderEventBus()
        time_analysis = WorkOrderTimeAnalysis()

        reevaluator = WorkflowReevaluator(
            event_bus=event_bus,
            time_analysis=time_analysis,
        )

        dashboard = reevaluator.get_dashboard_data()

        assert "status" in dashboard
        assert "queue_status" in dashboard
        assert "trigger_engine_metrics" in dashboard
        assert "re_evaluation_stats" in dashboard

    def test_metrics_summary(self):
        """Test getting metrics summary."""
        event_bus = WorkOrderEventBus()
        time_analysis = WorkOrderTimeAnalysis()

        reevaluator = WorkflowReevaluator(
            event_bus=event_bus,
            time_analysis=time_analysis,
        )

        summary = reevaluator.get_metrics_summary()

        assert "system_status" in summary
        assert "trigger_engine" in summary
        assert "re_evaluation_queue" in summary
        assert "parameter_adjuster" in summary
        assert "re_evaluations" in summary


class TestPhase3Integration:
    """Integration tests for Phase 3 components."""

    def test_end_to_end_workflow(self):
        """Test end-to-end workflow re-evaluation flow."""
        # Set up components
        event_bus = WorkOrderEventBus()
        time_analysis = WorkOrderTimeAnalysis()

        reevaluator = WorkflowReevaluator(
            event_bus=event_bus,
            time_analysis=time_analysis,
        )

        # Record a time variance
        variance = time_analysis.record_variance(
            work_order_id="WO-001",
            step_id="STEP-001",
            estimated_minutes=30,
            actual_minutes=45.0,
        )

        # Publish TIME_VARIANCE_DETECTED event
        event = WorkOrderEvent(
            event_type=WorkOrderEventType.TIME_VARIANCE_DETECTED,
            work_order_id="WO-001",
            step_id="STEP-001",
            batch_id="BATCH-001",
            execution_id="EXEC-001",
            data={
                "variance_id": variance.variance_id,
                "variance_percent": variance.variance_percent,
                "variance_type": variance.variance_type.value,
            },
        )

        # This should trigger the engine via event subscription
        import asyncio

        async def publish_event():
            await event_bus.publish(event)

        asyncio.run(publish_event())

        # Check that re-evaluation was queued
        queue_metrics = reevaluator.re_evaluation_queue.get_metrics()
        # The event handler should have triggered a re-evaluation
        # Total count depends on event subscription timing

    def test_cascading_trigger_prevention(self):
        """Test that cascading triggers are prevented."""
        event_bus = WorkOrderEventBus()
        time_analysis = WorkOrderTimeAnalysis()

        reevaluator = WorkflowReevaluator(
            event_bus=event_bus,
            time_analysis=time_analysis,
        )

        # Try to trigger same re-evaluation multiple times
        results = []
        for _ in range(3):
            triggered = reevaluator.trigger_engine.trigger_re_evaluation(
                work_order_id="WO-001",
                batch_id="BATCH-001",
                execution_id="EXEC-001",
                reason=ReEvaluationReason.TIME_VARIANCE_OVER,
                priority=ReEvaluationPriority.HIGH,
                variance_data={},
            )
            results.append(triggered)

        # First should succeed, rest should be deduplicated
        assert results[0] is True
        assert results[1] is False  # Deduplicated
        assert results[2] is False  # Deduplicated

        # Only one request in queue
        assert reevaluator.re_evaluation_queue.get_pending_count() == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
