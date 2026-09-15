# Runtime governance — evaluated by GovernanceEngine.evaluate()
# (src/monkey_brain/kernel/governance.py), called from the /plan, /execute,
# /predict, /simulate, /compare, /learn, and /query route handlers before
# each request runs.
#
# Replaces the previous in-memory, Python-only stub: a per-process dict of
# RuntimeCharter objects that only ever got populated by register_charter(),
# which had zero callers anywhere in production — so real governance was
# never actually configurable at all, only ever "not configured" (allow
# everything) or, in an earlier version, "deny everything" (a fail-closed
# control with no provisioning path, which is an outage, not security — see
# tests/security/test_governance_gate.py). This moves the actual decision
# into OPA's data layer, which IS provisionable at runtime (PUT
# /v1/data/agentos/governance/{denied_runtimes,denied_actions,charters})
# without a code deploy.
#
# Input schema (supplied by GovernanceEngine.evaluate):
#   input.runtime_id  — string  (the authenticated caller/runtime id)
#   input.action      — string  ("plan" | "execute" | "simulate" | "compare"
#                                 | "learn" | "query")
#   input.context     — object  (request-specific: question text, run_id, ...)
#
# Data (pushed via OPA's data API or a bundle; all optional — absent data
# means "nothing has been configured to deny," which is the correct default
# for a system with no provisioning path yet):
#   data.agentos.governance.denied_runtimes  — [runtime_id, ...] full block
#   data.agentos.governance.denied_actions   — [action, ...] global kill-switch
#   data.agentos.governance.charters[runtime_id] — {
#       constitution: ["deny <action>", ...],
#       denied_actions: [action, ...],
#   } per-runtime rules, the direct successor of the old RuntimeCharter shape
#
# GovernanceEngine calls this with default_allow=True when OPA is
# unreachable/unconfigured (OPA_URL unset) — the client-side safety net that
# keeps this inert-by-default, same as every other OPA integration in this
# codebase (services.common.opa.evaluate_full's own default_allow param,
# require_opa's "falls back to allow when OPA is not configured").
# The policy itself still defaults to deny internally and only allows
# through explicit rules, so a MISCONFIGURED-but-reachable OPA fails closed
# rather than silently open.

package agentos.governance

import future.keywords.if
import future.keywords.in

default allow = false

# Global runtime block — a runtime_id anyone has explicitly blocked.
runtime_blocked if {
	input.runtime_id in data.agentos.governance.denied_runtimes
}

# Global action kill-switch — an action ops has explicitly disabled for
# everyone (e.g. "learn" during an incident, without touching the code).
action_blocked if {
	input.action in data.agentos.governance.denied_actions
}

# Per-runtime charter: a constitution rule naming this action as denied, or
# an explicit denied_actions list on the charter — the direct successor of
# GovernanceEngine's own old _check_rule/_check_policy logic, now data-driven.
charter := data.agentos.governance.charters[input.runtime_id]

charter_denies if {
	some rule in charter.constitution
	contains(lower(rule), "deny")
	contains(lower(rule), lower(input.action))
}

charter_denies if {
	input.action in charter.denied_actions
}

# SPIFFE/SPIRE workload identity layer: recipient binding (Phase 12) --
# the authenticated sender's own SPIFFE ID (input.context.auth.spiffe_id,
# populated only from a verified WorkloadIdentity -- kernel/trusted_auth.py::
# evidence_from_spiffe -- never from agent-supplied message content) may
# be restricted to a specific, data-driven set of recipients it's allowed
# to address. Absent data (the default, same as denied_runtimes/
# denied_actions above) means every sender may address any recipient --
# byte-for-byte today's behavior. Only evaluated when the caller actually
# supplies recipient_spiffe_id (kernel/security_boundary.py::
# build_opa_input's optional keyword) -- a non-communication operation
# with no recipient concept is never affected.
#
# Data:
#   data.agentos.governance.allowed_recipients[sender_spiffe_id] —
#       [recipient_spiffe_id, ...] the ONLY recipients that sender may address

recipient_mismatch if {
	input.context.recipient_spiffe_id != ""
	sender := input.context.auth.spiffe_id
	sender != ""
	allowed := data.agentos.governance.allowed_recipients[sender]
	not input.context.recipient_spiffe_id in allowed
}

# ─── Portable Delegation ────────────────────────────────────────────────
# input.context.delegation is populated ONLY by build_opa_input's
# verified_delegation parameter (kernel/security_boundary.py) -- itself
# populated ONLY from a chain that already passed kernel/delegation.py::
# verify_delegation_chain (proof, attenuation, expiry, revocation, audience
# all already checked before OPA ever sees this). OPA's own job here is
# narrower and different: given a delegation that is ALREADY known-valid,
# does the SPECIFIC capability/action being requested right now actually
# fall within what that delegation named? A valid-but-irrelevant
# delegation (e.g. valid for "grocery.purchase" presented for a
# "bank.transfer" request) must still deny.
#
# Absent input.context.delegation means "this request is not claiming
# delegated authority" -- every existing caller today -- and none of the
# rules below ever fire, so behavior is unchanged from before this rule
# existed.

delegation_present if {
	input.context.delegation
	input.context.delegation.delegation_id != ""
}

delegation_capability_mismatch if {
	delegation_present
	requested := object.get(input.context, "capability", "")
	requested != ""
	not requested in input.context.delegation.capabilities
}

# ─── Bias Audit ─────────────────────────────────────────────────────────
# input.context.signals.bias_detected is computed in Python, not here --
# OPA has no statistical capability, it only branches on the pre-computed
# disparate-impact verdict kernel/bias_audit.py::BiasAuditor.evaluate()
# already produced (see kernel/governance.py::evaluate's bias_group
# handling). Absent input.context.signals (every caller today, since
# config.BIAS_AUDITED_ACTIONS is empty by default) means this never fires
# -- same "additive, opt-in" shape as delegation above.

bias_detected if {
	input.context.signals.bias_detected == true
}

allow if {
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	not delegation_capability_mismatch
	not bias_detected
	not crash_test_unsafe
}

# Structured deny reason for audit logs / the API's {"reason": ...} field.
deny_reason := "runtime_blocked" if {
	not allow
	runtime_blocked
}

deny_reason := "action_blocked" if {
	not allow
	not runtime_blocked
	action_blocked
}

deny_reason := sprintf("charter denies action %q", [input.action]) if {
	not allow
	not runtime_blocked
	not action_blocked
	charter_denies
}

deny_reason := sprintf("sender %q is not permitted to address recipient %q", [
	input.context.auth.spiffe_id, input.context.recipient_spiffe_id,
]) if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	recipient_mismatch
}

deny_reason := sprintf("delegation %q does not grant capability %q", [
	input.context.delegation.delegation_id, object.get(input.context, "capability", ""),
]) if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	delegation_capability_mismatch
}

deny_reason := sprintf("bias audit flagged action %q for protected attribute %q (disparity ratio %v)", [
	input.action, input.context.signals.bias_protected_attribute, input.context.signals.bias_disparity_ratio,
]) if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	not delegation_capability_mismatch
	bias_detected
}

deny_reason := "crash_test_unsafe" if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	not delegation_capability_mismatch
	not bias_detected
	crash_test_unsafe
}

# ─── Simulator-Only Crash Test ──────────────────────────────────────────
# kernel/edge/ros_integration.py::run_ros_action_if_governed computes
# input.context.signals.{simulation_only,crash_test_mode,is_simulation}
# itself, from os.environ / the actor-bound adapter's own is_simulation
# attribute (kernel-trusted evidence, never taken from the calling
# capability's own claim -- same "agent extra cannot set auth" posture
# build_opa_input's docstring states, same input.context.signals.* subkey
# the Bias Audit block above already uses). This is real, evaluated
# governance -- action_class == "capability.CrashTest" is denied unless
# ALL THREE signals are present and true, independent of anything else in
# this file (runtime_blocked/charter_denies/etc. can still deny it too;
# this only ever ADDS a reason to deny, never removes one).
crash_test_unsafe if {
	input.action == "capability.CrashTest"
	not input.context.signals.simulation_only == true
}

crash_test_unsafe if {
	input.action == "capability.CrashTest"
	not input.context.signals.crash_test_mode == true
}

crash_test_unsafe if {
	input.action == "capability.CrashTest"
	not input.context.signals.is_simulation == true
}

# ─── Runtime Approval Gate ────────────────────────────────────────────────
# Exposes the AUTO_APPROVE / HUMAN_APPROVAL_REQUIRED / DENY distinction
# GovernanceEngine.evaluate() (kernel/governance.py) already threads through
# to ApprovalArtifact creation. Same data-driven shape as denied_runtimes/
# denied_actions/charters above -- provisionable via
# PUT /v1/data/agentos/governance/{high_risk_actions,charters/<id>/high_risk_actions}
# with no code deploy. Absent data means "nothing configured as high-risk,"
# which preserves today's exact behavior: every allowed action resolves to
# AUTO_APPROVE, every denied action resolves to DENY. This does not change
# what allow/runtime_blocked/action_blocked/charter_denies decide -- it only
# names, for an already-allowed action, whether it may proceed automatically
# or must escalate to a human.
#
# Data:
#   data.agentos.governance.high_risk_actions       — [action, ...] global
#   data.agentos.governance.charters[id].high_risk_actions — per-runtime

high_risk_action if {
	input.action in data.agentos.governance.high_risk_actions
}

high_risk_action if {
	input.action in charter.high_risk_actions
}

approval_mode := "DENY" if {
	not allow
}

approval_mode := "HUMAN_APPROVAL_REQUIRED" if {
	allow
	high_risk_action
}

approval_mode := "AUTO_APPROVE" if {
	allow
	not high_risk_action
}

risk_level := "HIGH" if {
	not allow
}

risk_level := "MEDIUM" if {
	allow
	high_risk_action
}

risk_level := "LOW" if {
	allow
	not high_risk_action
}

# Which named rule produced the decision above -- approval provenance
# (Section 7: "under what policy") without inventing a second reason field.
policy_rule := "runtime_blocked" if {
	not allow
	runtime_blocked
}

policy_rule := "action_blocked" if {
	not allow
	not runtime_blocked
	action_blocked
}

policy_rule := "charter_denies" if {
	not allow
	not runtime_blocked
	not action_blocked
	charter_denies
}

policy_rule := "recipient_mismatch" if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	recipient_mismatch
}

policy_rule := "delegation_capability_mismatch" if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	delegation_capability_mismatch
}

policy_rule := "bias_detected" if {
	not allow
	not runtime_blocked
	not action_blocked
	not charter_denies
	not recipient_mismatch
	not delegation_capability_mismatch
	bias_detected
}

policy_rule := "high_risk_action" if {
	allow
	high_risk_action
}

policy_rule := "default_allow" if {
	allow
	not high_risk_action
}

requires_hitl if {
	approval_mode == "HUMAN_APPROVAL_REQUIRED"
}
