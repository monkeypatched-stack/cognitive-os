import { apiClient } from './client'

// Wraps the Runtime Approval Gate (src/monkey_brain/api/routes/approval.py,
// "RUNTIME APPROVAL GATE (NEW)" section) — distinct from securityClient.ts's
// PendingApproval, which is a different, execution-scoped concept
// (executions/{id}/pending-approval). This one is operation-scoped and is
// the real HITL gate GovernanceEngine/OPA route decisions through.

export interface PendingRuntimeApproval {
  approval_id: string
  operation_id: string
  approval_status: string
  risk_level: string
  requesting_principal: string
  target_operation: string
  target_resource: string
  policy_rule: string
  created_at: number
  expires_at: number
}

export function fetchPendingRuntimeApprovals(): Promise<{ approvals: PendingRuntimeApproval[]; total: number }> {
  return apiClient.request('/runtime-approvals')
}

export interface RuntimeApprovalArtifact {
  approval_id: string
  operation_id: string
  approval_mode: string
  approval_source: string
  approval_status: string
  requesting_principal: string
  approving_principal: string
  target_operation: string
  target_resource: string
  operation_class: string
  risk_level: string
  policy_rule: string
  approved_at: number
  expires_at: number
  revoked_at: number | null
  revocation_reason: string
}

export function fetchRuntimeApproval(approvalId: string): Promise<RuntimeApprovalArtifact> {
  return apiClient.request(`/runtime-approvals/${approvalId}`)
}

export function approveRuntimeApproval(approvalId: string, reason: string): Promise<{ approval_id: string; status: string; approving_principal: string; operation_id: string }> {
  return apiClient.request(`/runtime-approvals/${approvalId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, scope: {} }),
  })
}

export function rejectRuntimeApproval(approvalId: string, reason: string): Promise<{ approval_id: string; status: string; rejecting_principal: string; operation_id: string }> {
  return apiClient.request(`/runtime-approvals/${approvalId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
}
