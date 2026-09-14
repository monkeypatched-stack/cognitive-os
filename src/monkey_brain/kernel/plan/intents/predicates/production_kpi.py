"""production_kpi predicates — real MongoDB aggregation queries."""

import re


async def production_kpi_question_answer(client, question, force=False):
    """Generate production KPI answers from MongoDB data."""
    try:
        db = client["demo"]

        # OEE question
        if re.search(r"oee|overall equipment", question, re.IGNORECASE):
            machines = db["pharmaceutical_machines"]
            total = await machines.count_documents({})
            active = await machines.count_documents({"status": "Active"})
            answer = (
                f"OEE Summary:\n"
                f"  Total machines: {total}\n"
                f"  Active machines: {active}\n"
                f"  Availability: {active / total * 100:.1f}%"
                if total > 0
                else "No machine data."
            )
            return (answer, [], [], False)

        # MTBF / MTTR
        if re.search(r"mtbf|mttr|mean time", question, re.IGNORECASE):
            machines = db["pharmaceutical_machines"]
            total = await machines.count_documents({})
            answer = (
                f"Reliability Metrics:\n"
                f"  Total machines tracked: {total}\n"
                f"  MTBF/MTTR data requires maintenance log integration.\n"
                f"  Current machine count: {total}"
            )
            return (answer, [], [], False)

        # Yield / quality
        if re.search(r"yield|quality|rejection|scrap", question, re.IGNORECASE):
            batches = db["production_batches"]
            total = await batches.count_documents({})
            approved = await batches.count_documents({"status": "Approved"})
            rejected = await batches.count_documents({"status": "Rejected"})
            in_progress = await batches.count_documents({"status": "In Progress"})
            answer = (
                f"Production Quality:\n"
                f"  Total batches: {total}\n"
                f"  Approved: {approved} ({approved / total * 100:.1f}%)"
                + (
                    f"\n  Rejected: {rejected} ({rejected / total * 100:.1f}%)\n  In Progress: {in_progress}"
                    if total > 0
                    else ""
                )
            )
            return (answer, [], [], False)

        # Downtime
        if re.search(r"downtime|uptime|availability", question, re.IGNORECASE):
            machines = db["pharmaceutical_machines"]
            total = await machines.count_documents({})
            active = await machines.count_documents({"status": "Active"})
            inactive = total - active
            answer = (
                f"Equipment Availability:\n"
                f"  Total machines: {total}\n"
                f"  Active: {active}\n"
                f"  Inactive: {inactive}\n"
                f"  Availability: {active / total * 100:.1f}%"
                if total > 0
                else "No data."
            )
            return (answer, [], [], False)

        # Default: overall summary
        batches = db["production_batches"]
        machines = db["pharmaceutical_machines"]
        work_orders = db["work_orders"]
        batch_total = await batches.count_documents({})
        machine_total = await machines.count_documents({})
        wo_total = await work_orders.count_documents({})
        wo_open = await work_orders.count_documents({"status": {"$nin": ["Completed", "Cancelled"]}})
        approved = await batches.count_documents({"status": "Approved"})

        answer = (
            f"Production KPI Dashboard:\n"
            f"  Batches: {batch_total} total, {approved} approved\n"
            f"  Machines: {machine_total}\n"
            f"  Work Orders: {wo_total} total, {wo_open} open"
        )
        return (answer, [], [], False)

    except Exception as e:
        return (f"Error generating KPIs: {e}", [], [], False)


def is_production_kpi_question(question):
    q = question.lower()
    return any(
        kw in q
        for kw in (
            "kpi",
            "oee",
            "mtbf",
            "mttr",
            "throughput",
            "production metric",
            "production efficiency",
            "yield",
            "downtime",
            "uptime",
            "quality metric",
            "performance",
            "scrap rate",
            "rejection",
        )
    )
