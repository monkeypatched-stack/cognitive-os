"""Static Wave 6 architecture conformance gate.

The gate validates hard ownership invariants and reports compatibility debt.
It is deliberately dependency-free so CI can run it before installing the
full runtime stack.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "monkey_brain"
OWNERSHIP_GRAPH = ROOT / "OWNERSHIP_GRAPH.json"


def _classes(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return []
    return [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _policy_audit_intent_precedes_effect() -> bool:
    """Commitment module notes AUDIT_INTENT before MUTATION."""
    text = (SRC / "kernel" / "security_boundary.py").read_text(errors="ignore")
    intent = text.find('_note("AUDIT_INTENT")')
    mutate = text.find('_note("MUTATION")')
    return 0 <= intent < mutate


def _policy_unknown_distinct_from_failed() -> bool:
    text = (SRC / "kernel" / "security_operation.py").read_text(errors="ignore")
    return 'FAILED = "failed"' in text and 'UNKNOWN = "unknown"' in text and "UnknownOutcomeError" in text


def _mutating_routes_have_idempotent() -> bool:
    routes = SRC / "api" / "routes"
    if not routes.exists():
        return False
    for path in routes.glob("*.py"):
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            mutating = False
            idem = False
            for d in node.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("post", "put", "patch", "delete")
                ):
                    mutating = True
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "idempotent":
                    idem = True
            if mutating and not idem:
                return False
    return True


def _production_runtime_construction() -> list[str]:
    findings: list[str] = []
    allowed = {
        # Architecture-facing aliases/facades, not independent runtime
        # implementations.
        "ActorRuntime",
        "AgentRuntime",
        "CapabilityRuntime",
        "VerticalRuntime",
        # Kernel-owned boot construction and Planetary's injected world model.
        "PipelineCognitiveRuntime",
        "PlanetaryRuntime",
        "WorldModelRuntime",
        "Runtime",  # bootstrap's Wolverine initializer is invoked by Kernel boot
        # SocietyRuntime is a per-society child object exclusively
        # constructed and owned by PlanetaryRuntime (kernel/society/
        # integration.py) as part of its multi-society registry — it is not
        # an independently registered Kernel runtime, so it is not routed
        # through Kernel DI.
        "SocietyRuntime",
        # TrustRuntime/BeliefRuntime are per-actor injected dependencies
        # (kernel/compile/actor_runtime.py, belief_runtime.py): "Dependency
        # inversion: all runtimes are INJECTED INTO the actor. The actor
        # OWNS its reasoning, belief, and trust." Constructing a default
        # instance when the caller does not inject one is the DI pattern
        # itself, not a Kernel-registry bypass — ActorRuntime, not Kernel,
        # is the sole owner of this actor-local state.
        "TrustRuntime",
        "BeliefRuntime",
        # common/evidence.py's example_usage() is demo code reachable only
        # via `if __name__ == "__main__":` when the module is run directly
        # as a script — never imported or invoked by production code, so it
        # is not a real Kernel-ownership bypass.
        "MonkeyBrainRuntime",
        # _NoRuntime (pipeline/orchestrator.py) is a sentinel/null-object
        # marker for "no runtime was available", not a runtime
        # implementation — it happens to match the `*Runtime` name pattern.
        "_NoRuntime",
    }
    for path in SRC.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        lines = path.read_text(errors="ignore").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if not (name == "Runtime" or name.endswith("Runtime")) or name in allowed:
                continue
            line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else name
            findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}: {line}")
    return findings


def _exclusive_constructor_violations() -> dict[str, list[str]]:
    """Two ownership boundaries central to the Planet -> Society -> Actor
    hierarchy (Runtime Encapsulation Refactor, Phase 2): only PlanetaryRuntime
    may construct a SocietyRuntime, and only ActorRuntime may construct a
    CognitiveOS. A stricter, per-class version of
    _production_runtime_construction's general allowlist — that one lets
    SocietyRuntime through globally as "constructed by PlanetaryRuntime";
    this verifies that claim by file, not just by name.
    """
    rules = {
        "SocietyRuntime": "kernel/society/integration.py",
        "CognitiveOS": "kernel/compile/actor_runtime.py",
    }
    # Physical Geography Hierarchy refactor: Planet/Country/State/County/
    # City/Street/Building/Space (and their 6 Building + 12 Space typed
    # subtypes) must be constructed only by GeographicRegistry.create() —
    # kernel/geography/registry.py — so PlanetaryRuntime and API routes stay
    # generic over the abstract types instead of each importing/instancing
    # a specific tier's dataclass.
    geo_types = {
        "Planet",
        "Country",
        "State",
        "County",
        "City",
        "Street",
        "Building",
        "Space",
        "ResidentialBuilding",
        "CommercialBuilding",
        "IndustrialBuilding",
        "InstitutionalBuilding",
        "GovernmentBuilding",
        "MixedUseBuilding",
        "Apartment",
        "House",
        "Office",
        "RetailUnit",
        "Restaurant",
        "HotelRoom",
        "FactoryFloor",
        "WarehouseZone",
        "Classroom",
        "HospitalWard",
        "StorageUnit",
        "ParkingSpace",
    }
    for geo_type in geo_types:
        rules[geo_type] = "kernel/geography/registry.py"
    # Temporal Presence & Actor Timeline Model refactor: Presence/
    # MembershipRecord/GoalRecord/BeliefRecord/ExecutionRecord/
    # RelationshipRecord/ActivityRecord must be constructed only by
    # TimelineStore.record() (kernel/timeline/store.py) — every other call
    # site (belief_state.py, belief_runtime.py, society/belief.py,
    # presence.py, membership.py) passes a TimelineKind + field kwargs
    # rather than importing/instantiating these dataclasses directly.
    timeline_types = {
        "Presence",
        "MembershipRecord",
        "GoalRecord",
        "BeliefRecord",
        "ExecutionRecord",
        "RelationshipRecord",
        "ActivityRecord",
        "IntentRecord",
        "PlanRecord",
        "DecisionRecord",
    }
    for timeline_type in timeline_types:
        rules[timeline_type] = "kernel/timeline/store.py"
    # Membership as a First-Class Runtime Resource refactor: Membership
    # (the derived current-state view) and Delegation must be constructed
    # only within their own defining modules.
    rules["Membership"] = "kernel/society/membership.py"
    rules["Delegation"] = "kernel/society/delegation.py"
    # Context-Aware Personalized Planning refactor: MemoryNode
    # (kernel/learn/memory/primitives.py) must be constructed only by
    # MemoryManager (manager.py) — callers (ContextConstructionEngine, etc)
    # go through record_experience()/search_episodic(), never the
    # dataclass directly.
    rules["MemoryNode"] = "kernel/learn/memory/manager.py"
    violations: dict[str, list[str]] = {name: [] for name in rules}
    for path in SRC.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        rel = str(path.relative_to(SRC))
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        lines = path.read_text(errors="ignore").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name not in rules or rel == rules[name]:
                continue
            # entity.py itself defines these dataclasses (class bodies, not
            # construction calls) — ast.Call only matches actual invocations,
            # but subclass field defaults like `building_type: BuildingType =
            # BuildingType.MIXED_USE` are Attribute access, not Call, so no
            # extra exemption is needed there.
            if rel == "kernel/geography/entity.py":
                continue
            line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else name
            violations[name].append(f"{path.relative_to(ROOT)}:{node.lineno}: {line}")
    return violations


def _boundary_violations() -> dict[str, list[str]]:
    """Runtime Encapsulation Refactor, Phase 7 — boundary verification.

    Each rule is a (forbidden call names -> allowed file set) check, same
    AST-Call-node style as _exclusive_constructor_violations, plus one
    reference-scan rule and one module-level-import rule:

    - actor_runtime_bypass: ActorRuntime( constructed only by SocietyRuntime
      (kernel/society/runtime.py) — "Planet cannot access actor internals."
    - agent_runtime_bypass: AgentRuntime(/AgentMiddleware( constructed only
      by the sanctioned internal engine (runtime/runtime.py, which drives
      ActorRuntime.goal_queue's execution) or api/routes/agents.py (the one
      non-actor-scoped global agent-execution surface, wrapped in
      AgentRuntimeAdapter — Phase 5).
    - capability_runtime_bypass: ActionExecutor( constructed only by
      LegacyCognitiveRuntime's own internal engine (kernel/pipeline/
      execution_runtime/integration.py) — "Capabilities cannot bypass
      ActorRuntime" for the Planet/Society/Actor path; the legacy path's own
      internals are out of scope per the Phase 5 adapter decision.
    - society_no_direct_cognitive_os: no CognitiveOS(/.cognitive_os/
      ._cognitive_os reference anywhere in kernel/society/*.py —
      "Society cannot call CognitiveOS directly."
    - no_module_level_cognitive_imports: kernel/society/integration.py and
      kernel/society/runtime.py must not import kernel/cognitive_os,
      runtime/agent_middleware, or kernel/pipeline/action_executor at module
      level (function-scoped/lazy imports, e.g. ActorRuntime's own
      intentionally-lazy import in register_actor, remain allowed — that is
      the established boundary-respecting DI pattern in this codebase).
    """
    call_rules = {
        "ActorRuntime": {"kernel/society/runtime.py", "actor_runtime.py"},
        "AgentRuntime": {"runtime/runtime.py", "api/routes/agents.py"},
        "AgentMiddleware": {"runtime/runtime.py", "api/routes/agents.py"},
        "ActionExecutor": {"kernel/pipeline/execution_runtime/integration.py"},
    }
    violations: dict[str, list[str]] = {name: [] for name in call_rules}
    violations["society_no_direct_cognitive_os"] = []
    violations["no_module_level_cognitive_imports"] = []

    society_files = {
        SRC / "kernel" / "society" / "runtime.py",
        SRC / "kernel" / "society" / "integration.py",
    }
    forbidden_modules = (
        "kernel.cognitive_os",
        "runtime.agent_middleware",
        "kernel.pipeline.action_executor",
    )

    for path in SRC.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        rel = str(path.relative_to(SRC))
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        lines = path.read_text(errors="ignore").splitlines()

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name in call_rules and rel not in call_rules[name]:
                    line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else name
                    violations[name].append(f"{path.relative_to(ROOT)}:{node.lineno}: {line}")

        if path in society_files:
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in (
                    "cognitive_os",
                    "_cognitive_os",
                ):
                    violations["society_no_direct_cognitive_os"].append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: .{node.attr}"
                    )
            for node in tree.body:
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                else:
                    continue
                for mod in mods:
                    if any(forbidden in mod for forbidden in forbidden_modules):
                        violations["no_module_level_cognitive_imports"].append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: import {mod}"
                        )

    return violations


def _capability_bus_implementations() -> list[str]:
    """Find CapabilityBus classes that are neither the canonical bus, a
    Protocol interface, nor a documented registry-attached/scoped adapter.

    One canonical bus (kernel/execute/capabilities/bus.py::CapabilityBus) is
    invoked by ActionExecutor. The following are legitimate, not duplicates:
      - pipeline/protocols.py::CapabilityBus: a typing.Protocol interface
        declaration, not an implementation.
      - domains/commerce.py::CommerceCapabilityBus: a domain-scoped adapter
        attached to Kernel's CapabilityRegistry via
        ``kernel.capability_registry.attach_bus(vertical.bus)``
        (domains/vertical_router.py) — the same "Kernel facade over existing
        backends" pattern used for AgentRegistry's Broca/provider backends.
      - domains/grocery.py::GroceryCapabilityBus: a subclass specialization
        of CommerceCapabilityBus (inherits its adapter registration), not an
        independent implementation.
      - codegen_runtime.py::BrocaCapabilityBus: a per-run adapter scoped to
        CodeGenRuntime's own GraphScheduler dispatch (passed via
        ``capability_bus=bus`` to ProcessManager.create_process), a
        Kernel-registered runtime that is not part of the ActionExecutor/
        CapabilityRegistry chain.
    A class outside this documented set is undocumented duplication and
    stays flagged.
    """
    allowed = {
        "pipeline/protocols.py": {"CapabilityBus"},
        "kernel/domains/commerce.py": {"CommerceCapabilityBus"},
        "kernel/domains/grocery.py": {"GroceryCapabilityBus"},
        "kernel/codegen_runtime.py": {"BrocaCapabilityBus"},
        "kernel/execute/capabilities/bus.py": {"CapabilityBus"},
    }
    findings: list[str] = []
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(SRC))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.ClassDef) and "capability" in node.name.lower() and "bus" in node.name.lower()
            ):
                continue
            if any(rel.endswith(suffix) and node.name in names for suffix, names in allowed.items()):
                continue
            findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
    return findings


def _class_node(path: Path, name: str) -> ast.ClassDef | None:
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except (OSError, SyntaxError):
        return None
    return next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == name),
        None,
    )


def _class_members(node: ast.ClassDef | None) -> set[str]:
    if node is None:
        return set()
    members: set[str] = set()
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(child.name)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    members.add(target.id)
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Store):
            members.add(child.attr)
    return members


def _alias_target(path: Path, alias: str) -> str | None:
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            if any(isinstance(target, ast.Name) and target.id == alias for target in node.targets):
                return node.value.id
    return None


def _positive_ownership_checks() -> dict:
    """Verify that intended owners positively expose their responsibilities.

    This complements negative searches for forbidden patterns. It is static
    evidence: dynamic dispatch is intentionally reported as an ownership
    boundary, not inferred from runtime traffic.
    """
    checks: dict[str, dict] = {}

    def check(name: str, passed: bool, evidence: str) -> None:
        checks[name] = {"passed": passed, "evidence": evidence}

    kernel = SRC / "kernel" / "kernel.py"
    kernel_text = kernel.read_text(errors="ignore")
    kernel_names = set(_classes(kernel))
    check(
        "Kernel.runtime_construction",
        {"Kernel", "RuntimeRegistry"}.issubset(kernel_names),
        "Kernel and RuntimeRegistry are defined in kernel/kernel.py",
    )
    check(
        "Kernel.boot_validation",
        "validate_architecture()" in kernel_text and "runtime_registry" in kernel_text,
        "Kernel boot exposes runtime_registry and architecture validation",
    )
    check(
        "Kernel.selection",
        "class RuntimeSelector" in kernel_text and "self.runtime_selector" in kernel_text,
        "Kernel owns RuntimeSelector wiring",
    )

    actor_path = SRC / "kernel" / "compile" / "actor_runtime.py"
    actor_members = _class_members(_class_node(actor_path, "ActorRuntime"))
    required_actor = {
        "context",
        "identity",
        "memory",
        "belief",
        "goal_queue",
        "resources",
        "authorization",
        "capabilities",
        "agents",
    }
    check(
        "ActorRuntime.actor_state",
        required_actor.issubset(actor_members),
        "ActorRuntime stores actor-local state and authorization views",
    )

    planetary_path = SRC / "kernel" / "society" / "integration.py"
    planetary_members = _class_members(_class_node(planetary_path, "PlanetaryRuntime"))
    required_planetary = {"_world", "_world_model", "_society_runtime"}
    check(
        "PlanetaryRuntime.world_owner",
        required_planetary.issubset(planetary_members),
        "PlanetaryRuntime owns SharedWorld through WorldModelRuntime",
    )
    check(
        "PlanetaryRuntime.world_routing",
        "self._world_model." in planetary_path.read_text(errors="ignore"),
        "Planetary world mutations route through WorldModelRuntime",
    )

    cognitive_path = SRC / "kernel" / "cognitive_os" / "cognitive_os.py"
    cognitive_members = _class_members(_class_node(cognitive_path, "CognitiveOS"))
    required_cognitive = {"evaluate_goals", "match_capabilities", "synthesize", "tick"}
    cognitive_text = cognitive_path.read_text(errors="ignore")
    check(
        "CognitiveOS.cognitive_loop",
        required_cognitive.issubset(cognitive_members),
        "CognitiveOS exposes the actor-facing reasoning and tick boundary",
    )
    check(
        "CognitiveOS.single_tick_delegate",
        "await tick(" in cognitive_text and 'getattr(actor, "tick"' in cognitive_text,
        "CognitiveOS delegates to the canonical actor cognitive tick",
    )

    agent_adapter = SRC / "runtime" / "agent_runtime.py"
    middleware_path = SRC / "runtime" / "agent_middleware.py"
    middleware_members = _class_members(_class_node(middleware_path, "AgentMiddleware"))
    check(
        "AgentRuntime.canonical_adapter",
        _alias_target(agent_adapter, "AgentRuntime") == "AgentMiddleware",
        "AgentRuntime reuses AgentMiddleware",
    )
    check(
        "AgentRuntime.orchestration",
        {"classifier", "router", "resolver"}.issubset(middleware_members),
        "AgentMiddleware owns classification, routing, and resolution",
    )

    capability_adapter = SRC / "kernel" / "pipeline" / "capability_runtime.py"
    executor_path = SRC / "kernel" / "pipeline" / "action_executor.py"
    executor_members = _class_members(_class_node(executor_path, "ActionExecutor"))
    check(
        "CapabilityRuntime.canonical_adapter",
        _alias_target(executor_path, "CapabilityRuntime") == "ActionExecutor",
        "CapabilityRuntime reuses ActionExecutor",
    )
    check(
        "CapabilityRuntime.execution",
        {"execute", "_execute_action", "_capability_bus"}.issubset(executor_members),
        "ActionExecutor owns capability invocation and outcomes",
    )

    passed = sum(item["passed"] for item in checks.values())
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "score": round((passed / len(checks)) * 100, 1) if checks else 0.0,
    }


def _untrusted_security_authority_violations() -> list[str]:
    """Catch trusted_auth.update(...) and similar merges from untrusted dicts."""
    findings: list[str] = []
    forbidden = (
        "trusted_auth.update(",
        "auth.update(",
    )
    skip = {"trusted_auth.py", "security_boundary.py"}
    for path in SRC.rglob("*.py"):
        if path.name in skip:
            continue
        text = path.read_text(errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for needle in forbidden:
                if needle in line:
                    findings.append(f"{path.relative_to(ROOT)}:{i}:{stripped}")
    return findings


def collect() -> dict:
    kernel = SRC / "kernel" / "kernel.py"
    names = _classes(kernel)
    hard = {
        "runtime_registry_definition": names.count("RuntimeRegistry") == 1,
        "agent_registry_definition": names.count("AgentRegistry") == 1,
        "capability_registry_definition": names.count("CapabilityRegistry") == 1,
        "runtime_selector_definition": names.count("RuntimeSelector") == 1,
        "agent_runtime_adapter": (SRC / "runtime" / "agent_runtime.py").exists(),
        "capability_runtime_adapter": (SRC / "kernel" / "pipeline" / "capability_runtime.py").exists(),
        "kernel_execute_boundary": "async def execute(" in kernel.read_text(),
    }
    exclusive = _exclusive_constructor_violations()
    hard["society_runtime_exclusive_to_planetary"] = not exclusive["SocietyRuntime"]
    hard["cognitive_os_exclusive_to_actor"] = not exclusive["CognitiveOS"]
    timeline_entity_names = {
        "Presence",
        "MembershipRecord",
        "GoalRecord",
        "BeliefRecord",
        "ExecutionRecord",
        "RelationshipRecord",
        "ActivityRecord",
        "IntentRecord",
        "PlanRecord",
        "DecisionRecord",
    }
    geo_entity_names = {
        name
        for name in exclusive
        if name
        not in (
            "SocietyRuntime",
            "CognitiveOS",
            "MemoryNode",
            "Membership",
            "Delegation",
        )
        and name not in timeline_entity_names
    }
    hard["geo_entities_exclusive_to_registry"] = not any(exclusive[name] for name in geo_entity_names)
    hard["timeline_entities_exclusive_to_store"] = not any(exclusive[name] for name in timeline_entity_names)
    hard["memory_node_exclusive_to_manager"] = not exclusive["MemoryNode"]
    hard["membership_exclusive_to_own_modules"] = not (exclusive["Membership"] or exclusive["Delegation"])

    boundary = _boundary_violations()
    hard["boundary_actor_runtime_bypass"] = not boundary["ActorRuntime"]
    hard["boundary_agent_runtime_bypass"] = not (boundary["AgentRuntime"] or boundary["AgentMiddleware"])
    hard["boundary_capability_runtime_bypass"] = not boundary["ActionExecutor"]
    hard["boundary_society_no_direct_cognitive_os"] = not boundary["society_no_direct_cognitive_os"]
    hard["boundary_no_module_level_cognitive_imports"] = not boundary["no_module_level_cognitive_imports"]
    hard["untrusted_security_authority_assignments"] = not _untrusted_security_authority_violations()
    hard["governed_commitment_on_action_executor"] = "ensure_governed" in (
        SRC / "kernel" / "pipeline" / "action_executor.py"
    ).read_text(errors="ignore")
    hard["governed_commitment_on_runtime"] = "ensure_governed" in (SRC / "runtime" / "runtime.py").read_text(
        errors="ignore"
    )
    hard["kg_mutations_require_commitment"] = "assert_state_mutation_allowed" in (
        SRC / "kernel" / "knowledge_graph.py"
    ).read_text(errors="ignore")
    hard["shared_world_mutations_require_commitment"] = "_require_write" in (
        SRC / "kernel" / "society" / "world.py"
    ).read_text(errors="ignore")
    hard["operation_classification_default_critical"] = "return OperationClass.SECURITY_CRITICAL" in (
        SRC / "kernel" / "operation_classification.py"
    ).read_text(errors="ignore")
    hard["mutating_routes_idempotent"] = _mutating_routes_have_idempotent()
    hard["security_operation_unknown_state"] = "RECONCILIATION_REQUIRED" in (
        SRC / "kernel" / "security_operation.py"
    ).read_text(errors="ignore")
    hard["policy_audit_intent_precedes_effect"] = _policy_audit_intent_precedes_effect()
    hard["policy_unknown_distinct_from_failed"] = _policy_unknown_distinct_from_failed()

    debt = {
        "direct_runtime_construction": _production_runtime_construction(),
        "capability_bus_implementations": _capability_bus_implementations(),
        "exclusive_constructor_violations": exclusive,
        "boundary_violations": boundary,
    }
    positive = _positive_ownership_checks()
    return {
        "hard_checks": hard,
        "hard_failures": [name for name, passed in hard.items() if not passed],
        "positive_ownership": positive,
        "compatibility_debt": debt,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--strict", action="store_true", help="fail on hard invariant violations")
    args = parser.parse_args()
    result = collect()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Architecture conformance gate")
        for name, passed in result["hard_checks"].items():
            print(f"  {'PASS' if passed else 'FAIL'} {name}")
        print(
            f"  INFO direct runtime constructions: {len(result['compatibility_debt']['direct_runtime_construction'])}"
        )
        print(
            f"  INFO capability bus implementations: {len(result['compatibility_debt']['capability_bus_implementations'])}"
        )
        ownership = result["positive_ownership"]
        print(f"  INFO positive ownership: {ownership['passed']}/{ownership['total']} ({ownership['score']:.1f}%)")
        for name, item in ownership["checks"].items():
            print(f"  {'PASS' if item['passed'] else 'FAIL'} ownership {name}")
    return 1 if args.strict and result["hard_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
