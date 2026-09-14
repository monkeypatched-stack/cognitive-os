# Approval Decision Contract — Canonical Definition

**Status**: Definition Phase (Discovery Complete, Contract Formalized)  
**Not Yet Wired to Runtime**: This contract defines the interface only. Implementation of the runtime gate requires separate authorization: `RUNTIME_APPROVAL_GATE_WIRING_APPROVED`.

---

## 0. PURPOSE

This contract sits between trusted policy evaluation and the runtime execution gate:

```
authenticated request
        ↓
authentication (TrustedAuthEvidence)
        ↓
authorization (OPA via GovernanceEngine.evaluate)
        ↓
ApprovalDecision (THIS CONTRACT)
        ↓
ApprovalArtifact (HUMAN/AUTO approval grant)
        ↓
runtime execution gate
        ↓
execution (mutation)
```

**The purpose**: Eliminate ambiguity about what constitutes an approval, who may issue one, what the runtime may do with it, and how automatic approval differs from human approval.

---

## 1. CANONICAL DECISION TYPES

Exactly three terminal approval modes:

```python
class ApprovalMode(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    DENY = "DENY"
```

No ambiguous states: TRUE, FALSE, PENDING, UNKNOWN, MAYBE, TRUSTED.

Non-terminal processing states (if needed) are kept separate from the approval decision itself.

---

## 2. DECISION SEMANTICS

### AUTO_APPROVE

**Definition**:
> The trusted governance system evaluated the authenticated request and policy and determined that the operation may proceed without human approval, subject to all remaining execution gates.

**Implies**:
- Trusted policy evaluation succeeded
- Operation has no special HITL requirement in policy
- Authentication is valid
- All trust prerequisites met
- Execution gate may proceed with the operation (subject to other runtime controls: scope, state, audit, idempotency)

**Does NOT imply**:
- Agent requested it
- Automatic execution without further security controls
- Bypass of other runtime gates

### HUMAN_APPROVAL_REQUIRED

**Definition**:
> Policy permits the operation only after an authorized human explicitly approves the specific requested scope.

**Implies**:
- Trusted policy evaluation succeeded AND determined escalation is needed
- Operation is blocked until valid human approval exists
- An authorized human (not the requesting agent) must explicitly approve
- Approval is bound to the specific operation, scope, and principal
- Approval is time-bound

**Does NOT imply**:
- Automatic approval if agent claims "I have approval"
- Agent-to-agent communication as human approval
- Policy silence as permission

### DENY

**Definition**:
> The operation must not execute.

**Implies**:
- Terminal decision for this policy evaluation
- No downstream component may weaken this decision
- Execution is blocked

**Does NOT imply**:
- Human can override this
- Appeal mechanism exists (requires new policy evaluation)

---

## 3. AGENTS CANNOT ISSUE DECISIONS

**Forbidden** decision sources:

```python
# WRONG: Agent output becomes policy decision
agent_response.approval = "AUTO_APPROVE"

# WRONG: Agent assertion in request
request.approved = True

# WRONG: LLM declares itself
llm_output["decision"] = "APPROVED"

# WRONG: Message content
{"message": "I approve this", "decision": "AUTO_APPROVE"}
```

**Allowed** agent outputs (input to policy engine, NOT the decision):
- request
- proposal
- intent
- requested operation
- scope / capabilities
- parameters

**Decision must originate from**:
- Trusted governance/policy boundary (OPA)
- Authorized human approval mechanism (separate authenticated path)

---

## 4. HUMAN APPROVAL IS DISTINCT FROM APPROVAL DECISION

**Anti-pattern**: Conflating decision mode with approval artifact source

```python
# WRONG: Equating modes with sources
AUTO_APPROVE ≡ "approved_by": "system"       # NO
HUMAN_APPROVAL_REQUIRED ≡ "approved_by": "agent"  # NO
```

**Correct model**:

```python
ApprovalDecision:
    mode = HUMAN_APPROVAL_REQUIRED
    # ↓
    What authorization path is required?
    
ApprovalArtifact:
    approval_source = HUMAN
    approving_principal = "human-123"
    # ↓
    What specific authorization was actually granted?
```

**Relationship**:

| Decision Mode | Artifact Creation | Approving Principal |
|---|---|---|
| AUTO_APPROVE | Automatic | runtime:governance |
| HUMAN_APPROVAL_REQUIRED | Manual (human action) | authenticated human |
| DENY | Never | (N/A) |

---

## 5. DECISION CONTRACT SPECIFICATION

### Immutable Canonical Type

**Location**: `src/monkey_brain/kernel/approval.py` (already exists as `ApprovalArtifact`; extend with `ApprovalDecision`)

**Define**:

```python
@dataclass(frozen=True)
class ApprovalDecision:
    """Immutable approval decision from trusted policy evaluation.

    This is NOT an approval artifact (human/auto grant).
    This is the DECISION about what kind of approval is required.
    """

    # Decision identity
    decision_id: str  # UUID, unique for this evaluation
    request_id: str  # Correlation to the originating request
    operation_id: str  # Links to SecurityOperation ledger

    # The decision
    mode: ApprovalMode  # AUTO_APPROVE | HUMAN_APPROVAL_REQUIRED | DENY

    # Authenticated principal requesting
    requesting_principal: str  # From TrustedAuthEvidence.principal_id
    principal_type: str  # human | service (from TrustedAuthEvidence)

    # What was evaluated
    operation: str  # Action name (e.g., "capability.execute")
    resource: str  # Resource being operated on
    operation_class: str  # SECURITY_CRITICAL, PROPOSAL_ONLY, READ_ONLY

    # Policy provenance
    policy_rule: str  # Which OPA rule applied
    policy_revision: str  # OPA policy version
    policy_decision_full: dict[str, Any]  # Full OPA output for audit

    # Risk classification
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL

    # Binding constraints
    scope: dict[str, Any]  # Capabilities, resources, constraints approved
    expires_at: float  # Decision expiration (TTL based on risk_level)

    # Immutability
    created_at: float
    decision_hash: str  # HMAC for integrity (if needed)

    # Audit trail
    audit_entry_id: str  # Reference to audit log entry
```

### Immutable Properties

- Once created, decision cannot be modified
- Creating a new policy evaluation produces a new decision
- Decisions are never "downgraded" (AUTO_APPROVE → DENY requires new evaluation)

---

## 6. TRUST BOUNDARY — FIELD PROVENANCE

Every field must have defined source and trust status:

| Field | Source | Trusted? | Agent-Controlled? | Governance-Controlled? |
|---|---|---|---|---|
| requesting_principal | TrustedAuthEvidence.principal_id | ✅ YES | ❌ NO | ✅ YES (extracted from JWT) |
| operation | Request metadata | ⚠️ VALIDATED | ❌ NO | ✅ YES (classified, not accepted as-declared) |
| resource | Request metadata | ⚠️ VALIDATED | ❌ NO | ✅ YES (scope validation) |
| scope | OPA output | ✅ YES | ❌ NO | ✅ YES (policy-determined) |
| mode | OPA output | ✅ YES | ❌ NO | ✅ YES (policy-determined) |
| policy_rule | OPA output | ✅ YES | ❌ NO | ✅ YES (OPA-generated) |
| risk_level | OPA output | ✅ YES | ❌ NO | ✅ YES (policy-determined) |
| expires_at | TTL calculation | ✅ YES | ❌ NO | ✅ YES (based on risk_level) |

---

## 7. POLICY PROVENANCE

An automatic approval must be traceable to policy evaluation that produced it.

**Minimum preservation**:
- `policy_rule`: Which OPA rule matched
- `policy_revision`: OPA policy version/hash
- `policy_decision_full`: Full OPA output (for audit/debugging)
- Existing OPA integration in `GovernanceEngine.evaluate()`

**Current OPA Integration**:
- `GovernanceEngine.evaluate()` calls `services.common.opa.evaluate_full("agentos/governance", ...)`
- Returns `{"allowed": bool, "reason": str, "violations": list, ...}` (plus approval fields from Phase 2)
- Location: `opa/policies/agentos_governance.rego` (not inspected here; assumed to exist)

**Limitation**: If OPA policy version is not provided by OPA, document it rather than fabricating it.

---

## 8. DECISION IMMUTABILITY

Once created, `ApprovalDecision` must not be mutable:

```python
# WRONG: Transforming an existing decision
decision.mode = ApprovalMode.AUTO_APPROVE  # ❌ frozen dataclass → AttributeError
```

**Correct approach**:
- New policy evaluation → new decision
- No downstream transformation
- Decisions are value objects

---

## 9. DECISION VS APPROVAL ARTIFACT

Keep these concepts separate:

| Concept | Answers | Timing | Creator |
|---|---|---|---|
| ApprovalDecision | What approval path is required? | At authorization time (policy evaluation) | OPA policy engine |
| ApprovalArtifact | What specific authorization was granted? | At approval time (AUTO_APPROVE auto-granted, HUMAN approval manual) | Governance engine OR human approver |

**Implications**:

```python
if decision.mode == ApprovalMode.AUTO_APPROVE:
    # Create ApprovalArtifact automatically
    artifact = ApprovalArtifact(
        approval_mode=ApprovalMode.AUTO_APPROVE,
        approval_source=ApprovalSource.POLICY_AUTOMATIC,
        approving_principal="runtime:governance",
        ...
    )
    # ↓ Artifact is valid immediately

if decision.mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED:
    # DO NOT create human approval artifact
    # Create approval REQUEST/HANDOFF only
    # Wait for human to explicitly approve
    # Only then create ApprovalArtifact(
    #     approval_source=ApprovalSource.HUMAN,
    #     approving_principal="human-123"
    # )
    # ↓ Artifact is NOT valid until human approves
```

---

## 10. HUMAN HANDOFF

For `HUMAN_APPROVAL_REQUIRED`, execution is blocked until valid human approval exists.

**Execution state machine**:

```
ApprovalDecision(mode=HUMAN_APPROVAL_REQUIRED)
    ↓
execution_permitted = false
    ↓
[Human reviews and approves]
    ↓
ApprovalArtifact(
    approval_source=HUMAN,
    approving_principal="human-123",
    approved_at=<timestamp>
)
    ↓
execution_permitted = true (subject to other gates)
```

**Design property**: The decision itself is NOT the human approval. The decision is a HANDOFF REQUEST.

---

## 11. AUTOMATIC APPROVAL

For `AUTO_APPROVE`, the decision authorizes automatic approval only when ALL trusted prerequisites succeeded:

```python
auto_approve_permitted = (
    authenticated_principal is not None           # ✅ AUTH passed
    AND authorization_succeeded                   # ✅ AUTHZ passed
    AND policy_allows_automatic_approval          # ✅ OPA decision mode
    AND scope_is_valid                            # ✅ Operation classification
    AND required_security_controls_pass           # ✅ MFA, idempotency, etc.
)
```

**Automatic approval does NOT mean**:
- Agent requested it → automatically approve
- Message says "approved" → automatically approve

**Automatic approval DOES mean**:
- Trusted policy evaluation → automatic approval permitted
- Governance system creates artifact without human review
- Artifact is valid immediately

---

## 12. DENY IS TERMINAL

`DENY` must be terminal for that policy evaluation:

```python
DENY ≠≠≠> AUTO_APPROVE  # No downstream transformation
DENY ≠≠≠> HUMAN_APPROVAL_REQUIRED  # Cannot escalate to human
```

**Correction requires**:
- New authenticated request
- New policy evaluation
- New decision

---

## 13. FAILURE SEMANTICS (FAIL-CLOSED)

| Failure | Default | Rationale |
|---|---|---|
| authentication failure | DENY | Unknown principal |
| authorization failure | DENY | OPA denies |
| OPA unavailable | DENY | Unknown policy |
| OPA error | DENY | Policy evaluation undefined |
| policy ambiguity | DENY | Unknown intent |
| invalid request | DENY | Cannot evaluate |
| invalid scope | DENY | Operation out of bounds |
| expired approval | DENY | Approval TTL exceeded |
| revoked approval | DENY | Explicitly blocked |
| audit failure | DENY | Cannot record (execution_permitted = false) |

**Critical**: Policy decision validity ≠ execution permission

```python
decision = ApprovalDecision(mode=AUTO_APPROVE)  # Valid diagnostically
audit_failed = true
# ↓
execution_permitted = false  # Audit failure can only restrict, never expand
```

---

## 14. IMPORTANT: DECISION ≠ EXECUTION PERMISSION

**Anti-pattern**:

```python
# WRONG: Treating decision as execution gate
if decision.mode == AUTO_APPROVE:
    execute()  # ❌ Skips other security controls
```

**Correct design**:

```
ApprovalDecision
        ↓
approval requirement satisfied (decision.mode logic)
        ↓
execution gate
        ├── authentication check
        ├── authorization check
        ├── scope validation
        ├── state validation
        ├── audit durability check
        ├── idempotency check
        └── other runtime security controls
        ↓
if all_pass:
    EXECUTE
```

**Decision is ONE component of execution permission, not the whole gate.**

---

## 15. DECISION MONOTONICITY

### Property 1: DENY is never weakened

```python
decision.mode = DENY
# ↓
no_downstream_logic_can_weaken_this
# ↓
execution_permitted = false  # Always
```

### Property 2: HUMAN_APPROVAL_REQUIRED blocks until approved

```python
decision.mode = HUMAN_APPROVAL_REQUIRED
# ↓
agent_claims_approval = "I have approval"
# ↓
if not valid_human_approval_artifact_exists:
    execution_permitted = false  # Agent claim is irrelevant
```

### Property 3: AUTO_APPROVE does not bypass other gates

```python
decision.mode = AUTO_APPROVE
# ↓
approval_requirement_satisfied = true
# ↓
but_other_gates_still_apply:
    authentication_required = true
    scope_validation_required = true
    audit_durability_required = true
```

---

## 16. SCOPE BINDING

Decision must be bound to operation scope:

```python
# WRONG: Scope from AUTO_APPROVE approval reused for different operation
approved_for: capability_A, resource_1
request_for: capability_B, resource_2
# ↓
reuse_approved_artifact: NO  # Scope mismatch

# CORRECT: New request requires new decision
new_request
    ↓
new_policy_evaluation
    ↓
new_ApprovalDecision
```

**Scope validation prevents**:
- Approved for "read document 123" → using for "delete document 456"
- Approved for "list_users" → using for "update_system_policy"

---

## 17. PRINCIPAL BINDING

Decision bound to authenticated principal:

```python
# WRONG: Reusing approval across different principals
decision.requesting_principal = "agent-1"
execute_as_principal = "agent-2"  # ❌ Scope breach

# CORRECT: Same principal or explicit delegation (if model supports it)
if existing_delegation(agent_1 → agent_2):
    use_delegation_canonical_form  # Use existing trusted delegation
else:
    new_request_from_agent_2 → new_decision
```

---

## 18. REQUEST BINDING

Decision associated with stable request/correlation identifier:

```python
decision.request_id = "req_abc123"
decision.operation_id = "op_def456"  # From SecurityOperation ledger

# Prevents:
decision_for_req_abc123 → used_for_req_xyz789  # ❌ Different request
```

---

## 19. EXPIRATION

Decision itself does not have a TTL (it's a historical record). But the approval it grants does:

```python
# Decision: Historical record
decision.created_at = 1000
decision.expires_at = 0.0  # Decisions don't expire

# Artifact (if AUTO_APPROVE): Has TTL based on risk
artifact.expires_at = 1000 + (24 * 3600)  # For AUTO_APPROVE, LOW risk
artifact.is_valid() at time 2000 = false  # Expired
```

---

## 20. REPLAY PROTECTION

Decision/approval not reusable for different request:

```python
# Test case
decision_for_req_A
    ↓
attempt_to_use_for_req_B
    ↓
validation_fails: "request ID mismatch"
```

---

## 21. SERIALIZATION

If decision crosses agent/process boundary:

```python
# Canonical JSON representation
{
    "decision_id": "dec_...",
    "mode": "AUTO_APPROVE",
    "requesting_principal": "agent-1",
    "operation": "capability.execute",
    "policy_rule": "rule_name",
    "created_at": 1234567890.0,
}

# Integrity: If signed/MAC'd, use existing envelope mechanism
# Don't invent cryptography solely for appearance
```

---

## 22. RUNTIME CONSUMER CONTRACT

Execution gate is allowed to consume:

```python
✅ Authenticated principal (TrustedAuthEvidence)
✅ ApprovalDecision
✅ ApprovalArtifact (where HUMAN_APPROVAL_REQUIRED)
✅ Current policy/security state
✅ Audit status

❌ LLM text
❌ Agent assertion
❌ Chat message saying "approved"
❌ Arbitrary metadata
```

---

## 23. EVENT/AUDIT CONTRACT

Auditable transitions:

```python
EVENTS = [
    "approval_decision_created",  # Policy evaluated
    "approval_decision_denied",  # Mode = DENY
    "approval_decision_auto_approved",  # Mode = AUTO_APPROVE
    "approval_decision_human_required",  # Mode = HUMAN_APPROVAL_REQUIRED
    "approval_artifact_auto_created",  # AUTO_APPROVE → artifact
    "approval_artifact_human_requested",  # HITL handoff created
    "human_approval_granted",  # Human approved
    "human_approval_rejected",  # Human rejected
    "approval_expired",  # TTL exceeded
    "approval_revoked",  # Explicit revocation
    "execution_approved_and_executed",  # Happy path
    "execution_blocked_approval_required",  # Awaiting human
    "execution_blocked_denied",  # DENY
    "execution_blocked_expired",  # Approval expired
]
```

---

## 24. DECISION MATRIX

| Auth | Policy | Human Approval | Expected Decision | Executable | Notes |
|---|---|---|---|---|---|
| valid | AUTO_APPROVE rule | none | AUTO_APPROVE | ✅ yes (if other gates pass) | Policy permits automatic |
| valid | HITL rule | absent | HUMAN_APPROVAL_REQUIRED | ❌ no | Awaiting human |
| valid | HITL rule | valid | decision still HUMAN_APPROVAL_REQUIRED | ✅ yes (if artifact valid + other gates) | Human artifact present |
| valid | DENY rule | none | DENY | ❌ no | Terminal |
| invalid | any | none | DENY | ❌ no | AUTH failed |
| N/A | N/A | none | DENY | ❌ no | OPA unavailable |
| valid | policy error | none | DENY | ❌ no | Policy evaluation failed |
| valid | AUTO_APPROVE rule | fake agent claim | AUTO_APPROVE only if policy independently permits | ❌ no (agent claim irrelevant) | Agent claim doesn't elevate |
| valid | HITL rule | agent claims | HUMAN_APPROVAL_REQUIRED | ❌ no | Only valid human artifact matters |
| valid | AUTO_APPROVE rule | audit unavailable | AUTO_APPROVE diagnostically | ❌ no | Audit failure blocks execution |

---

## 25. SECURITY PROPERTY TESTS

Regression tests must prove:

```python
✅ agent cannot create AUTO_APPROVE  # Requires OPA
✅ agent cannot create HUMAN approval  # Requires human
✅ agent cannot convert DENY to AUTO_APPROVE  # Immutable
✅ agent cannot expand scope  # Scope from OPA only
✅ agent cannot substitute another principal  # From TrustedAuthEvidence
✅ agent cannot replay approval against another request  # Request binding
✅ audit failure cannot enable execution  # Audit blocks, never enables
✅ OPA failure cannot enable execution  # Fail-closed
✅ authentication failure cannot enable execution  # AUTH required
```

---

## 26. BACKWARD COMPATIBILITY

Before wiring runtime gate:

```python
✅ grep src/monkey_brain/kernel/approval.py  # ApprovalArtifact exists (Phase 2)
✅ grep src/monkey_brain/api/routes/approval.py  # Endpoints exist (Phase 2)
✅ grep GovernanceEngine.evaluate()  # Already returns approval_mode (Phase 2)
✅ No existing parallel approval systems  # Single approval path
❌ Do NOT wire until explicitly authorized  # This contract stage only
```

---

## 27. NOT YET IMPLEMENTED: RUNTIME WIRING

**This contract defines the interface.**

Unless separately authorized with:
```
RUNTIME_APPROVAL_GATE_WIRING_APPROVED
```

DO NOT wire new contract into:
- Agent execution paths
- Actor mutation gates
- Plan executor
- Agent-to-agent execution
- Production request handling

**Wiring phase includes**:
- Integrating `ApprovalDecision` creation in `GovernanceEngine.evaluate()`
- Adding approval validation gate in `_execute_attempt_pipeline()`
- Human approval endpoint implementation
- HUMAN_APPROVAL_REQUIRED blocking logic
- Audit integration

---

## 28. FINAL CONTRACT INVARIANT

```
ApprovalDecision answers:
    "What approval path does trusted governance require
     for this authenticated operation?"

It does NOT answer:
    "May the executor bypass all other security controls?"

================================================================================

The three decisions are:

    AUTO_APPROVE
        Policy permits trusted automatic approval.

    HUMAN_APPROVAL_REQUIRED
        Policy requires explicit human approval.

    DENY
        Operation cannot proceed.

================================================================================

Execution formula:

    EXECUTION =
        authenticated
        AND authorized
        AND approval_requirement_satisfied
        AND scope_valid
        AND state_valid
        AND required_audit_durable
        AND other_runtime_security_controls

================================================================================
```

---

## 29. IMPLEMENTATION CHECKLIST (DO NOT IMPLEMENT YET)

When `RUNTIME_APPROVAL_GATE_WIRING_APPROVED` is granted:

- [ ] Define `ApprovalDecision` dataclass (immutable, frozen)
- [ ] Extend `GovernanceEngine.evaluate()` to create `ApprovalDecision`
- [ ] Add approval validation gate in `_execute_attempt_pipeline()` before MUTATION
- [ ] Block HUMAN_APPROVAL_REQUIRED operations at gate
- [ ] Block DENY operations at gate
- [ ] Allow AUTO_APPROVE to proceed (subject to other gates)
- [ ] Implement human approval endpoint (POST `/runtime-approvals/{decision_id}/approve`)
- [ ] Implement approval rejection endpoint (POST `/runtime-approvals/{decision_id}/reject`)
- [ ] Prevent self-approval in human approval endpoint
- [ ] Add audit events for all transitions
- [ ] Add expiration validation
- [ ] Add scope validation
- [ ] Add tests for all decision modes
- [ ] Add tests for all failure modes
- [ ] Verify no C2/C3/OPA/MFA redesign
- [ ] Verify no duplicate approval systems
- [ ] Verify backward compatibility

---

## 30. DISCOVERY ARTIFACTS

### Existing Canonical Types (Already in CognitiveOS)

| Type | Location | Purpose | Immutable? |
|---|---|---|---|
| `TrustedAuthEvidence` | `trusted_auth.py` | Authenticated principal | ✅ YES (frozen dataclass) |
| `SecurityOperation` | `security_operation.py` | Operation ledger entry | ❌ NO (mutable state machine) |
| `AuditEntry` | `audit.py` | Audit log record | ❌ (append-only, not modified) |
| `ApprovalArtifact` | `approval.py` (Phase 2) | Approval grant record | ✅ YES (frozen dataclass) |
| `GovernanceEngine.evaluate()` result | `governance.py` | OPA policy decision | ❌ (transient dict) |

### Extension Points (Where contract integrates)

| Location | Component | Role | Change Needed? |
|---|---|---|---|
| `GovernanceEngine.evaluate()` | OPA result enrichment | Add approval_mode, risk_level, policy_rule | ✅ YES (Phase 2 done) |
| `run_governed_mutation()` | Artifact creation | Create ApprovalArtifact from decision | ✅ YES (Phase 2 done) |
| `_execute_attempt_pipeline()` | Approval gate | Validate approval before MUTATION | ❌ NO (not wired yet) |
| `security_boundary.py` | Pipeline stages | Add APPROVAL_VALIDATION stage | ❌ NO (not wired yet) |
| `src/monkey_brain/api/routes/approval.py` | Human endpoints | Approve/reject operations | ✅ YES (Phase 2 done) |

### Known Gaps in Current Implementation

| Gap | Impact | Resolution |
|---|---|---|
| `ApprovalDecision` type not defined | Contract unclear | Define immutable dataclass (DO NOT wire yet) |
| Runtime approval gate not wired | No HUMAN_APPROVAL_REQUIRED blocking | Wait for RUNTIME_APPROVAL_GATE_WIRING_APPROVED |
| Explicit scope validation not in gate | Scope escalation possible | Add scope checking in approval validator |
| Human approval rejection not implemented | No denial path | Implement POST `.../reject` endpoint (Phase 2 done) |

---

## FINAL STATUS

✅ **Discovery**: Complete. All existing types identified.  
✅ **Contract Defined**: Immutable, canonical, fail-closed.  
✅ **Non-Negotiable Invariants**: Specified.  
❌ **NOT WIRED**: Awaiting `RUNTIME_APPROVAL_GATE_WIRING_APPROVED`.  
❌ **NOT IMPLEMENTED**: This is contract definition only.

---

**Next Phase**: Runtime wiring (requires separate authorization)
