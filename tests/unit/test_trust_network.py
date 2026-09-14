"""Cognitive Trust Network — runtimes connect, exchange knowledge under governance.

Validates: relationship→permission policies, trust edges (score + provenance/audit), and
the belief-as-PROPOSAL pipeline — verify → trust → policy → simulate/compare → accept/
reject/quarantine → merge queue → BATCH world update. Nothing enters the world directly.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    BeliefProposal,
    Context,
    KnowledgeExchange,
    Perm,
    Relationship,
    TrustNetwork,
    WorldModelRuntime,
    is_shareable,
)

# ── relationships → permission policies ──────────────────────────────────────────


def test_relationships_grant_different_policies():
    net = TrustNetwork()
    net.connect("alice", "student", Relationship.MENTOR)  # alice mentors a student
    net.connect("alice", "boss", Relationship.COLLEAGUE)
    # mentor shares beliefs/graphs; colleague shares graphs + joint execution but not beliefs
    assert net.permits("alice", "student", Perm.PUBLISH_BELIEFS)
    assert net.permits("alice", "boss", Perm.EXECUTE_JOINTLY)
    assert not net.permits("alice", "boss", Perm.PUBLISH_BELIEFS)


def test_trust_score_and_audit_trail():
    net = TrustNetwork()
    e = net.connect("alice", "bob", Relationship.FAMILY)
    assert e.trust_score == 0.9  # family is high-trust
    net.grant("alice", "bob", Perm.SHARE_EXECUTION_GRAPHS)
    net.revoke("alice", "bob", Perm.SHARE_WORKFLOWS)
    ops = [p[0] for p in net.audit("alice", "bob")]
    # every change is auditable (revoke distinguishes a permission from an edge teardown)
    assert ops == ["connect", "grant", "revoke_permission"]


def test_friendship_is_not_unrestricted():
    net = TrustNetwork()
    net.connect("alice", "acq", Relationship.FRIEND)
    assert net.permits("alice", "acq", Perm.SEND_MESSAGE)
    assert not net.permits("alice", "acq", Perm.SHARE_EXECUTION_GRAPHS)  # friend ≠ full access


# ── never-share classification ───────────────────────────────────────────────────


def test_sensitive_classes_never_share():
    assert is_shareable("workflow", "cooking")
    assert not is_shareable("belief", "journal")
    assert not is_shareable("observation", "health_records")
    assert not is_shareable("belief", "credentials")


# ── belief-as-proposal pipeline ─────────────────────────────────────────────────


def _setup():
    net = TrustNetwork()
    # alice (mentor) may publish beliefs TO bob; bob receives
    net.connect("alice", "bob", Relationship.MENTOR)
    ex = KnowledgeExchange(net)
    return net, ex


def test_publish_blocks_sensitive_knowledge():
    _, ex = _setup()
    assert ex.publish("alice", "belief", "journal", [("A", "B", 1.0)]) is None  # never leaves
    ok = ex.publish("alice", "workflow", "cooking", [("Prep", "Cook", 1.0)])
    assert ok is not None and ok.signature  # signed


def test_accepted_proposal_does_not_enter_world_directly():
    net, ex = _setup()
    wm = WorldModelRuntime()
    bob_belief = wm.new_local_belief()
    prop = ex.publish("alice", "belief", "api", [("Call", "Parse", 1.0)])

    res = ex.deliver(prop, "bob", bob_belief)
    assert res.status == "accepted"
    # NOTHING is in the world yet — it is queued for a batch merge
    assert ex.queue.pending() == 1
    assert wm.summary()["private_transitions"].get("bob", 0) == 0
    # only a BATCH flush applies it (the world's single mutation path)
    applied = ex.queue.flush(wm, tenant_of=lambda r: r)
    assert applied == 1
    assert wm.summary()["private_transitions"]["bob"] == 1


def test_untrusted_origin_rejected():
    net = TrustNetwork()
    net.connect("stranger", "bob", Relationship.COMMUNITY, trust=0.2)  # below threshold
    ex = KnowledgeExchange(net, trust_threshold=0.5)
    prop = ex.publish("stranger", "belief", "api", [("A", "B", 1.0)])
    res = ex.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "trust_below_threshold"


def test_unpermitted_class_rejected():
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.FRIEND)  # friend cannot publish beliefs
    ex = KnowledgeExchange(net)
    prop = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    res = ex.deliver(prop, "bob", WorldModelRuntime().new_local_belief())  # kind→PUBLISH_BELIEFS
    assert res.status == "rejected" and res.reason == "not_permitted"


def test_tampered_proposal_rejected():
    net, ex = _setup()
    prop = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    tampered = BeliefProposal(
        prop.origin, prop.kind, prop.domain, (("A", "B", 9.9),), prop.signature
    )  # payload changed, old sig
    res = ex.deliver(tampered, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


def test_conflicting_proposal_quarantined_not_overwritten():
    net, ex = _setup()
    bob_belief = WorldModelRuntime().new_local_belief()
    bob_belief.observe("Rule", "PolicyA", domain="gov", reward=1.0)  # bob's existing belief
    prop = ex.publish("alice", "belief", "gov", [("Rule", "PolicyA", 0.0)])  # conflicting value
    res = ex.deliver(prop, "bob", bob_belief)
    assert res.status == "quarantined"
    assert res.conflicts and res.conflicts[0]["edge"] == ("Rule", "PolicyA")
    assert ex.queue.pending() == 0  # not merged — no silent overwrite
