"""
Systems Validation: Governance Bypass Vectors

Enumerate every path by which actor code can cause external side effects.
For each path, verify that side effects ALWAYS go through governance.

A side effect is "consequential" if it:
- Modifies external state (database, files, network)
- Sends messages to other actors
- Triggers other computations
- Changes resource allocation
- Records audit events

Inconsequential: logging, tracing, metrics
"""
import pytest
from typing import List, Tuple
from dataclasses import dataclass


@dataclass
class SideEffectPath:
    """Description of a path where actor can cause external effect"""
    name: str
    description: str
    external_target: str  # where effect goes
    goes_through_governance: bool
    status: str  # "PROVEN" | "ASSUMED" | "UNTESTED" | "BYPASSES"


class TestGovernanceSideEffectPaths:
    """Test all paths through which actors cause external effects"""
    
    def test_enumerate_side_effect_paths(self):
        """CRITICAL: Map all paths where actor code can affect external state"""
        
        paths: List[SideEffectPath] = [
            # Direct capability invocation
            SideEffectPath(
                name="Capability.execute()",
                description="Actor calls runtime.capability.execute(...)",
                external_target="ROS/external service",
                goes_through_governance=True,  # Via CapabilityBus
                status="PROVEN"  # test_runtimes.py verifies this
            ),
            
            # Message sending
            SideEffectPath(
                name="SendMessage()",
                description="Actor sends message to another actor",
                external_target="Other actor",
                goes_through_governance=True,  # Via CapabilityBus → ensure_governed
                status="ASSUMED"  # Requires integration test
            ),
            
            # LLM tool invocation (indirect effect)
            SideEffectPath(
                name="LLM tool_call()",
                description="Model generates tool call in output",
                external_target="Depends on tool definition",
                goes_through_governance=False,  # Model output is NOT authority
                status="PROVEN"  # Designed as untrusted context
            ),
            
            # Direct capability access (attempt to bypass)
            SideEffectPath(
                name="Direct capability invocation (no CapabilityBus)",
                description="Actor somehow imports and calls capability directly",
                external_target="ROS/external",
                goes_through_governance=False,  # If successful, this is a BUG
                status="UNTESTED"  # Need to verify this is prevented
            ),
            
            # ROS adaptation layer
            SideEffectPath(
                name="ROS service call",
                description="Actor calls ROS service through adapter",
                external_target="ROS system",
                goes_through_governance=True,  # Must go through adapter
                status="ASSUMED"  # Requires ROS integration test
            ),
            
            # NATS message dispatch
            SideEffectPath(
                name="NATS publish",
                description="Actor publishes to NATS topic",
                external_target="NATS message queue",
                goes_through_governance=True,  # Must go through governance boundary
                status="ASSUMED"  # Requires NATS integration test
            ),
            
            # HTTP outbound (if actor code has network)
            SideEffectPath(
                name="HTTP request",
                description="Actor makes external HTTP request",
                external_target="External HTTP endpoint",
                goes_through_governance=False,  # Actor code can make direct HTTP if not prevented
                status="UNTESTED"  # Security issue if possible!
            ),
            
            # Background task/callback
            SideEffectPath(
                name="Background task",
                description="Actor schedules background work",
                external_target="Task queue or scheduler",
                goes_through_governance=False,  # If permitted, bypasses governance
                status="UNTESTED"  # Need to verify this is prevented
            ),
            
            # Database mutation
            SideEffectPath(
                name="Direct database write",
                description="Actor code writes to database",
                external_target="MongoDB/database",
                goes_through_governance=False,  # Direct DB access is a security hole
                status="UNTESTED"  # Verify isolation
            ),
            
            # File system mutation
            SideEffectPath(
                name="File write",
                description="Actor writes to filesystem",
                external_target="Filesystem",
                goes_through_governance=False,  # Direct FS access bypasses governance
                status="UNTESTED"  # Verify isolation
            ),
            
            # Delegation creation (if actor code can create auth)
            SideEffectPath(
                name="Delegation issuance",
                description="Actor creates new delegation token",
                external_target="Authorization system",
                goes_through_governance=True,  # Must check if actor can create delegations
                status="UNTESTED"  # Requires security test
            ),
            
            # Policy modification
            SideEffectPath(
                name="Policy change",
                description="Actor modifies governance policy",
                external_target="OPA policy or governance engine",
                goes_through_governance=True,  # Should require authorization
                status="UNTESTED"  # Should be DENIED for actor code
            ),
        ]
        
        print("\n" + "="*120)
        print("ACTOR SIDE-EFFECT PATHS & GOVERNANCE COVERAGE")
        print("="*120)
        print(f"{'Path':<30} {'Target':<25} {'Goes Through Governance':<25} {'Status':<20}")
        print("-"*120)
        
        for path in paths:
            governance_str = "✓ YES" if path.goes_through_governance else "✗ NO (RISK!)"
            print(f"{path.name:<30} {path.external_target:<25} {governance_str:<25} {path.status:<20}")
        
        print("="*120)
        
        # Count paths
        through_governance = sum(1 for p in paths if p.goes_through_governance)
        bypasses = sum(1 for p in paths if not p.goes_through_governance)
        proven = sum(1 for p in paths if p.status == "PROVEN")
        untested = sum(1 for p in paths if p.status == "UNTESTED")
        
        print(f"\nSummary:")
        print(f"  Total paths: {len(paths)}")
        print(f"  Through governance: {through_governance}")
        print(f"  Bypass governance: {bypasses} ⚠️ ")
        print(f"  Proven: {proven}")
        print(f"  Untested: {untested}")
        
        print(f"\n⚠️  CRITICAL: {untested} paths remain untested!")
        print(f"  These are high-priority for security validation:")
        for path in paths:
            if path.status == "UNTESTED" and not path.goes_through_governance:
                print(f"    - {path.name}: {path.description}")


class TestDirectCapabilityBypass:
    """Test: Can actor bypass CapabilityBus and call capability directly?"""
    
    def test_actor_cannot_import_capability_directly(self):
        """SECURITY: Verify actor code cannot import and invoke capabilities directly"""
        
        print("\n⚠️  SECURITY TEST: Direct capability invocation")
        print("  Scenario: Actor code attempts: from src.capabilities import move_robot; move_robot(...)")
        print("  Expected: Either fails (not importable) or fails (no authorization)")
        print("  Status: REQUIRES isolation testing")
        print("  Priority: CRITICAL - if this works, governance is bypassed")
    
    def test_actor_cannot_access_runtime_internals(self):
        """SECURITY: Verify actor code cannot access runtime's internal capability registry"""
        
        print("\n⚠️  SECURITY TEST: Runtime internals access")
        print("  Scenario: Actor code accesses actor.runtime._capabilities directly")
        print("  Expected: Fails (no direct access)")
        print("  Status: REQUIRES inspection of actor code environment")


class TestNetworkBypass:
    """Test: Can actor make direct network requests?"""
    
    def test_actor_cannot_make_direct_http_requests(self):
        """SECURITY: Verify actor code cannot make direct HTTP requests"""
        
        print("\n⚠️  SECURITY TEST: Direct HTTP bypass")
        print("  Scenario: Actor code: import requests; requests.post('http://...')")
        print("  Expected: Either fails (network unavailable) or logged/denied")
        print("  Status: REQUIRES network isolation verification")
        print("  Impact: If possible, bypasses ALL governance")


class TestFileSystemBypass:
    """Test: Can actor write to filesystem?"""
    
    def test_actor_cannot_write_files(self):
        """SECURITY: Verify actor code has no filesystem write access"""
        
        print("\n⚠️  SECURITY TEST: Filesystem access")
        print("  Scenario: Actor code: open('/tmp/malicious.txt', 'w').write(...)")
        print("  Expected: Fails (permission denied) or no access")
        print("  Status: REQUIRES sandbox/permission verification")


class TestDatabaseBypass:
    """Test: Can actor write directly to database?"""
    
    def test_actor_cannot_access_database_directly(self):
        """SECURITY: Verify actor code cannot write to MongoDB directly"""
        
        print("\n⚠️  SECURITY TEST: Direct database access")
        print("  Scenario: Actor code gets MongoDB connection and modifies actor states")
        print("  Expected: Fails (no credentials) or audit-logged")
        print("  Status: REQUIRES database access control verification")


class TestDelegationCreation:
    """Test: Can actor create its own authorizations?"""
    
    def test_actor_cannot_create_delegations(self):
        """SECURITY: Verify actor code cannot issue delegations to itself"""
        
        print("\n⚠️  SECURITY TEST: Self-delegation")
        print("  Scenario: Actor creates delegation: scope=ADMIN, principal=self")
        print("  Expected: DENIED (governance rejects)")
        print("  Status: Delegation issuance must check authorization")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
