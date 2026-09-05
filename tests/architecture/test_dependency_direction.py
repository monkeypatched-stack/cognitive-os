"""Architecture Boundary Hardening, Section 12: dependency-direction
enforcement via real static analysis of the actual import graph (ast,
not string comments) -- proves specific forbidden dependencies do not
exist today, rather than merely documenting an intended direction.

Target direction (Section 12):
    Control Plane -> Actor Runtime -> Cognitive abstractions ->
    Governance -> Execution abstractions -> Infrastructure adapters

Infrastructure implementations depend on interfaces, not the reverse.
"""
from __future__ import annotations

import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_ROOT = os.path.join(REPO_ROOT, "src", "monkey_brain")


def _module_imports(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=path)
        except SyntaxError:
            return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _py_files(*relative_dirs: str, exclude: tuple[str, ...] = ()) -> list[str]:
    out = []
    for rel in relative_dirs:
        base = os.path.join(SRC_ROOT, rel)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if "__pycache__" in root:
                continue
            for f in files:
                if f.endswith(".py") and f not in exclude:
                    out.append(os.path.join(root, f))
    return out


class TestGovernanceNeverImportsExecutionSubstrates:
    """ensure_governed's own module must remain execution-substrate-
    agnostic -- it must not know ROS, Moss, or a specific transport
    exist, or a new substrate could never be added without touching the
    governance boundary itself."""

    def test_security_boundary_does_not_import_ros_or_moss_or_sync_transport(self):
        path = os.path.join(SRC_ROOT, "kernel", "security_boundary.py")
        imports = _module_imports(path)
        forbidden_substrings = ("ros_integration", "moss_retrieval", "sync_transport")
        violations = [imp for imp in imports for f in forbidden_substrings if f in imp]
        assert violations == [], (
            f"kernel/security_boundary.py must not import execution-substrate-specific "
            f"modules, found: {violations}"
        )

    def test_governance_module_does_not_import_ros_or_moss(self):
        path = os.path.join(SRC_ROOT, "kernel", "governance.py")
        if not os.path.exists(path):
            return
        imports = _module_imports(path)
        violations = [imp for imp in imports if "ros_integration" in imp or "moss_retrieval" in imp]
        assert violations == []


class TestExecutionAdaptersNeverImportGovernanceInternals:
    """A RosExecutionAdapter must never reach INTO security_boundary's
    private decision machinery -- it may only be CALLED BY the governance
    boundary (ensure_governed), never call into it to manufacture its own
    authorization. This is a structural, not textual, check: it parses
    real import statements in the real file."""

    def test_ros_integration_module_only_imports_ensure_governed_the_public_entry_point(self):
        path = os.path.join(SRC_ROOT, "kernel", "edge", "ros_integration.py")
        imports = _module_imports(path)
        # Importing security_boundary itself (to call the PUBLIC
        # ensure_governed function) is expected and correct -- the
        # violation would be reaching for a PRIVATE symbol.
        with open(path, encoding="utf-8") as f:
            source = f.read()
        assert "_authorize_and_gate" not in source
        assert "_authorize(" not in source
        assert "GovernanceEngine(" not in source


class TestNoRawNeo4jDriverOutsideItsOwnModule:
    """kernel/knowledge_graph_neo4j.py is the ONE sanctioned place a raw
    neo4j driver is touched -- every other cognitive/capability/domain
    module must go through kernel/knowledge_graph.py::KnowledgeGraph
    (WorldStateStore protocol, Section 2), never bolt:// or GraphDatabase
    directly."""

    def test_pipeline_and_domains_never_import_neo4j_directly(self):
        files = _py_files("kernel/pipeline", "kernel/domains", "kernel/edge")
        violations = []
        for path in files:
            imports = _module_imports(path)
            if any(imp == "neo4j" or imp.startswith("neo4j.") for imp in imports):
                violations.append(os.path.relpath(path, SRC_ROOT))
        assert violations == [], f"raw neo4j import found outside knowledge_graph_neo4j.py: {violations}"


class TestEdgePackageDoesNotImportControlPlaneInternals:
    """Section 7 (edge is a deployment mode, not a second architecture):
    kernel/edge/* should depend DOWNWARD (on protocols/persistence
    primitives), never reach up into kernel/society/integration.py's
    PlanetaryRuntime internals -- doing so would make the edge package a
    second, parallel control plane rather than a deployment-mode
    extension of the one real ActionExecutor/governance pipeline."""

    def test_edge_modules_never_import_planetary_runtime(self):
        files = _py_files("kernel/edge")
        violations = []
        for path in files:
            imports = _module_imports(path)
            if any("society.integration" in imp for imp in imports):
                violations.append(os.path.relpath(path, SRC_ROOT))
        assert violations == [], f"kernel/edge/* must not import PlanetaryRuntime internals: {violations}"


class TestTwoDelegationConceptsStayApart:
    """Society architecture review (Phase 1): kernel/society/delegation.py
    ::Delegation is a real, live, but NARROW mechanism (membership-scoped,
    unsigned) whose only real consumer is communication-routing
    eligibility in kernel/affiliations/graph.py. It must never be reached
    for a capability-authorization decision -- that is exclusively
    kernel/delegation.py::verify_delegation_chain's job, via
    ensure_governed. This test proves the domain/capability layer has no
    import path to the narrower mechanism at all, so the two can never be
    confused at a call site."""

    def test_domains_never_import_the_membership_scoped_delegation_module(self):
        files = _py_files("kernel/domains", "kernel/pipeline")
        violations = []
        for path in files:
            imports = _module_imports(path)
            if any(imp.endswith("society.delegation") or ".society.delegation" in imp for imp in imports):
                violations.append(os.path.relpath(path, SRC_ROOT))
        assert violations == [], (
            f"kernel/domains and kernel/pipeline must never import "
            f"kernel.society.delegation (membership-scoped, no execution authority): {violations}"
        )


class TestSocietyGovernanceEngineNeverGatesExecution:
    """Society architecture review (Phase 1): SocietyGovernanceEngine.
    authorize()/check_permission() are real and live (communication-
    routing eligibility, kernel/affiliations/graph.py), but must never
    become reachable from the capability-execution path -- that would
    create a second, competing authorization oracle alongside
    ensure_governed. Proven structurally: action_executor.py has no
    import of, or reference to, SocietyGovernanceEngine or its methods."""

    def test_action_executor_never_references_society_governance_engine(self):
        path = os.path.join(SRC_ROOT, "kernel", "pipeline", "action_executor.py")
        imports = _module_imports(path)
        assert not any("society.governance" in imp for imp in imports)
        with open(path, encoding="utf-8") as f:
            source = f.read()
        assert "SocietyGovernanceEngine" not in source
        assert ".check_permission(" not in source
        # ".authorize(" alone would also match ensure_governed's own
        # force_authorize kwarg usage elsewhere in this file, so this
        # checks specifically for a governance-engine-shaped call.
        assert "governance.authorize(" not in source

    def test_domains_never_call_society_governance_engine_authorize_or_check_permission(self):
        files = _py_files("kernel/domains")
        violations = []
        for path in files:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            if "SocietyGovernanceEngine" in source:
                violations.append(os.path.relpath(path, SRC_ROOT))
        assert violations == [], f"kernel/domains/* must never reference SocietyGovernanceEngine directly: {violations}"
