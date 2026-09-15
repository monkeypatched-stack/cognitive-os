"use client";

// Same UX shape as living-world-explorer/src/components/ApprovalsPanel.tsx
// (built earlier in this same plan) — a first-class page here instead of
// an inspector panel, same backend endpoints.
import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import {
  approveRuntimeApproval,
  fetchPendingRuntimeApprovals,
  rejectRuntimeApproval,
  type PendingRuntimeApproval,
} from "../../lib/approvalClient";

function ApprovalsContent() {
  const [approvals, setApprovals] = useState<PendingRuntimeApproval[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchPendingRuntimeApprovals()
      .then((result) => {
        if (!cancelled) setApprovals(result.approvals);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  };

  useEffect(load, []);

  const decide = async (approvalId: string, action: "approve" | "reject") => {
    const reason = window.prompt(action === "approve" ? "Reason for approval (optional):" : "Reason for rejection:") ?? "";
    if (action === "reject" && !reason) return;
    setBusyId(approvalId);
    try {
      if (action === "approve") await approveRuntimeApproval(approvalId, reason);
      else await rejectRuntimeApproval(approvalId, reason);
      setApprovals((current) => current.filter((a) => a.approval_id !== approvalId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="page">
      <h1>Approvals</h1>
      <p>Operations currently awaiting a human decision.</p>
      {loading && <div className="empty-state">Loading pending approvals…</div>}
      {!loading && error && <div className="empty-state">{error}</div>}
      {!loading && !error && approvals.length === 0 && (
        <div className="empty-state">No operations awaiting approval.</div>
      )}
      {!loading && !error && approvals.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Operation</th>
              <th>Target</th>
              <th>Requested by</th>
              <th>Risk</th>
              <th>Policy rule</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {approvals.map((a) => (
              <tr key={a.approval_id}>
                <td>{a.target_operation}</td>
                <td>{a.target_resource}</td>
                <td>{a.requesting_principal}</td>
                <td>{a.risk_level}</td>
                <td>{a.policy_rule}</td>
                <td className="actions">
                  <button type="button" disabled={busyId === a.approval_id} onClick={() => decide(a.approval_id, "approve")}>
                    Approve
                  </button>
                  <button type="button" disabled={busyId === a.approval_id} onClick={() => decide(a.approval_id, "reject")}>
                    Reject
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function ApprovalsPage() {
  return (
    <RequireAuth>
      <ApprovalsContent />
    </RequireAuth>
  );
}
