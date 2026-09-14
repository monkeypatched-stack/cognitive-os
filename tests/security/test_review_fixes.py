"""Regression tests for the manual-review findings (M/L)."""

from __future__ import annotations

import copy
import time

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import CertificateAuthority, verify_bundle
from src.monkey_brain.kernel.compile import (
    KnowledgeExchange,
    Relationship,
    TrustNetwork,
    WorldModelRuntime,
)
from src.monkey_brain.kernel.compile.exchange import MergeQueue, BeliefProposal


# M1 — dedup set is bounded
def test_merge_queue_dedup_window_is_bounded():
    q = MergeQueue(max_size=10_000, dedup_window=100)
    for k in range(500):
        q.enqueue("bob", BeliefProposal("a", "belief", "api", ((f"S{k}", f"T{k}", 1.0),)))
        q._q.clear()  # simulate periodic flush of the pending list
    assert len(q._seen) <= 100  # dedup window bounded regardless of volume


# L1 — duplicate is idempotent-accepted, not "queue_full"
def test_duplicate_proposal_is_idempotent():
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    ex = KnowledgeExchange(net)
    prop = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    belief = WorldModelRuntime().new_local_belief()
    r1 = ex.deliver(prop, "bob", belief)
    r2 = ex.deliver(prop, "bob", belief)  # same proposal again
    assert r1.status == "accepted" and r2.status == "accepted"
    assert r2.reason == "duplicate" and ex.queue.pending() == 1  # not double-queued


# L3 — future-dated bundle rejected
def test_future_dated_bundle_rejected(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    ca = CertificateAuthority("authority:root", key_manager=km)
    b = ca.export_bundle()
    assert verify_bundle(b, ca.ca_public_pem())
    future = copy.deepcopy(b)
    future["issued_at"] = time.time() + 4000
    assert not verify_bundle(future, ca.ca_public_pem())


# M4 — trust relationships are durable across restart
def test_trust_network_persists(tmp_path):
    store = str(tmp_path / "trust.json")
    n1 = TrustNetwork(store_path=store)
    n1.connect("alice", "bob", Relationship.MENTOR)
    n2 = TrustNetwork(store_path=store)  # "restart"
    assert n2.permits("alice", "bob", "learn.publish_beliefs") or n2.edge("alice", "bob") is not None
    assert n2.trust("alice", "bob") > 0
