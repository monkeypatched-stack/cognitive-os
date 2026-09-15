import { useEffect, useState } from 'react'
import { PanelContainer } from './PanelContainer'
import { fetchPendingRuntimeApprovals, approveRuntimeApproval, rejectRuntimeApproval, type PendingRuntimeApproval } from '../api/approvalClient'
import { useRefreshStore } from '../store/refreshStore'
import './ApprovalsPanel.css'

/**
 * The real HITL gate (src/monkey_brain/api/routes/approval.py's Runtime
 * Approval Gate) had no UI at all before this — every approval decision
 * had to be made by calling the API directly. Lists every operation
 * currently blocked on a human decision, actionable here.
 */
export function ApprovalsPanel() {
  const [approvals, setApprovals] = useState<PendingRuntimeApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const refreshSeq = useRefreshStore((s) => s.refreshSeq)

  const load = () => {
    let cancelled = false
    setLoading(true)
    setError('')
    fetchPendingRuntimeApprovals()
      .then((result) => { if (!cancelled) setApprovals(result.approvals) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }

  useEffect(load, [refreshSeq])

  const decide = async (approvalId: string, action: 'approve' | 'reject') => {
    const reason = window.prompt(action === 'approve' ? 'Reason for approval (optional):' : 'Reason for rejection:') ?? ''
    if (action === 'reject' && !reason) return
    setBusyId(approvalId)
    try {
      if (action === 'approve') await approveRuntimeApproval(approvalId, reason)
      else await rejectRuntimeApproval(approvalId, reason)
      setApprovals((current) => current.filter((a) => a.approval_id !== approvalId))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  return <PanelContainer title="Approvals">
    <div className="lwe-approvals-panel">
      {loading && <div className="lwe-approvals-empty">Loading pending approvals…</div>}
      {!loading && error && <div className="lwe-approvals-empty">{error}</div>}
      {!loading && !error && approvals.length === 0 && <div className="lwe-approvals-empty">No operations awaiting approval.</div>}
      {!loading && !error && approvals.length > 0 && <table className="lwe-approvals-table">
        <thead><tr><th>Operation</th><th>Target</th><th>Requested by</th><th>Risk</th><th>Policy rule</th><th /></tr></thead>
        <tbody>
          {approvals.map((a) => <tr key={a.approval_id}>
            <td>{a.target_operation}</td>
            <td>{a.target_resource}</td>
            <td>{a.requesting_principal}</td>
            <td><span className={`lwe-approvals-risk lwe-approvals-risk-${a.risk_level}`}>{a.risk_level}</span></td>
            <td>{a.policy_rule}</td>
            <td className="lwe-approvals-actions">
              <button type="button" disabled={busyId === a.approval_id} onClick={() => decide(a.approval_id, 'approve')}>Approve</button>
              <button type="button" disabled={busyId === a.approval_id} onClick={() => decide(a.approval_id, 'reject')}>Reject</button>
            </td>
          </tr>)}
        </tbody>
      </table>}
    </div>
  </PanelContainer>
}
