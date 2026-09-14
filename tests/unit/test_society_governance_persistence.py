"""SocietyGovernanceEngine cross-process persistence — qualification tests.

Prior state (see DEPLOYMENT_ARCHITECTURE.md's addendum): investigation
found policies/permissions already had persisted fields in
PlanetaryRuntime._save_societies()/_load_societies() (the same durable
Redis blob society metadata round-trips through), but two of the four
live governance-mutating routes (api/routes/societies.py::
add_society_governance_policy, grant_actor_permission) never actually
called _save_societies() after mutating — so a policy/permission added
through them was only durable by accident, whenever some UNRELATED route
happened to trigger a save afterward. Separately, trust_records/
safety_constraints/audit_log had NO persisted fields at all.

This file tests both fixes:
  1. The save/load round trip is correct for all five governance concerns
     — including the three that previously had zero persisted fields.
  2. The exact sequence the four now-fixed routes perform (mutate, then
     _save_societies()) actually makes the mutation visible to a SECOND,
     independent PlanetaryRuntime sharing the same Redis — the literal
     cross-process guarantee this fix exists to provide.

Uses a minimal in-memory fake for the one Redis operation
_save_societies()/_load_societies() actually perform (a plain string
GET/SET on "monkeybrain:societies") — no NX/EX/hash semantics needed here,
unlike the actor-registry/lease fakes elsewhere in this repo's test suite.

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/unit/test_society_governance_persistence.py -v
"""

from __future__ import annotations

import os

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import Society
from src.monkey_brain.kernel.society.governance import (
    GovernancePolicy,
    Permission,
    TrustRecord,
    SafetyConstraint,
    ComplianceStatus,
    PolicyType,
)

_SHARED_SOCIETY_ID = "shared-test-society"
"""Each bare PlanetaryRuntime() mints its OWN default society with a
random society_id -- two independently-constructed instances would never
actually reference the same society, making a save/load round trip
between them meaningless (pr2._load_societies() would create a THIRD,
separate SocietyRuntime for pr1's random id rather than updating pr2's
own). Passing an explicit, identical Society(society_id=...) to both
constructors is what makes "two processes, same society" a real
simulation rather than an accidental pass."""


class _FakeSocietiesRedis:
    """The minimal subset of redis-py's API _save_societies()/
    _load_societies() actually call: get/set on one string key. Shared
    across two PlanetaryRuntime instances in these tests to simulate two
    processes reading/writing the same real Redis."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def set(self, key, value, **kwargs):
        self._store[key] = value
        return True

    def get(self, key):
        return self._store.get(key)

    # Unused by _save_societies/_load_societies but present on the real
    # client and touched by other PlanetaryRuntime.__init__ persistence
    # paths (e.g. geography/collective-learning save) that run in the same
    # constructor call — no-op stand-ins so construction doesn't raise.
    def hset(self, *a, **kw):
        pass

    def hget(self, *a, **kw):
        return None

    def hgetall(self, *a, **kw):
        return {}

    def hdel(self, *a, **kw):
        pass

    def sadd(self, *a, **kw):
        pass

    def smembers(self, *a, **kw):
        return set()

    def exists(self, *a, **kw):
        return 0

    def rpush(self, *a, **kw):
        pass

    def lrange(self, *a, **kw):
        return []

    def delete(self, *a, **kw):
        return 0


def _shared_pair():
    """Two independent PlanetaryRuntime instances, both constructed
    against the SAME explicit society_id and wired to the SAME fake Redis
    — the closest honest simulation of "two processes governing the same
    society" available without actually spawning a second process, same
    technique this repo's own checkpoint/restart tests use for "the
    process restarted." Redis is attached AFTER construction so each
    instance's own __init__-time persistence calls (which run before
    this) safely no-op against the real self._redis=None fallback,
    exactly like every other test in this file that only cares about the
    explicit _save_societies()/_load_societies() calls it makes itself."""
    redis = _FakeSocietiesRedis()
    pr1 = PlanetaryRuntime(society=Society(society_id=_SHARED_SOCIETY_ID, name="Shared"))
    pr1._redis = redis
    pr2 = PlanetaryRuntime(society=Society(society_id=_SHARED_SOCIETY_ID, name="Shared"))
    pr2._redis = redis
    return pr1, pr2, redis


# ── Round-trip correctness for all five governance concerns ─────────────


def test_save_load_round_trips_governance_policy():
    pr1, pr2, redis = _shared_pair()
    policy = GovernancePolicy(
        name="refund-limit",
        description="cap refunds",
        policy_type=PolicyType.RESTRICTION,
        rules=("amount<=100",),
        scope="cashier",
    )
    pr1.governance.add_policy(policy)
    pr1._save_societies()

    pr2._load_societies()
    loaded = pr2.governance.get_policy(policy.policy_id)
    assert loaded is not None
    assert loaded.name == "refund-limit"
    assert loaded.rules == ("amount<=100",)
    assert loaded.policy_type == PolicyType.RESTRICTION


def test_save_load_round_trips_permission():
    pr1, pr2, redis = _shared_pair()
    permission = Permission(actor_id="alice", resource="order", action="create", granted_by="admin")
    pr1.governance.grant_permission(permission)
    pr1._save_societies()

    pr2._load_societies()
    assert pr2.governance.check_permission("alice", "order", "create") is True


def test_save_load_round_trips_trust_record():
    """Previously: zero persisted fields for trust_records at all."""
    pr1, pr2, redis = _shared_pair()
    pr1.governance.evaluate_trust("bob", 0.82, factors={"reliability": 0.9})
    pr1._save_societies()

    pr2._load_societies()
    trust = pr2.governance.get_trust("bob")
    assert trust.trust_score == 0.82
    assert trust.factors.get("reliability") == 0.9
    assert trust.evidence_count == 1


def test_save_load_round_trips_safety_constraint():
    """Previously: zero persisted fields for safety_constraints at all."""
    pr1, pr2, redis = _shared_pair()
    constraint = SafetyConstraint(
        name="no-unsupervised-large-refunds",
        rule="amount<=500",
        severity="high",
        applies_to=("cashier",),
    )
    pr1.governance.add_safety_constraint(constraint)
    pr1._save_societies()

    pr2._load_societies()
    loaded = pr2.governance.safety_constraints()
    assert len(loaded) == 1
    assert loaded[0].name == "no-unsupervised-large-refunds"
    assert loaded[0].applies_to == ("cashier",)


def test_save_load_round_trips_audit_log_in_chronological_order():
    """Previously: zero persisted fields for audit_log at all. Confirms
    restore order matches original append order, not reversed or
    scrambled by the JSON round trip."""
    pr1, pr2, redis = _shared_pair()
    pr1.governance.audit(
        "carol",
        "attempted_refund",
        compliance_status=ComplianceStatus.COMPLIANT,
        details="first",
    )
    pr1.governance.audit(
        "carol",
        "attempted_refund",
        compliance_status=ComplianceStatus.NON_COMPLIANT,
        details="second",
    )
    pr1._save_societies()

    pr2._load_societies()
    log = pr2.governance.audit_log(actor_id="carol")
    assert [e.details for e in log] == ["first", "second"]
    assert pr2.governance.check_compliance("carol") == ComplianceStatus.NON_COMPLIANT


# ── The actual cross-process guarantee: mutate-then-save sequence, as the
#    now-fixed routes perform it, is visible to a second process ──────────


def test_policy_added_and_saved_is_visible_to_a_second_process():
    """Exercises the EXACT sequence add_society_governance_policy now
    performs (sr.governance.add_policy(...) then pr._save_societies()) —
    the literal fix for the route that previously never called
    _save_societies() at all."""
    pr1, pr2, redis = _shared_pair()
    policy = GovernancePolicy(name="cross-process-policy", policy_type=PolicyType.GUIDELINE)

    pr1.governance.add_policy(policy)
    pr1._save_societies()  # the line the route was missing before this fix

    pr2._load_societies()
    names = [p.name for p in pr2.governance.policies(enabled_only=False)]
    assert "cross-process-policy" in names


def test_permission_granted_and_saved_is_visible_to_a_second_process():
    pr1, pr2, redis = _shared_pair()
    permission = Permission(actor_id="dave", resource="world", action="observe", granted_by="admin")

    pr1.governance.grant_permission(permission)
    pr1._save_societies()  # the line grant_actor_permission was missing before this fix

    pr2._load_societies()
    assert pr2.governance.check_permission("dave", "world", "observe") is True


def test_policy_added_without_saving_is_correctly_NOT_visible_to_a_second_process():
    """Negative control: confirms the round trip genuinely depends on the
    save call actually happening (not some other implicit sharing) — if
    this test failed, the positive tests above would be meaningless."""
    pr1, pr2, redis = _shared_pair()
    policy = GovernancePolicy(name="never-saved-policy")
    pr1.governance.add_policy(policy)
    # deliberately no pr1._save_societies() call

    pr2._load_societies()
    names = [p.name for p in pr2.governance.policies(enabled_only=False)]
    assert "never-saved-policy" not in names


# ── restore_trust_record / restore_audit_entry are restores, not new events ──


def test_restore_trust_record_does_not_increment_evidence_count():
    """restore_trust_record must reinstate verbatim, unlike evaluate_trust
    (which always increments evidence_count relative to whatever is
    already held) — a reload must not look like a NEW trust event."""
    from src.monkey_brain.kernel.society.governance import SocietyGovernanceEngine

    engine = SocietyGovernanceEngine()
    record = TrustRecord(actor_id="erin", trust_score=0.7, evidence_count=5, factors={"honesty": 0.6})
    engine.restore_trust_record(record)
    restored = engine.get_trust("erin")
    assert restored.evidence_count == 5  # not 6 — verbatim restore, not an increment
    assert restored.trust_score == 0.7


def test_restore_audit_entry_preserves_original_timestamp():
    """restore_audit_entry must preserve the ORIGINAL timestamp, unlike
    audit() (which always mints time.time()) — otherwise every restart
    would silently rewrite audit history to "now"."""
    from src.monkey_brain.kernel.society.governance import (
        SocietyGovernanceEngine,
        AuditEntry,
    )

    engine = SocietyGovernanceEngine()
    original = AuditEntry(actor_id="frank", action="checkout", timestamp=1000.0)
    engine.restore_audit_entry(original)
    restored = engine.audit_log(actor_id="frank")[0]
    assert restored.timestamp == 1000.0
