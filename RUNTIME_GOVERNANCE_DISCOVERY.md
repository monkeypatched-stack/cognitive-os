# RUNTIME APPROVAL GATE DISCOVERY

## EXECUTIVE SUMMARY

The CognitiveOS runtime has **already implemented a sophisticated security boundary** for governing all mutations. The architecture exists and is operational. The task is to **extend the existing governance engine to emit explicit approval decisions** (AUTO_APPROVE / HUMAN_APPROVAL_REQUIRED / DENY) and make those decisions available as trust-bound ApprovalArtifacts that can be used to authorize agent-to-agent operations.

Current state: **governance evaluates all mutations against OPA policy**, but does not distinguish between "policy permits this automatically" vs. "human must approve this." This distinction is required for agent-to-agent governance.

---

## DISCOVERED RUNTIME ARCHITECTURE

### 1. AGENT COMMUNICATION PATH

**Request Entry Point:**
```
Agent A → Interaction.REQUEST (kernel/society/interaction.py)
        ↓
        InteractionManager (in-memory request tracking)
        ↓
        Agent message propagated via shared context stream
        ↓
        Agent B observes request (during its tick)
        ↓
        Agent B proposes response/action
```

**Delegation Entry Point:**
```
Agent A → Delegation.grant() (kernel/society/delegation.py)
        ↓
        DelegationRegistry (in-memory delegation tracking)
        ↓
        Delegates permissions + constraints to Actor B
        ↓
        timeline event recorded (immutable)
```

**Execution Entry Point:**
```
Agent → execute_actor_request() (kernel/society/integration.py:4275)
  │
  ├─ resolve actor geography
  ├─ resolve actor societies
  ├─ acquire planetary tick lock
  └─ _run_actor_tick()
```

**Location:** `src/monkey_brain/kernel/society/integration.py:4275` - `execute_actor_request()`

---

### 2. AUTHENTICATION SOURCE

**Current Implementation:**
```python
TrustedAuthEvidence (kernel/trusted_auth.py)
  ├─ authenticated: bool
  ├─ token_valid: bool
  ├─ principal_id: str
  ├─ principal_type: str (human | service | unknown)
  ├─ mfa_status: str
  ├─ session_id: str
  └─ permissions: tuple[str, ...]
```

**Authentication is extracted from:**
1. **JWT tokens** (validated, not self-reported)
2. **Session credentials** (trusted infrastructure)
3. **Service credentials** (bound to runtime identity)

**CRITICAL:** Agent's self-reported `agent_id` from message ≠ authenticated principal
- `TrustedAuthEvidence.principal_id` = authenticated identity
- `agent_id` in message = proposed operation by unknown caller
- Governance gates use `TrustedAuthEvidence`, never trusting message-supplied identity

**Location:** `src/monkey_brain/kernel/trusted_auth.py` lines 1-82

---

### 3. AUTHORIZATION/OPA EVALUATION

**OPA Policy Evaluation:**
```python
GovernanceEngine.evaluate()  (kernel/governance.py:146)
  │
  ├─ Input: runtime_id, action, context (from TrustedAuthEvidence)
  ├─ OPA query: POST /v1/data/agentos/governance
  │  (via services/common/opa.py::evaluate_full())
  │
  └─ Output: {
       "allowed": bool,
       "reason": str,
       "violations": list[dict]
     }
```

**Policy Decision Tree:**
```
OPA receives:
  {
    "runtime_id": authenticated_principal_id,
    "action": action_name,
    "context": {
      "trusted_auth": {
        "authenticated": bool,
        "principal": str,
        "principal_type": "human" | "service",
        "mfa_status": str,
        ...
      }
    }
  }

OPA evaluates:
  package agentos.governance (opa/policies/agentos_governance.rego)
  
  Returns:
    allowed: true/false
    reason: policy_key | deny_reason
```

**Current decision output:** Boolean (allowed/denied only)
**Missing:** APPROVAL MODE (AUTO_APPROVE vs HUMAN_APPROVAL_REQUIRED vs DENY)

**Location:** `src/monkey_brain/kernel/governance.py:146` - `GovernanceEngine.evaluate()`

---

### 4. EXECUTION BOUNDARY

**Governance gate function:**
```python
ensure_governed()  (kernel/security_boundary.py:838)
  │
  ├─ Operation classification (READ_ONLY, PROPOSAL_ONLY, MUTATION)
  ├─ If MUTATION:
  │  └─ run_governed_mutation()
  │     │
  │     ├─ AUTH: assert authenticated
  │     ├─ AUTHZ: await GovernanceEngine.evaluate()
  │     ├─ IDEMPOTENCY: check SecurityOperation ledger
  │     ├─ AUDIT_INTENT: record intent (append-only)
  │     ├─ MUTATION: execute effect()
  │     └─ AUDIT_RESULT: record result (append-only)
  │
  └─ Nested calls inside active commitment skip gate
```

**All mutations funnel through this single gate:**
- CapabilityBus.execute() → `ensure_governed()` (kernel/execute/capability_bus.py:77)
- actor.tick() → `ensure_governed()` (actor_runtime.py:593)
- payments.webhook() → `ensure_governed()` (api/routes/payments.py:184)
- Every security-sensitive operation in the codebase

**Location:** `src/monkey_brain/kernel/security_boundary.py:838` - `ensure_governed()`

---

### 5. STATE MUTATION BOUNDARY

**Execution Attempt Ledger:**
```python
SecurityOperation  (kernel/security_operation.py)
  ├─ operation_id: str
  ├─ action: str
  ├─ resource: str
  ├─ state: AUTHORIZED | AUDIT_INTENT_RECORDED | EXECUTING | SUCCEEDED | FAILED | UNKNOWN
  ├─ principal_id: str
  ├─ policy_decision: str
  └─ timestamp: float
```

**Execution Attempt Store:**
```python
ExecutionAttempt  (kernel/execution_attempt.py)
  ├─ attempt_id: str
  ├─ operation_id: str
  ├─ state: EXECUTING | SUCCEEDED | FAILED | UNKNOWN | RECONCILIATION_REQUIRED
  ├─ evidence: dict (non-mutable audit trail)
  └─ reconciliation_state: dict
```

**State Mutation Guard:**
```python
assert_state_mutation_allowed()  (security_boundary.py:116)
  └─ Raises SecurityBoundaryDenied unless:
       commitment_active() OR
       privileged_infrastructure_active() OR
       insecure_dev_mode()
```

**Invariant:** Ungoverned writes to persistent state are impossible in production

**Location:** `src/monkey_brain/kernel/security_operation.py` + `src/monkey_brain/kernel/execution_attempt.py`

---

### 6. AUDIT TRAIL

**Two Separate Audit Systems:**

**A) Security Audit Trail (production-critical):**
```python
AuditLog  (kernel/audit.py)
  ├─ MongoAuditStore (durable append-only)
  │  └─ MongoDB collection: audit_records
  └─ MemoryDurableAuditStore (process-local fallback)

Events captured:
  - execute (state mutation)
  - governance (policy decision)
  - security (auth/authz)
  - auth (login/token)
  - world_mutation (world state change)
  - plan
  - authorization
  - policy
  - login
  - token
```

**B) Governance Decisions (runtime audit):**
```python
GovernanceEngine._decisions  (governance.py)
  └─ list of recent policy decisions:
       [
         {
           "runtime_id": principal_id,
           "action": action,
           "allowed": bool,
           "reason": str,
           "timestamp": float
         },
         ...
       ]
```

**Audit Ordering Invariant:**
```
APPROVAL GRANTED
    ↓
AUDIT INTENT DURABLE (MongoDB)
    ↓
EXECUTION PERMITTED
    ↓
STATE MUTATION
    ↓
AUDIT RESULT DURABLE
```

**Audit Failure Mode:** NO EXECUTION (fail-closed)

**Location:** `src/monkey_brain/kernel/audit.py` + `src/monkey_brain/kernel/security_boundary.py`

---

### 7. AGENT-TO-AGENT COMMUNICATION PATHS

**Path 1: Agent-to-Agent Request (Lightweight)**
```
Agent A
  ↓
send_interaction(InteractionType.REQUEST, actor_id=B, proposal={...})
  ↓
InteractionManager.create_interaction()  (in-memory)
  ↓
Context stream propagation (shared world)
  ↓
Agent B observes during tick()
  ├─ No automatic execution
  ├─ B decides to accept/decline
  └─ B may propose counter-interaction
```

**No governance gate needed:** Just communication, not execution authorization

---

**Path 2: Agent-to-Agent Delegation (Lightweight)**
```
Agent A
  ↓
delegation_registry.grant(
  membership_id=A_membership,
  delegate_actor_id=B,
  permissions=(...),
  valid_until=...
)
  ↓
DelegationRegistry._delegations  (in-memory)
  ↓
Membership timeline event recorded
  ↓
Agent B can now use delegated permissions
```

**No governance gate needed:** Delegation is administrative, not operational

---

**Path 3: Agent-to-Agent Capability Execution (REQUIRES GATE)**
```
Agent A
  ↓
capability_bus.execute(name="B.operation", state={...})
  ↓
CapabilityBus._execute_resolved()
  │
  ├─ ensure_governed("capability.operation", name, _run)
  │
  └─ _run()
     │
     ├─ AUTH: verify principal
     ├─ AUTHZ: OPA evaluate
     ├─ IDEMPOTENCY: check ledger
     ├─ AUDIT_INTENT: record
     ├─ MUTATION: execute
     └─ AUDIT_RESULT: record
```

**This path ALREADY gates execution through ensure_governed()**

---

### 8. NARROWEST TRUSTWORTHY GOVERNANCE INTERCEPTION POINT

**Current Single Gate:**
```
ensure_governed()  (security_boundary.py:838)
  └─ All mutations funnel here
     └─ run_governed_mutation()  (559)
        └─ _authorize() → GovernanceEngine.evaluate()
           └─ OPA policy decision
```

**Why this is correct:**
1. **Before this gate:** untrusted agent proposals can still be made
2. **At this gate:** authenticated principal + OPA policy + execution ledger converge
3. **After this gate:** only durable audit + permitted execution
4. **No bypass:** architecture requires privileged_infrastructure() context or commitment_active()

**Key invariant:** `assert_state_mutation_allowed()` blocks all direct state writes outside this context

---

### 9. CAPABILITY BUS AS THE AGENT-TO-AGENT EXECUTION ENTRY POINT

**CapabilityBus routing (kernel/execute/capability_bus.py:42-120):**
```python
resolve(name: str)
  ├─ Try: Runtime.get_capability(name)      (in-process)
  ├─ Try: AgentBus.resolve_agent(name)     (local agents)
  ├─ Try: ProviderRegistry.find_agent(name) (external agents)
  └─ Source labels which tier answered

execute(name: str, state: dict)
  ├─ ensure_governed(f"capability.{name}", name, _execute_resolved)
  │
  └─ _execute_resolved()
     ├─ capability.execute(state)   OR
     ├─ agent_bus.execute(name, **state)   OR
     └─ provider_registry.execute_agent(name, state)
```

**This is where Agent B's capability is actually invoked and is ALREADY gated.**

---

## RECOMMENDED APPROVAL GATE DESIGN

### Primary Gate Location: `GovernanceEngine.evaluate()` Output

**Extend the policy decision from:**
```python
{"allowed": bool, "reason": str, "violations": list}
```

**To:**
```python
{
    "allowed": bool,
    "reason": str,
    "violations": list,
    # NEW: Approval mode
    "approval_mode": "AUTO_APPROVE" | "HUMAN_APPROVAL_REQUIRED" | "DENY",
    # NEW: Approval source
    "approval_source": "POLICY_AUTOMATIC" | "HUMAN" | "NONE",
    # NEW: Policy decision details
    "policy_rule": str,  # which OPA rule matched
    "requires_hitl": bool,  # policy requires HITL
    "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
}
```

### Secondary Gate: ApprovalArtifact Validation

**Create at the moment AUTO_APPROVE is issued:**
```python
ApprovalArtifact (new)
  ├─ approval_id: str
  ├─ operation_id: str
  ├─ approval_source: "POLICY_AUTOMATIC" | "HUMAN"
  ├─ requesting_principal: str (authenticated)
  ├─ target_resource: str
  ├─ operation_class: str
  ├─ scope: dict
  ├─ approved_at: float
  ├─ expires_at: float
  ├─ policy_decision: str
  ├─ policy_revision: str
  ├─ integrity_signature: str
  └─ immutable: bool
```

---

## WHY THIS IS THE CORRECT BOUNDARY

1. **Already gated:** CapabilityBus (agent-to-agent execution) already calls `ensure_governed()`
2. **Single chokepoint:** All mutations funnel through `ensure_governed()` → `run_governed_mutation()` → `_authorize()` → `GovernanceEngine.evaluate()`
3. **Authenticated principal:** TrustedAuthEvidence provided, not agent-supplied
4. **OPA already evaluates:** Policy is already evaluated; we just need to expose the mode
5. **Audit already durable:** Audit intent already persisted before execution
6. **Fail-closed already enforced:** Any error in pipeline denies execution
7. **No bypass paths:** Architecture prevents direct state mutation outside this context

---

## CURRENT STATE: WHAT EXISTS

✅ **Already Implemented:**
- TrustedAuthEvidence: Authenticated principal (JWT, session, service credentials)
- GovernanceEngine: OPA policy evaluation
- ensure_governed(): Single mutation gate
- run_governed_mutation(): AUTH → AUTHZ → IDEMPOTENCY → AUDIT_INTENT → MUTATION → AUDIT_RESULT pipeline
- SecurityOperation: Execution ledger (immutable)
- ExecutionAttempt: State mutation tracking
- AuditLog: Append-only audit trail (MongoDB)
- CapabilityBus: Agent-to-agent capability dispatch (already gated)
- Delegation: Authority delegation with timeline
- Interaction: Agent-to-agent communication (lightweight)

✅ **Security Properties Already Enforced:**
- All mutations gated at single boundary
- Fail-closed behavior (unknown = deny)
- Audit-before-execution invariant
- OPA policy as source of truth
- Untrusted security signals stripped
- Agent-supplied metadata cannot override OPA
- MFA status tracked (but not decision-gated)

---

## MISSING: WHAT NEEDS TO BE ADDED

❌ **Not Implemented:**
1. **Approval Modes:** OPA can only return allowed/denied, not AUTO_APPROVE vs HUMAN_REQUIRED vs DENY
2. **ApprovalArtifact:** No data model for storing automatic approvals
3. **Approval Provenance:** No way to record why approval was granted
4. **Approval Validation:** No gate to validate ApprovalArtifact at execution time
5. **Human Approval Flow:** No mechanism to escalate HUMAN_APPROVAL_REQUIRED operations
6. **Approval Expiration:** No time-bound validation before execution
7. **Approval Scope:** No fine-grained capability boundaries in approval
8. **Agent Self-Approval Prevention:** No explicit block of agent approving own operations
9. **Approval Reuse:** No safe reuse logic across operations

---

## OPA POLICY INTERFACE REQUIRED

**Current OPA output:** `allowed: true/false`

**Required OPA output:** New fields in agentos_governance.rego:
```rego
approval_mode = "AUTO_APPROVE" | "HUMAN_APPROVAL_REQUIRED" | "DENY"
requires_hitl = true | false
risk_level = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
policy_rule = "rule_name"
```

**If OPA cannot distinguish these, STOP and report smallest required policy change.**

---

## REMAINING QUESTIONS FOR IMPLEMENTATION PHASE

1. Where should ApprovalArtifact be stored? (MongoDB? Redis? In-memory delegation registry?)
2. How long should AUTO_APPROVE artifacts be valid? (1 minute? Duration of operation?)
3. Should approval be per-operation or per-capability per principal per window?
4. Who can create human approvals? (Only certain principals? Authenticated humans?)
5. Should capability bus also validate ApprovalArtifact or is GovernanceEngine.evaluate() sufficient?
6. What is the mechanism for HUMAN_APPROVAL_REQUIRED escalation? (Webhook? Queue? UI?)

---

## PHASE 1 COMPLETE

✅ **No code modified**
✅ **All boundaries discovered**
✅ **All components catalogued**
✅ **Recommended approval gate located: `GovernanceEngine.evaluate()` output**
✅ **Extension path clear: Add approval_mode, ApprovalArtifact, approval validation**

**READY FOR PHASE 2 (Implementation)**

**Awaiting authorization:** `RUNTIME_APPROVAL_GATE_APPROVED`
