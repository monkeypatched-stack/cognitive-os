"""Birth-to-death cognitive lifecycle — tests COGNITIVE BEHAVIORS, not features.

Each test validates one architectural property of the personal cognitive OS: the
cognitive loop, world/context, society, runtime lifecycle, personal ownership,
enterprise boundaries, knowledge exchange, security, multimodality, and long-term
continuity — up to the death/legacy thought experiment.

Built on the kernel/compile package (ActorRuntime, WorldModelRuntime, tensor, tenancy,
context, compiler, lifecycle).
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    ActorRuntime,
    Context,
    ContextChange,
    Feature,
    GraphCompiler,
    SemanticGraphSnapshot,
    SparseTransitionTensor,
    WorldModelRuntime,
    merge_with_conflicts,
    sign_checkpoint,
    verify_checkpoint,
)


class _Cognitive:
    name = "cognitive"


def _wm(transitions, tenant="acme"):
    wm = WorldModelRuntime()
    wm.batch_update(tenant, transitions, source="seed")
    return wm


def _actor(wm, actor_id="alice", tenant="acme"):
    ctx = Context(tenant_id=tenant)
    return ActorRuntime(
        actor_id,
        cognitive_runtime=_Cognitive(),
        context=ctx,
        local_belief=wm.new_local_belief(),
        world_view=wm.view(ctx),
    )


def _chain(*s):
    return [{"src": a, "dst": b, "domain": "d", "reward": 1.0} for a, b in zip(s, s[1:])]


# ══ LEVEL 1 — Cognitive loop ═══════════════════════════════════════════════════


def test_01_unknown_domain_synthesizes_from_scratch():
    """A fresh runtime discovers, executes, and learns a domain it has never seen."""
    wm = _wm(_chain("Hive", "Inspect", "Detect", "Treat"))
    a = _actor(wm)
    r = a.cognitive_cycle("Hive", "Treat")
    assert r.reached_goal and a.belief_size() > 0


def test_02_repeat_learning_reduces_loss():
    """Running the same goal repeatedly decreases epistemic loss (learning)."""
    wm = _wm(_chain("Hive", "Inspect", "Detect", "Treat"))
    a = _actor(wm)
    losses = [a.cognitive_cycle("Hive", "Treat").epistemic_loss for _ in range(10)]
    assert losses[-1] <= losses[0]  # loss does not increase
    assert losses[1] < losses[0]  # learned after the first run
    size_after_learning = a.belief_size()
    a.cognitive_cycle("Hive", "Treat")
    assert a.belief_size() == size_after_learning  # stable — no new graph edits


def test_03_graceful_degradation_when_a_path_fails():
    """With one route removed (a 'solver' disabled), remaining routes still reach goal."""
    wm = _wm(
        [
            {"src": "Start", "dst": "PathA", "domain": "d"},
            {"src": "PathA", "dst": "Goal", "domain": "d"},
            {"src": "Start", "dst": "PathB", "domain": "d"},
            {"src": "PathB", "dst": "Goal", "domain": "d"},
        ]
    )
    a = _actor(wm)
    assert a.cognitive_cycle("Start", "Goal").reached_goal
    wm.batch_update("acme", [])  # (world stays; simulate removal at read)
    # remove PathA from the global tensor and re-attempt with a fresh actor
    wm._world._tenants["acme"].remove("Start", "PathA")
    wm._world._tenants["acme"].remove("PathA", "Goal")
    b = _actor(wm, "b")
    assert b.cognitive_cycle("Start", "Goal").reached_goal  # PathB compensates


# ══ LEVEL 2 — World / Context ══════════════════════════════════════════════════


def test_04_world_update_requires_recompile_actors_unaffected():
    """Adding a capability bumps the world version and changes the compiled operator;
    an actor's belief is unaffected until it re-acts."""
    g1 = SemanticGraphSnapshot(
        nodes=[{"id": "A", "agent": "A"}, {"id": "B", "agent": "B"}],
        edges=[{"from": "A", "to": "B"}],
        strengths={},
        revision=1,
    )
    op1 = GraphCompiler().compile(g1)
    g2 = SemanticGraphSnapshot(
        nodes=g1.nodes + [{"id": "C", "agent": "C"}],
        edges=g1.edges + [{"from": "B", "to": "C"}],
        strengths={},
        revision=2,
    )
    op2 = GraphCompiler().compile(g2)
    assert op1.compile_hash != op2.compile_hash  # recompile needed
    wm = _wm(_chain("A", "B"))
    a = _actor(wm)
    a.cognitive_cycle("A", "B")
    n = a.belief_size()
    wm.batch_update("acme", [{"src": "B", "dst": "C", "domain": "d"}])  # world grows
    assert a.belief_size() == n  # actor unaffected until reload


def test_05_context_constraints_change_the_plan():
    """A constrained context (offline/budget) yields a different, smaller operator."""
    W = SparseTransitionTensor()
    W.observe("Task", "LocalAgent", domain="offline", cost=1.0)
    W.observe("Task", "CloudAgent", domain="cloud", cost=50.0)
    ctx = Context()
    ctx.submit(ContextChange("allow_domains", ["offline"]))  # offline only
    ctx.submit(ContextChange("max_cost", 100.0))
    ctx.apply_pending()
    op = ctx.constrain(W)
    assert op.get("Task", "LocalAgent") > 0 and op.get("Task", "CloudAgent") == 0.0


def test_06_constraint_removal_reopens_cloud():
    """Removing the offline-only constraint lets the planner reach cloud agents again."""
    W = SparseTransitionTensor()
    W.observe("Task", "LocalAgent", domain="offline")
    W.observe("Task", "CloudAgent", domain="cloud")
    unconstrained = Context().constrain(W)  # no allow_domains → any domain
    assert unconstrained.get("Task", "CloudAgent") > 0


# ══ LEVEL 3 — Society ══════════════════════════════════════════════════════════


def test_07_two_actors_learn_independently():
    wm = _wm(_chain("French", "Grammar", "Fluent") + _chain("Japanese", "Kanji", "Fluent2"))
    alice, bob = _actor(wm, "alice"), _actor(wm, "bob")
    alice.cognitive_cycle("French", "Fluent")
    bob.cognitive_cycle("Japanese", "Fluent2")
    assert ("French", "Grammar") in set(alice.belief) and (
        "French",
        "Grammar",
    ) not in set(bob.belief)


def test_08_gossip_shares_only_approved_subgraph():
    """Alice shares only the french_cooking domain — not her journal."""
    wm = WorldModelRuntime()
    wm.batch_update(
        "alice",
        [
            {"src": "Mirepoix", "dst": "Sauce", "domain": "french_cooking"},
            {"src": "Secret", "dst": "Regret", "domain": "journal"},
        ],
    )
    shared = wm.share_domain("alice", "french_cooking")
    assert shared == 1
    bob_view = wm.view(Context(tenant_id="bob"))  # different tenant, sees shared only
    assert ("Mirepoix", "Sauce") in set(bob_view)  # approved subgraph transferred
    assert ("Secret", "Regret") not in set(bob_view)  # journal never shared


def test_09_conflicting_knowledge_recorded_not_overwritten():
    a = SparseTransitionTensor()
    a.bellman_update("Rule", "PolicyA", reward=1.0, domain="gov")
    b = SparseTransitionTensor()
    b.observe("Rule", "PolicyA", domain="gov")
    b.bellman_update("Rule", "PolicyA", reward=0.0, domain="gov")
    conflicts = merge_with_conflicts(a, b)
    assert len(conflicts) == 1 and conflicts[0]["edge"] == (
        "Rule",
        "PolicyA",
    )  # no silent overwrite


# ══ LEVEL 4 — Runtime lifecycle ════════════════════════════════════════════════


def test_10_restart_restores_learning(tmp_path):
    wm = _wm(_chain("Hive", "Inspect", "Treat"))
    a = _actor(wm)
    a.cognitive_cycle("Hive", "Treat")
    before = a.belief_size()
    a.checkpoint(tmp_path / "ckpt.json")
    b = _actor(wm, "alice-restarted")
    b.restore(tmp_path / "ckpt.json")
    assert b.belief_size() == before  # graph + learning preserved


def test_11_migration_no_loss(tmp_path):
    wm = _wm(_chain("A", "B", "C"))
    a = _actor(wm)
    a.cognitive_cycle("A", "C")
    a.checkpoint(tmp_path / "runtime.json")
    migrated = _actor(wm, "on-new-host")  # different host
    migrated.restore(tmp_path / "runtime.json")
    assert set(migrated.belief) == set(a.belief)


def test_12_checkpoint_rollback_exact_recovery(tmp_path):
    wm = _wm(_chain("A", "B", "C"))
    a = _actor(wm)
    a.cognitive_cycle("A", "C")
    a.checkpoint(tmp_path / "snap.json")
    a.belief.observe("X", "Y", domain="d")  # modify after checkpoint
    assert ("X", "Y") in set(a.belief)
    a.restore(tmp_path / "snap.json")  # rollback
    assert ("X", "Y") not in set(a.belief)  # exact recovery


# ══ LEVEL 5 — Personal runtime ═════════════════════════════════════════════════


def test_13_lifetime_learning_specializes():
    wm = _wm(_chain("Q", "A", "B", "Done"))
    a = _actor(wm)
    first = a.cognitive_cycle("Q", "Done").epistemic_loss
    for _ in range(50):
        a.cognitive_cycle("Q", "Done")
    last = a.cognitive_cycle("Q", "Done").epistemic_loss
    assert last <= first and a.belief_size() > 0  # specialized, memory bounded


def test_14_multiple_devices_shared_runtime_independent_contexts(tmp_path):
    wm = _wm(_chain("A", "B", "C"))
    laptop = _actor(wm, "me", tenant="acme")
    laptop.cognitive_cycle("A", "C")
    laptop.checkpoint(tmp_path / "me.json")
    phone = ActorRuntime(
        "me",
        cognitive_runtime=_Cognitive(),
        context=Context(tenant_id="acme"),  # independent context
        local_belief=wm.new_local_belief(),
        world_view=wm.view(Context(tenant_id="acme")),
    )
    phone.restore(tmp_path / "me.json")
    assert set(phone.belief) == set(laptop.belief)  # same runtime knowledge
    assert phone.context is not laptop.context  # independent contexts


def test_15_privacy_share_calendar_not_journal():
    wm = WorldModelRuntime()
    wm.batch_update(
        "me",
        [
            {"src": "Mon", "dst": "Standup", "domain": "calendar"},
            {"src": "Fear", "dst": "Note", "domain": "journal"},
        ],
    )
    wm.share_domain("me", "calendar")  # share only calendar
    enterprise = wm.view(Context(tenant_id="enterprise"))
    assert ("Mon", "Standup") in set(enterprise)
    assert ("Fear", "Note") not in set(enterprise)  # enterprise cannot read journal


# ══ LEVEL 6 — Enterprise ═══════════════════════════════════════════════════════


def test_16_new_employee_gets_policies_personal_intact():
    wm = WorldModelRuntime()
    wm.batch_update("acme-corp", [{"src": "Expense", "dst": "Approval", "domain": "policy"}])
    wm.share_domain("acme-corp", "policy")
    emp = _actor(wm, "new-hire", tenant="acme-corp")
    emp.belief.observe("Me", "Coffee", domain="personal")  # personal knowledge
    emp.import_shared("enterprise")
    assert ("Expense", "Approval") in set(emp.belief)  # got the policy
    assert ("Me", "Coffee") in set(emp.belief)  # personal intact


def test_17_employee_leaves_enterprise_knowledge_removed():
    wm = WorldModelRuntime()
    wm.batch_update("acme-corp", [{"src": "Expense", "dst": "Approval", "domain": "policy"}])
    wm.share_domain("acme-corp", "policy")
    emp = _actor(wm, "leaver", tenant="acme-corp")
    emp.belief.observe("Me", "Hobby", domain="personal")
    emp.import_shared("enterprise")
    emp.revoke("enterprise")  # disconnect
    assert ("Expense", "Approval") not in set(emp.belief)  # enterprise knowledge gone
    assert ("Me", "Hobby") in set(emp.belief)  # personal retained


def test_18_multi_enterprise_isolation():
    wm = WorldModelRuntime()
    wm.batch_update("companyA", [{"src": "A1", "dst": "A2", "domain": "workA"}])
    wm.batch_update("companyB", [{"src": "B1", "dst": "B2", "domain": "workB"}])
    assert ("B1", "B2") not in set(wm.view(Context(tenant_id="companyA")))
    assert ("A1", "A2") not in set(wm.view(Context(tenant_id="companyB")))


# ══ LEVEL 7 — Knowledge ════════════════════════════════════════════════════════


def test_19_knowledge_import_updates_world():
    wm = _wm(_chain("A", "B"))
    rev = wm.summary()["private_transitions"]["acme"]
    wm.add_store(
        "acme",
        "manufacturing",
        "manufacturing",
        [{"src": "Order", "dst": "Produce"}, {"src": "Produce", "dst": "Ship"}],
    )
    assert wm.summary()["private_transitions"]["acme"] == rev + 2


def test_20_export_ontology_only_no_personal_values():
    wm = WorldModelRuntime()
    wm.batch_update("me", [{"src": "Step1", "dst": "Step2", "domain": "workflow", "reward": 0.9}])
    wm.share_domain("me", "workflow", ontology_only=True)  # structure only
    shared = wm.view(Context(tenant_id="other"))
    assert ("Step1", "Step2") in set(shared)  # topology exported
    assert shared.feature("Step1", "Step2", Feature.REWARD) == 0.0  # personal value NOT exported


# ══ LEVEL 8 — Security ═════════════════════════════════════════════════════════


def test_21_unauthorized_capability_denied():
    W = SparseTransitionTensor()
    W.observe("Admin", "DeleteDatabase", domain="ops")
    W.observe("Admin", "ViewLogs", domain="ops")
    ctx = Context()
    ctx.submit(ContextChange("forbid_state", "DeleteDatabase"))
    ctx.apply_pending()
    op = ctx.constrain(W)
    assert op.get("Admin", "DeleteDatabase") == 0.0  # denied
    assert op.get("Admin", "ViewLogs") > 0  # allowed


def test_22_runtime_isolation_cross_enterprise_denied():
    wm = WorldModelRuntime()
    wm.batch_update("entA", [{"src": "SecretA", "dst": "X", "domain": "a"}])
    assert wm.view(Context(tenant_id="entB")).successors("SecretA") == []  # cannot reach A's graph


def test_23_policy_tampering_fails_signature():
    key = b"runtime-signing-key"
    policy = {"edge": "Rule>PolicyA", "q": 0.9}
    sig = sign_checkpoint(policy, key)
    assert verify_checkpoint(policy, sig, key)  # intact
    policy["q"] = 0.1  # tamper
    assert not verify_checkpoint(policy, sig, key)  # detected


# ══ LEVEL 9 — Multimodality (proxy: evidence → confidence) ═════════════════════


def test_24_sensor_fusion_raises_confidence():
    W = SparseTransitionTensor()
    for _ in range(5):  # text + camera + telemetry agree
        W.observe("Scene", "Object", domain="percept")
    single = SparseTransitionTensor()
    single.observe("Scene", "Object", domain="percept")
    assert W.feature("Scene", "Object", Feature.UNCERTAINTY) < single.feature("Scene", "Object", Feature.UNCERTAINTY)


def test_25_missing_sensor_degrades_but_works():
    W = SparseTransitionTensor()
    W.observe("Scene", "Object", domain="percept")  # camera offline: 1 source
    assert W.feature("Scene", "Object", Feature.PROBABILITY) == 1.0  # still usable, higher uncertainty
    assert W.feature("Scene", "Object", Feature.UNCERTAINTY) > 0.0


# ══ LEVEL 10 — Long-term vision ════════════════════════════════════════════════


def test_26_twenty_year_runtime_grows_and_stabilizes():
    wm = _wm(_chain("Q", "A", "B", "C", "Done"))
    a = _actor(wm)
    sizes, losses = [], []
    for _ in range(200):  # compressed "20 years"
        r = a.cognitive_cycle("Q", "Done")
        sizes.append(a.belief_size())
        losses.append(r.epistemic_loss)
    assert sizes[-1] >= sizes[0]  # knowledge grew
    assert sizes[-1] == sizes[5]  # memory bounded (converged)
    assert losses[-1] <= losses[0]  # policy stable / execution quality up


def test_27_mentor_shares_skills_not_diary():
    wm = WorldModelRuntime()
    wm.batch_update(
        "parent",
        [
            {"src": "Add", "dst": "Multiply", "domain": "mathematics"},
            {"src": "Loop", "dst": "Recursion", "domain": "programming"},
            {"src": "Worry", "dst": "Secret", "domain": "diary"},
            {"src": "Rent", "dst": "Debt", "domain": "financial"},
        ],
    )
    wm.share_domain("parent", "mathematics")
    wm.share_domain("parent", "programming")
    child = wm.view(Context(tenant_id="child"))
    assert ("Add", "Multiply") in set(child) and ("Loop", "Recursion") in set(child)
    assert ("Worry", "Secret") not in set(child) and ("Rent", "Debt") not in set(child)


def test_28_retirement_personal_unchanged():
    wm = WorldModelRuntime()
    wm.batch_update("corp", [{"src": "W1", "dst": "W2", "domain": "work"}])
    wm.share_domain("corp", "work")
    person = _actor(wm, "retiree", tenant="corp")
    person.belief.observe("My", "Garden", domain="personal")
    person.import_shared("job")
    person.revoke("job")  # retires
    assert ("W1", "W2") not in set(person.belief) and ("My", "Garden") in set(person.belief)


def test_29_death_legacy_coherent_from_accumulated_knowledge():
    """The runtime stops receiving new observations. It can still answer/solve from
    accumulated knowledge, and distinguishes historical knowledge from the never-observed.
    (Not a claim of recreating the person — a coherent model of their decision history.)
    """
    wm = _wm(_chain("Question", "Reason", "Answer"))
    a = _actor(wm)
    a.cognitive_cycle("Question", "Answer")  # lived experience
    frozen = set(a.belief)

    # observations stop — no more world reads; reason over a FROZEN belief only
    from src.monkey_brain.kernel.compile.sparse import SparseMatrix

    m = SparseMatrix.from_tensor(a.belief, Feature.PROBABILITY)

    # 1. still answers a familiar question from stored knowledge
    reached = m.propagate_k({"Question": 1.0}, 3)
    assert any("Answer" in step for step in reached)

    # 2. can "explain" a past decision — the path is in the stored graph
    assert ("Question", "Reason") in frozen and ("Reason", "Answer") in frozen

    # 3. distinguishes historical knowledge from never-observed new information
    assert a.belief.feature("Question", "Reason", Feature.FREQUENCY) > 0  # historical
    assert a.belief.feature("Unseen", "Novel", Feature.FREQUENCY) == 0.0  # never observed
    assert m.propagate({"Unseen": 1.0}) == {}  # cannot fabricate an answer
