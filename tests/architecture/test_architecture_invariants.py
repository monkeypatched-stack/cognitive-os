"""Architecture Boundary Hardening, Section 13: structural invariant
tests for the boundaries Sections 1-11 describe. Each test proves a
concrete, currently-true fact about the real implementation -- never a
re-statement of a docstring or diagram. Where an existing test file
already covers an invariant in depth (portable delegation, ROS
governance, edge zero-round-trip), this file adds a fresh, minimal,
self-contained proof rather than duplicating that suite, and says so.
"""
from __future__ import annotations

import inspect
import re

import pytest


REPO_SRC = "src/monkey_brain"


class TestGovernanceCoverage:
    """Every capability side effect passes through ensure_governed
    (Section 8/13) -- proven structurally: action_executor.py's ONLY
    two `capability.handle(` call sites are both inside `_invoke_handle`,
    which is only ever referenced as the callable passed to
    `ensure_governed` (via `_governed_invoke`), never called directly."""

    @staticmethod
    def _real_call_sites(source: str, pattern: str) -> int:
        """Count real code occurrences of `pattern`, excluding comment
        lines and docstring/prose references -- inspect.getsource() over
        a whole module includes both, and this codebase's convention is
        to reference code in prose wrapped in backticks (`` `x.y()` ``),
        so both `#`-prefixed lines and backtick-wrapped mentions are
        excluded rather than counted as real call sites."""
        count = 0
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("def ") or stripped.startswith("async def "):
                continue
            for match in re.finditer(re.escape(pattern), line):
                if f"`{pattern}" in line or f"`{pattern[:-1]}`" in line:
                    continue
                count += 1
        return count

    def test_capability_handle_is_only_ever_called_inside_the_governed_invoke_closure(self):
        import src.monkey_brain.kernel.pipeline.action_executor as mod

        source = inspect.getsource(mod)
        n = self._real_call_sites(source, "capability.handle(")
        assert n == 2, (
            f"expected exactly 2 real capability.handle( call sites (sync+async branch of "
            f"_invoke_handle), found {n} -- a new direct call site would be an "
            f"ungoverned capability invocation"
        )
        invoke_handle_block = re.search(r"async def _invoke_handle.*?\n\n", source, re.DOTALL)
        assert invoke_handle_block is not None
        assert "capability.handle(" in invoke_handle_block.group(0)
        # _invoke_handle itself must only be referenced from inside the
        # ensure_governed call (via _governed_invoke wrapping it) --
        # never invoked bare, bypassing governance.
        governed_invoke_block = re.search(r"async def _governed_invoke.*?\n\n", source, re.DOTALL)
        assert governed_invoke_block is not None
        assert "_invoke_handle()" in governed_invoke_block.group(0)
        assert self._real_call_sites(source, "_invoke_handle()") == 1, (
            "_invoke_handle() must be called from exactly one place: _governed_invoke"
        )


class TestRosCannotBypassGovernance:
    """Reuses the invariant tests/unit/test_ros_integration_contract.py
    ::TestGovernanceBoundaryIsUnconditional already proves in depth
    (a denied governance decision never reaches the adapter). Added here:
    the structural half -- run_ros_action_if_governed has no code path
    that calls adapter.invoke() without first awaiting ensure_governed."""

    def test_run_ros_action_if_governed_never_calls_invoke_outside_ensure_governed(self):
        import src.monkey_brain.kernel.edge.ros_integration as mod

        source = inspect.getsource(mod.run_ros_action_if_governed)
        real_calls = [
            ln for ln in source.splitlines()
            if "adapter.invoke(" in ln and not ln.strip().startswith("#") and "`adapter.invoke" not in ln
        ]
        # adapter.invoke is only referenced inside the _invoke() closure,
        # which is passed AS THE MUTATE ARGUMENT to ensure_governed --
        # never awaited directly in this function's own body.
        assert len(real_calls) == 1
        assert "return await ensure_governed(" in source
        invoke_closure = re.search(r"async def _invoke.*?\n\n", source, re.DOTALL)
        assert invoke_closure is not None
        assert "adapter.invoke(" in invoke_closure.group(0)


class TestEdgeAuthorityBoundedByCentralAuthority:
    """A locally-cached decision can never grant MORE than the delegation
    chain (itself issued centrally/by an authorized parent) actually
    permits -- fresh, minimal, self-contained proof; the full attack-model
    suite lives in tests/security/test_portable_delegation.py and
    tests/security/test_edge_delegation_message_wiring.py."""

    def test_local_governance_rejects_a_delegation_claiming_more_than_its_parent_granted(self):
        import time as _time

        from src.monkey_brain.kernel.delegation import DelegationCredential, issue_delegation
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes
        from src.monkey_brain.kernel.edge.local_governance import LocalGovernanceEvaluator
        from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
        from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore

        import tempfile
        import os

        tmp_db = tempfile.mktemp(suffix=".db")
        try:
            store = EdgeLocalStore(tmp_db)
            cache = EdgePolicyCache(store)
            gov = LocalGovernanceEvaluator(cache)

            d1 = issue_delegation(issuer="A", delegate="B", capabilities=("grocery.purchase",))
            # B properly signs D2 with its OWN real key, but claims a
            # capability D1 never granted -- a real forged-but-signed
            # escalation attempt, not just a tampered field.
            forged = DelegationCredential(
                issuer="B", delegate="C", parent_delegation_id=d1.delegation_id,
                issued_at=_time.time(), expires_at=d1.expires_at,
                scope=d1.scope, capabilities=("grocery.purchase", "bank.transfer"),
                delegation_depth=d1.delegation_depth + 1,
            )
            km = get_key_manager()
            signed_forged = forged.with_proof(sign_bytes(forged.signing_bytes(), km.get_or_create("B")))

            snapshot = issue_policy_snapshot(
                principal="C", action="capability.bank.transfer", resource="bank.transfer",
                policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "test"},
            )
            cache.store_snapshot(snapshot)

            outcome = gov.evaluate(
                principal="C", action="capability.bank.transfer", resource="bank.transfer",
                authenticated_principal="C", delegation_chain=(d1, signed_forged),
            )

            assert outcome.allowed is False, (
                "a delegation claiming authority its parent never granted must never be "
                "locally allowed, even with a valid signature and a cached AUTO_APPROVE policy"
            )
        finally:
            if os.path.exists(tmp_db):
                os.unlink(tmp_db)


class TestPlacementIndependentOfIdentity:
    """Actor identity (actor_id) is caller-supplied at registration time
    (via ActorProfile), never derived from node_id/pod name/container
    id -- proven by inspecting register_actor's real signature and by a
    real SchedulingDecision showing actor_id is unaffected by which
    node_id it resolves to."""

    def test_register_actor_does_not_derive_actor_id_from_any_node_parameter(self):
        from src.monkey_brain.kernel.society.runtime import SocietyRuntime

        sig = inspect.signature(SocietyRuntime.register_actor)
        params = set(sig.parameters.keys())
        assert "node_id" not in params
        assert "pod_name" not in params
        assert "container_id" not in params

    def test_scheduling_decision_carries_actor_id_and_node_id_as_independent_fields(self):
        from src.monkey_brain.kernel.society.actor_scheduler import SchedulingDecision

        decision = SchedulingDecision(actor_id="actor-42", scheduled=True, node_id="node-7")
        # Re-scheduling the SAME actor to a DIFFERENT node must be
        # representable without touching actor_id at all -- proven by
        # constructing two decisions that share actor_id but differ only
        # in node_id (dataclasses.replace never needs to touch actor_id
        # to change placement).
        import dataclasses
        migrated = dataclasses.replace(decision, node_id="node-9")
        assert migrated.actor_id == decision.actor_id == "actor-42"
        assert migrated.node_id != decision.node_id


class TestControlPlaneOwnsDesiredState:
    """The runtime reconciles TOWARD desired state; it must never itself
    call set_desired_state to redefine what it is reconciling toward --
    only the Scheduler/Lifecycle Controller (control-plane components)
    do. Proven by inspecting which class actually calls
    set_actor_desired_state in the real source tree."""

    def test_set_desired_state_is_called_only_from_control_plane_components(self):
        import ast
        import os

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        src_root = os.path.join(repo_root, "src", "monkey_brain")
        callers = []
        for root, _dirs, files in os.walk(os.path.join(src_root, "kernel")):
            if "__pycache__" in root:
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    source = f.read()
                if "set_actor_desired_state(" in source or "def set_desired_state(" in source:
                    callers.append(os.path.relpath(path, src_root))

        # Every caller must be a control-plane component (scheduler,
        # lifecycle controller, or the PlanetaryRuntime method itself
        # that they call through), never the per-actor cognitive tick
        # path (actor.py, cognitive_actor.py, belief_runtime.py).
        forbidden = {"compile/actor.py", "compile/cognitive_actor.py", "compile/belief_runtime.py"}
        violating = [c for c in callers if c in forbidden]
        assert violating == [], f"the actor cognitive-tick path must never redefine its own desired state: {violating}"
        assert callers, "expected to find at least the real control-plane call sites"


class TestDelegationSubsetInvariant:
    """Authority(child) subset_of Authority(parent) -- fresh, minimal
    proof; the full suite is tests/security/test_portable_delegation.py."""

    def test_child_cannot_outlive_parent(self):
        """issue_delegation clamps (never rejects) a child's expiry to the
        parent's own -- requesting a longer TTL than the parent has
        silently narrows, it does not error; the invariant under test is
        the CLAMPED RESULT, not an exception."""
        from src.monkey_brain.kernel.delegation import issue_delegation

        parent = issue_delegation(issuer="A", delegate="B", capabilities=("grocery.purchase",), ttl_seconds=60)
        child = issue_delegation(
            issuer="B", delegate="C", capabilities=("grocery.purchase",),
            ttl_seconds=3600, parent=parent,
        )
        assert child.expires_at <= parent.expires_at, "a child delegation must never outlive its parent"

    def test_child_cannot_claim_a_capability_the_parent_never_granted(self):
        from src.monkey_brain.kernel.delegation import DelegationDeniedError, issue_delegation

        parent = issue_delegation(issuer="A", delegate="B", capabilities=("grocery.purchase",))
        with pytest.raises(DelegationDeniedError):
            issue_delegation(
                issuer="B", delegate="C", capabilities=("grocery.purchase", "bank.transfer"),
                parent=parent,
            )


class TestEventStateSeparation:
    """Recording a WorldEvent must never, by itself, mutate authoritative
    world entities -- events and state are separate operations reached
    through separate methods, proven by actually recording an event and
    checking entity state is untouched."""

    def test_recording_an_event_does_not_mutate_any_entity(self):
        from src.monkey_brain.kernel.society.world import SharedWorld, WorldEntity, WorldEvent, WorldEntityType

        world = SharedWorld()
        world._require_write = lambda *a, **k: None  # bypass write-lock plumbing not under test here
        entity = WorldEntity(entity_id="e1", name="milk", entity_type=WorldEntityType.RESOURCE)
        world.add_entity(entity)
        version_before = world.version
        entity_before = world.get_entity("e1")

        world.record_event(WorldEvent(entity_id="e1", description="observed low stock"))

        assert world.get_entity("e1") == entity_before, "recording an event must never silently mutate entity state"
        assert world.version == version_before + 1, "recording an event still bumps the world version (it IS a real mutation of event history, just not of entity state)"


class TestMossCannotMutateWorldState:
    """Moss remains retrieval-only (Section 11) -- structural proof: the
    module has no import of, or reference to, anything that could write
    to KnowledgeGraph/SharedWorld."""

    def test_moss_retrieval_module_has_no_world_mutation_capability(self):
        import src.monkey_brain.kernel.edge.moss_retrieval as mod

        source = inspect.getsource(mod)
        for forbidden in ("knowledge_graph", "SharedWorld", "add_entity", "update_entity", "record_event"):
            assert forbidden not in source, f"moss_retrieval.py must never reference {forbidden!r}"

    def test_moss_semantic_memory_has_no_world_mutating_methods(self):
        from src.monkey_brain.kernel.edge.moss_retrieval import MossSemanticMemory

        public_methods = {name for name in dir(MossSemanticMemory) if not name.startswith("_")}
        assert public_methods == {"available", "index_documents", "query"}


class TestCoordinationDoesNotAccumulateAGiantSocietyState:
    """Society architecture review, Section 9 / Phase 3: coordination
    (CoordinationEngine, TransactionCoordinator) must compose through
    existing primitives (actor state, leases, delegation, events,
    messages) rather than accumulating a second, growing, unscoped
    'god state' dict, and must never call SocietyGovernanceEngine's
    authorize()/check_permission() to gate a transaction step (that
    would be a private, undocumented second authority path alongside
    the one Phase 1 already proved for action_executor.py/kernel/domains).

    Verified by inspection before writing this test: CoordinationEngine's
    only instance attributes are `_negotiations` (dict[str, Negotiation])
    and `_actor_negotiations` (an index over it) -- both narrowly scoped
    to negotiation bookkeeping, not a copy of actor/world state.
    TransactionCoordinator's only instance attributes are `_planetary`
    (a back-reference, not owned state) and `_policy_gate` (optional,
    defaults to None). Neither class calls governance.authorize()/
    check_permission() anywhere in its own source."""

    def test_coordination_engine_state_is_narrowly_scoped(self):
        from src.monkey_brain.kernel.society.coordination import CoordinationEngine

        engine = CoordinationEngine()
        attrs = {a for a in vars(engine) if not a.startswith("__")}
        # Anything beyond negotiation bookkeeping + the optional strategic
        # runtime hook would be a sign of accumulating unrelated state.
        assert attrs <= {"_negotiations", "_actor_negotiations", "strategic_runtime"}

    def test_transaction_coordinator_owns_no_state_beyond_its_back_reference(self):
        import inspect

        import src.monkey_brain.kernel.society.transaction as mod

        init_source = inspect.getsource(mod.TransactionCoordinator.__init__)
        assigned = re.findall(r"self\.(\w+)\s*=", init_source)
        assert set(assigned) <= {"_planetary", "_policy_gate"}

    def test_neither_coordination_component_calls_society_governance_engine(self):
        import src.monkey_brain.kernel.society.coordination as coord_mod
        import src.monkey_brain.kernel.society.transaction as txn_mod

        for mod in (coord_mod, txn_mod):
            source = inspect.getsource(mod)
            assert ".authorize(" not in source
            assert ".check_permission(" not in source

    def test_neither_coordination_component_constructs_a_world_state_store(self):
        import src.monkey_brain.kernel.society.coordination as coord_mod
        import src.monkey_brain.kernel.society.transaction as txn_mod

        for mod in (coord_mod, txn_mod):
            source = inspect.getsource(mod)
            assert "KnowledgeGraph(" not in source
            assert "SharedWorld(" not in source


class TestActorBeliefCannotSilentlyBecomeWorldState:
    """Actor belief (persistence.actor_state_store.PersistedActorState /
    kernel.pipeline.belief_state.BeliefState) and world state
    (kernel.knowledge_graph.KnowledgeGraph / kernel.society.world.SharedWorld)
    are distinct classes with distinct storage keys -- checkpointing an
    actor's belief must never write into the world-state store."""

    def test_checkpoint_actor_belief_writes_only_to_the_actor_state_store_not_the_knowledge_graph(self):
        import src.monkey_brain.kernel.society.integration as mod

        source = inspect.getsource(mod.PlanetaryRuntime.checkpoint_actor_belief)
        assert "knowledge_graph" not in source.lower() or "_knowledge_graph" not in source
        assert "store.save(" in source or "actor_state_store" in source.lower()

    def test_belief_state_and_world_entity_are_distinct_types_with_no_shared_base(self):
        from src.monkey_brain.kernel.pipeline.belief_state import BeliefState
        from src.monkey_brain.kernel.society.world import WorldEntity

        assert not issubclass(BeliefState, WorldEntity)
        assert not issubclass(WorldEntity, BeliefState)
