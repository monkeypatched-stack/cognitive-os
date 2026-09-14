"""decision_intelligence predicates — real MongoDB aggregation."""


async def decision_intelligence_question_answer(client, question, force=False):
    """Generate decision support data from MongoDB."""
    try:
        db = client["demo"]
        batches = db["production_batches"]
        work_orders = db["work_orders"]
        approvals = db["approvals"]
        change_ctrls = db["change_controls"]

        batch_total = await batches.count_documents({})
        batch_approved = await batches.count_documents({"status": "Approved"})
        batch_hold = await batches.count_documents({"status": "On Hold"})
        wo_total = await work_orders.count_documents({})
        wo_open = await work_orders.count_documents({"status": {"$nin": ["Completed", "Cancelled"]}})
        wo_high = await work_orders.count_documents({"priority": "High"})
        apr_pending = await approvals.count_documents({"status": "Pending"})
        cc_open = await change_ctrls.count_documents({"status": "Open"})

        lines = [
            "Decision Support Summary:",
            f"  Batches: {batch_total} total, {batch_approved} approved, {batch_hold} on hold",
            f"  Work Orders: {wo_total} total, {wo_open} open, {wo_high} high priority",
            f"  Approvals: {apr_pending} pending",
            f"  Change Controls: {cc_open} open",
        ]

        if wo_high > 0:
            lines.append(f"\n  ⚠ {wo_high} high-priority work orders need attention")
        if batch_hold > 0:
            lines.append(f"  ⚠ {batch_hold} batches are on hold")
        if apr_pending > 0:
            lines.append(f"  ⚠ {apr_pending} approvals awaiting decision")

        return ("\n".join(lines), [], [], False)

    except Exception as e:
        return (f"Error generating decision data: {e}", [], [], False)


def is_decision_intelligence_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "decide",
            "decision",
            "prioritize",
            "focus",
            "what should",
            "critical",
            "urgent",
            "attention",
            "recommend",
            "action",
        )
    )
