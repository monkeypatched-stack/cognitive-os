"""Regression: SocietyRuntime._publish_message_interaction misattributed
an AskActor/DelegateTask received answer to the asker instead of the
actual responder.

Confirmed live: "Ask Raj if he can pick up milk" (a plan-driven AskActor
step) produced TWO INTERACTION events in the execution's conversation --
AskActorCapability's own correct one ("Priya Sharma asked Raj Sharma:
...") plus a second, wrong one from this generic tick-event publisher
("<priya's raw actor_id>: Sure! I can pick up milk...") attributing
Raj's real answer to Priya. Only RespondToInquiryCapability/
BroadcastToAffiliationCapability results (no target_actor key) mean "I,
the ticking actor, said this" -- AskActor/DelegateTask results always
carry target_actor precisely because the content came from someone else.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.runtime import SocietyRuntime


def _actions_result(**result_fields):
    return [{"action_id": "a1", "success": True, "result": result_fields}]


class _FakeTickResult:
    def __init__(self, actions):
        self.actions = actions
        self.observations = None
        self.belief_updated = False
        self.learned = False
        self.predicted_outcome = None


def test_ask_actor_answer_is_not_republished_as_the_askers_own_message():
    sr = SocietyRuntime()
    actions = _actions_result(
        success=True,
        target_actor="raj",
        question="Can you pick up milk?",
        answer="Sure, I'll grab a gallon on the way home.",
    )
    sr._publish_tick_events("priya", _FakeTickResult(actions))

    interactions = sr.context_stream.replay(event_type=ContextEventType.INTERACTION, actor_id="priya")
    # No event from THIS generic publisher should attribute Raj's answer
    # to Priya -- if AskActorCapability itself had published its own
    # event (as it does live), that's a separate call site this test
    # doesn't exercise; this test isolates _publish_message_interaction
    # specifically.
    assert not any(
        e.payload and e.payload.get("answer") == "Sure, I'll grab a gallon on the way home." for e in interactions
    )


def test_respond_to_inquiry_answer_is_still_published_as_the_actors_own_message():
    """The fix must not silently break the real, intended case: an actor
    genuinely answering an inquiry directed at them (no target_actor —
    they ARE the one speaking) still gets published."""
    sr = SocietyRuntime()
    actions = _actions_result(success=True, answer="Yes, we have oat milk in stock.")
    sr._publish_tick_events("bob", _FakeTickResult(actions))

    interactions = sr.context_stream.replay(event_type=ContextEventType.INTERACTION, actor_id="bob")
    assert any(e.payload and e.payload.get("answer") == "Yes, we have oat milk in stock." for e in interactions)


def test_broadcast_message_is_still_published_as_the_actors_own_message():
    sr = SocietyRuntime()
    actions = _actions_result(success=True, message="Milk is on sale this week!", recipients=["alice", "bob"])
    sr._publish_tick_events("priya", _FakeTickResult(actions))

    interactions = sr.context_stream.replay(event_type=ContextEventType.INTERACTION, actor_id="priya")
    assert any(e.payload and e.payload.get("message") == "Milk is on sale this week!" for e in interactions)


def test_delegate_task_result_is_not_republished_as_the_delegators_own_message():
    sr = SocietyRuntime()
    actions = _actions_result(
        success=True,
        target_actor="raj",
        answer="Done -- order placed.",
        success_count=3,
        failure_count=0,
    )
    sr._publish_tick_events("priya", _FakeTickResult(actions))

    interactions = sr.context_stream.replay(event_type=ContextEventType.INTERACTION, actor_id="priya")
    assert not any(e.payload and e.payload.get("answer") == "Done -- order placed." for e in interactions)
