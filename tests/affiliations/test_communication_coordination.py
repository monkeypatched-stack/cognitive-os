from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile
from src.monkey_brain.kernel.society.runtime import SocietyRuntime


def _affiliation(actor, target, *, kind="employment"):
    actor.affiliations.add(
        Affiliation(
            affiliation_id=f"{actor.entity_id}-{target}",
            affiliation_type=kind,
            target_id=target,
            target_name=target,
            permissions=("communicate",),
        )
    )


def test_messages_route_through_society_and_affiliation():
    runtime = SocietyRuntime()
    warehouse = runtime.register_actor(ActorProfile(identity=ActorIdentity(actor_id="warehouse")))
    packer = runtime.register_actor(ActorProfile(identity=ActorIdentity(actor_id="packer")))
    _affiliation(warehouse.actor, "warehouse-team")
    _affiliation(packer.actor, "warehouse-team")

    assert runtime.send_message("warehouse", "packer", "request", {"message": "Pack order 123"})
    assert runtime.get_messages_for("packer")[0]["routing"]["affiliation_id"] == "warehouse-team"
    assert runtime.communication_audit()[-1].allowed


def test_unauthorized_or_unknown_recipient_is_denied_and_broadcast_is_scoped():
    runtime = SocietyRuntime()
    sender = runtime.register_actor(ActorProfile(identity=ActorIdentity(actor_id="sender")))
    recipient = runtime.register_actor(ActorProfile(identity=ActorIdentity(actor_id="recipient")))
    outsider = runtime.register_actor(ActorProfile(identity=ActorIdentity(actor_id="outsider")))
    _affiliation(sender.actor, "warehouse-team")
    _affiliation(recipient.actor, "warehouse-team")
    _affiliation(outsider.actor, "other-team")

    assert runtime.send_message("sender", "missing", "request") is False
    assert runtime.broadcast_message("sender", "stop") == 1
    assert {m["to"] for m in runtime._message_queue} == {"recipient"}
