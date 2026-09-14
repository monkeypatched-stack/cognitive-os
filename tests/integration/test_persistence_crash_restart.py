"""Integration test: crash/restart survival for the drone use case's
persistence layer — idempotency, execution checkpoints, current plan,
approvals, negotiation, payment, learning events, transition-model, and
belief-state.

A recent drone-use-case run found Redis AND Mongo unreachable throughout,
so every one of these stores silently fell back to a process-local
mechanism (or, for the Redis-only ones with no fallback at all, silently
dropped writes) that a real restart would wipe (see run_store.py:446,
api/idempotency.py:338, execution_checkpoint_store.py:60,
current_plan_store.py:59, approval_store.py:59, negotiation_store.py:49,
payment_store.py:75, learning_event_store.py:62, and
prediction/persistence.py:61 for the exact Redis fallback log lines; the
Mongo-backed belief store degrades the same way per
kernel/society/integration.py:3526). This test proves the other half of
the claim: with real Redis and Mongo actually reachable (this suite's
shared Redis, flushed before every test by conftest.py::_flush_shared_redis
— the same instance IdempotencyStore/RunStore/PlanetaryRuntime all connect
to per that fixture's own docstring), each of these stores' save/load
calls goes straight to the real backend on every call, never a
Python-level cache of the saved VALUE — so a call made by "process A" and
a call made by "process B" (a restarted service) see identical state.

Unlike TestActorRestartRehydration (test_actor_persistence_roundtrip.py),
which must construct a brand-new PlanetaryRuntime() to force a real reload
(PlanetaryRuntime caches actors/societies in memory across ticks), these
modules hold no such value cache to reset — every save/load already hits
Redis/Mongo directly — so "process A" / "process B" below are just two
calls to the same module-level functions/singleton, exactly like a literal
second OS process would make.

The one place that distinction matters: IdempotencyStore has a WORKING
in-memory fallback backend (unlike the Redis-only stores, which simply
no-op when Redis is down) — its reserve()/complete()/duplicate-detection
semantics are identical whether backed by Redis or by the in-memory dict,
so a naive round-trip here would still pass even if Redis were
unreachable, proving nothing about real persistence. The backend-type
assertion in TestIdempotencySurvivesRestart exists specifically to catch
that silent-fallback case instead of reporting a false pass.

The belief-state test below exercises ActorStateStore.save/load directly
(Mongo), not the full PlanetaryRuntime.checkpoint_actor_belief/
restore_actor_belief path — that path needs a real registered
actor+society, which is out of scope for a persistence-layer-focused
test; the guarantee that matters here is whether ActorStateStore's own
Mongo round-trip survives a restart, which is exactly what silently broke
during the drone run.
"""

from __future__ import annotations

import uuid

from src.monkey_brain.api.idempotency import (
    _RedisIdempotencyBackend,
    get_idempotency_store,
    request_fingerprint,
)
from src.monkey_brain.kernel.pipeline.execution_checkpoint_store import (
    load_execution_checkpoint,
    save_execution_checkpoint,
)
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    CurrentPlanRecord,
    load_current_plan,
    save_current_plan,
)


class TestIdempotencySurvivesRestart:
    def test_duplicate_command_after_restart_replays_cached_result_not_reexecuted(self):
        """The guarantee the drone-run finding flagged as most important:
        a drone command retried after a restart must return the FIRST
        attempt's result, not execute a second time."""
        run_id = uuid.uuid4().hex[:8]
        key = f"drone-1:execute-mission-step-3:{run_id}"
        request_hash = request_fingerprint("POST", "/actors/drone-1/execute", {"step": 3})

        store_a = get_idempotency_store()  # "process A", before the crash
        assert isinstance(store_a._backend, _RedisIdempotencyBackend), (
            "IdempotencyStore is running on the in-memory fallback, not real "
            "Redis — this test would pass even during an outage and prove "
            "nothing; bring Redis up (`docker compose up -d redis`) first"
        )
        claimed, existing = store_a.reserve(key, request_hash)
        assert claimed and existing is None, "expected a fresh reservation"
        store_a.complete(key, request_hash, {"status": "executed", "step": 3})

        store_b = get_idempotency_store()  # "process B", after the restart
        claimed_again, existing_after_restart = store_b.reserve(key, request_hash)
        assert claimed_again is False, (
            "the same Idempotency-Key was claimed a SECOND time after a "
            "restart — a retried drone command would execute twice"
        )
        assert existing_after_restart is not None
        assert existing_after_restart.response_body == {"status": "executed", "step": 3}


class TestExecutionCheckpointSurvivesRestart:
    def test_checkpoint_saved_before_crash_is_loaded_after_restart(self):
        run_id = uuid.uuid4().hex[:8]
        execution_id = f"drone-1:mission-step-3:{run_id}"
        plan = {
            "goal": "deliver-payload",
            "steps": ["takeoff", "navigate", "drop-payload"],
        }
        completed_steps = {0: {"action_id": "takeoff", "success": True}}

        ok = save_execution_checkpoint(execution_id, plan, completed_steps)  # process A
        assert ok, (
            "save_execution_checkpoint returned False — Redis unreachable? "
            "bring it up (`docker compose up -d redis`) first"
        )

        checkpoint = load_execution_checkpoint(execution_id)  # process B, after restart
        assert checkpoint is not None, "execution checkpoint did not survive a restart"
        assert checkpoint.plan == plan
        assert checkpoint.completed_steps == {"0": {"action_id": "takeoff", "success": True}}


class TestCurrentPlanSurvivesRestart:
    def test_plan_saved_before_crash_is_loaded_after_restart(self):
        run_id = uuid.uuid4().hex[:8]
        actor_id = f"drone-1:{run_id}"
        goal_key = "deliver-payload"
        record = CurrentPlanRecord(
            plan_id=f"plan-{run_id}",
            actor_id=actor_id,
            goal=goal_key,
            steps=("takeoff", "navigate", "drop-payload"),
        )

        ok = save_current_plan(actor_id, goal_key, record)  # process A
        assert ok, (
            "save_current_plan returned False — Redis unreachable? bring it up (`docker compose up -d redis`) first"
        )

        loaded = load_current_plan(actor_id, goal_key)  # process B, after restart
        assert loaded is not None, "current plan did not survive a restart"
        assert loaded.plan_id == record.plan_id
        assert loaded.goal == goal_key
        assert loaded.steps == record.steps


class TestApprovalSurvivesRestart:
    def test_pending_approval_saved_before_crash_is_loaded_after_restart(self):
        from src.monkey_brain.kernel.pipeline.approval_store import (
            PendingApproval,
            load_pending_approval,
            save_pending_approval,
        )

        run_id = uuid.uuid4().hex[:8]
        execution_id = f"drone-1:mission-step-3:approval:{run_id}"
        approval = PendingApproval(
            execution_id=execution_id,
            actor_id=f"drone-1:{run_id}",
            step_index=2,
            capability="drone.approach_restricted_zone",
            proposed_action={"zone": "restricted-alpha"},
            reason="requires human sign-off before entering restricted airspace",
        )

        ok = save_pending_approval(approval)  # process A
        assert ok, (
            "save_pending_approval returned False — Redis unreachable? bring it up (`docker compose up -d redis`) first"
        )

        loaded = load_pending_approval(execution_id)  # process B, after restart
        assert loaded is not None, "pending approval did not survive a restart"
        assert loaded.capability == "drone.approach_restricted_zone"
        assert loaded.proposed_action == {"zone": "restricted-alpha"}
        assert loaded.decided is None


class TestNegotiationSurvivesRestart:
    def test_pending_negotiation_saved_before_crash_is_loaded_after_restart(self):
        from src.monkey_brain.kernel.pipeline.negotiation_store import (
            PendingNegotiation,
            load_pending_negotiation,
            save_pending_negotiation,
        )

        run_id = uuid.uuid4().hex[:8]
        execution_id = f"drone-1:mission-step-3:negotiation:{run_id}"
        negotiation = PendingNegotiation(
            execution_id=execution_id,
            actor_id=f"drone-1:{run_id}",
            step_index=1,
            capability="drone.share_airspace",
            proposed_transition={"altitude_band": "120-150m"},
            counterparties=["drone-2"],
            reason="airspace overlap with drone-2's planned route",
        )

        ok = save_pending_negotiation(negotiation)  # process A
        assert ok, (
            "save_pending_negotiation returned False — Redis unreachable? "
            "bring it up (`docker compose up -d redis`) first"
        )

        loaded = load_pending_negotiation(execution_id)  # process B, after restart
        assert loaded is not None, "pending negotiation did not survive a restart"
        assert loaded.proposed_transition == {"altitude_band": "120-150m"}
        assert loaded.counterparties == ["drone-2"]
        assert loaded.decided is None


class TestPaymentSurvivesRestart:
    def test_pending_payment_saved_before_crash_is_loaded_after_restart(self):
        from src.monkey_brain.kernel.pipeline.payment_store import (
            PendingPayment,
            load_pending_payment,
            load_pending_payment_by_reservation,
            save_pending_payment,
        )

        run_id = uuid.uuid4().hex[:8]
        execution_id = f"drone-1:mission-step-3:payment:{run_id}"
        reservation_id = f"resv-{run_id}"
        payment = PendingPayment(
            execution_id=execution_id,
            actor_id=f"drone-1:{run_id}",
            step_index=0,
            capability="drone.pay_landing_fee",
            provider_name="upi_reserve_pay",
            reservation_id=reservation_id,
            payer_ref="merchant-landing-pad-1",
            amount=49.0,
        )

        ok = save_pending_payment(payment)  # process A
        assert ok, (
            "save_pending_payment returned False — Redis unreachable? bring it up (`docker compose up -d redis`) first"
        )

        loaded = load_pending_payment(execution_id)  # process B, after restart
        assert loaded is not None, "pending payment did not survive a restart"
        assert loaded.reservation_id == reservation_id
        assert loaded.amount == 49.0
        assert loaded.decided is None

        # The real caller (a PSP webhook) only ever knows the
        # reservation_id, not the execution_id — the index must also
        # survive the restart, not just the primary record.
        by_reservation = load_pending_payment_by_reservation(reservation_id)
        assert by_reservation is not None, "reservation_id index did not survive a restart"
        assert by_reservation.execution_id == execution_id


class TestLearningEventsSurviveRestart:
    def test_learning_event_recorded_before_crash_is_loaded_after_restart(self):
        from src.monkey_brain.kernel.pipeline.learning_event_store import (
            LearningEvent,
            load_learning_events_for_actor,
            load_learning_events_for_execution,
            record_learning_event,
        )

        run_id = uuid.uuid4().hex[:8]
        execution_id = f"drone-1:mission-step-3:learn:{run_id}"
        actor_id = f"drone-1:{run_id}"
        event = LearningEvent(
            execution_id=execution_id,
            actor_id=actor_id,
            goal_key="deliver-payload",
            action_key="navigate",
            success=True,
            previous=None,
            updated={"probability": 0.9},
        )

        ok = record_learning_event(event)  # process A
        assert ok, (
            "record_learning_event returned False — Redis unreachable? bring it up (`docker compose up -d redis`) first"
        )

        by_execution = load_learning_events_for_execution(execution_id)  # process B
        assert len(by_execution) == 1, "learning event did not survive a restart"
        assert by_execution[0].action_key == "navigate"
        assert by_execution[0].updated == {"probability": 0.9}

        by_actor = load_learning_events_for_actor(actor_id)
        assert len(by_actor) == 1
        assert by_actor[0].execution_id == execution_id


class TestTransitionModelSurvivesRestart:
    def test_transition_model_saved_before_crash_is_loaded_after_restart(self):
        from src.monkey_brain.kernel.pipeline.prediction.persistence import (
            load_transition_model,
            save_actor_meta,
            save_transition_model,
        )
        from src.monkey_brain.kernel.pipeline.prediction.transitions import (
            TransitionKind,
            TransitionModel,
            WorldTransition,
        )

        run_id = uuid.uuid4().hex[:8]
        actor_id = f"drone-1:{run_id}"
        model = TransitionModel(
            known_transitions={
                ("deliver-payload", "navigate"): (
                    WorldTransition(
                        action="navigate",
                        kind=TransitionKind.PROBABILISTIC,
                        probability=0.9,
                        confidence=0.7,
                    ),
                ),
            }
        )

        ok = save_transition_model(actor_id, model)  # process A
        assert ok, (
            "save_transition_model returned False — Redis unreachable? bring it up (`docker compose up -d redis`) first"
        )
        save_actor_meta(actor_id, "Drone One")

        loaded = load_transition_model(actor_id)  # process B, after restart
        assert loaded is not None, "transition model did not survive a restart"
        assert ("deliver-payload", "navigate") in loaded.known_transitions
        learned = loaded.known_transitions[("deliver-payload", "navigate")][0]
        assert learned.probability == 0.9
        assert learned.confidence == 0.7


class TestBeliefStateSurvivesRestart:
    def test_belief_state_saved_before_crash_is_loaded_after_restart(self):
        import json
        from datetime import datetime

        from src.monkey_brain.persistence.actor_state_store import (
            ActorStateStore,
            PersistedActorState,
        )
        from src.monkey_brain.persistence.db_pool import get_db_pool

        run_id = uuid.uuid4().hex[:8]
        actor_id = f"drone-1:{run_id}"
        tenant_id = "default"
        belief_payload = {"goal": "deliver-payload", "confidence": 0.8}

        store_a = ActorStateStore(get_db_pool())  # process A
        state = PersistedActorState(
            actor_id=actor_id,
            tenant_id=tenant_id,
            belief_state=json.dumps(belief_payload).encode(),
            bellman_policy=b"",
            phi_compiled=b"",
            memory_kv={},
            last_updated=datetime.now().isoformat(),
            version=1,
        )
        store_a.save(state)

        store_b = ActorStateStore(get_db_pool())  # process B, after restart
        loaded = store_b.load(actor_id, tenant_id)
        assert loaded is not None, "belief state did not survive a restart"
        assert json.loads(loaded.belief_state.decode()) == belief_payload
