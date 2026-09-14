from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from services.auth.helpers import nats_store
from services.common.compliance import (
    CHANGE_CONTROL_COLLECTION,
    RECORD_ID_KEYS,
    append_audit_entry,
    utc_now,
)
from services.common.config import settings

PROPOSED_CHANGE_COLLECTION = "gxp_proposed_changes"
TERMINAL_APPROVAL_STATUSES = {"Approved", "Rejected"}


class ApprovalAlreadyDecidedError(ValueError):
    pass


def record_lookup_query(record_id: str) -> dict:
    query = {"$or": [{key: record_id} for key in RECORD_ID_KEYS]}
    try:
        if ObjectId.is_valid(record_id):
            query["$or"].append({"_id": ObjectId(record_id)})
    except Exception:
        pass
    return query


async def _approval_records_for_source(db: Any, source_id: str | None) -> list[dict]:
    if not source_id:
        return []
    return (
        await db["approvals"]
        .find({"source_id": source_id}, {"_id": 0})
        .to_list(length=200)
    )


async def promote_proposed_changes(
    db: Any, change_control_id: str, decided_at: datetime
) -> list[dict]:
    proposed_nodes = (
        await db[PROPOSED_CHANGE_COLLECTION]
        .find(
            {
                "change_control_id": change_control_id,
                "status": {"$in": ["Pending Approval", "Approved"]},
                "archived": {"$ne": True},
            },
            {"_id": 0},
        )
        .to_list(length=200)
    )
    promoted: list[dict] = []
    for node in proposed_nodes:
        collection = node.get("source_collection")
        source_id = node.get("source_id")
        pending_properties = (
            node.get("pending_properties")
            if isinstance(node.get("pending_properties"), dict)
            else {}
        )
        if not collection or not source_id:
            continue
        set_values = {
            **pending_properties,
            "updated_at": decided_at,
            "change_control_effective_at": decided_at,
            "last_approved_change_control_id": change_control_id,
        }
        await db[str(collection)].update_one(
            record_lookup_query(str(source_id)), {"$set": set_values}
        )
        await db[PROPOSED_CHANGE_COLLECTION].update_one(
            {"proposed_change_id": node.get("proposed_change_id")},
            {
                "$set": {
                    "status": "Archived",
                    "lifecycle_state": "archived",
                    "archived": True,
                    "archived_at": utc_now(),
                    "effective_at": decided_at,
                    "live_node_touched": True,
                    "promotion_result": {
                        "collection": collection,
                        "source_id": source_id,
                        "applied_values": pending_properties,
                    },
                    "updated_at": utc_now(),
                }
            },
        )
        promoted.append(
            {
                "proposed_change_id": node.get("proposed_change_id"),
                "collection": collection,
                "source_id": source_id,
                "applied_values": pending_properties,
                "effective_at": decided_at,
            }
        )
    return promoted


async def rollback_proposed_changes(
    db: Any, change_control_id: str, decided_at: datetime, reason: str | None
) -> dict:
    proposed_nodes = (
        await db[PROPOSED_CHANGE_COLLECTION]
        .find({"change_control_id": change_control_id}, {"_id": 0})
        .to_list(length=200)
    )
    await db[PROPOSED_CHANGE_COLLECTION].delete_many(
        {"change_control_id": change_control_id}
    )
    return {
        "rolled_back": len(proposed_nodes),
        "deleted": len(proposed_nodes),
        "rolled_back_at": decided_at,
        "reason": reason,
        "live_node_touched": False,
    }


async def _apply_approval_decision_to_source(
    db: Any, approval: dict, decision: str, reason: str | None, decided_at: datetime
) -> None:
    source_collection = approval.get("source_collection")
    source_id = approval.get("source_id")
    if not source_collection or not source_id:
        return
    record = await db[source_collection].find_one(
        record_lookup_query(str(source_id)), {"_id": 0}
    )
    if not record:
        return
    chain = record.get("approval_chain") or record.get("approval_workflow") or []
    if not isinstance(chain, list):
        return
    updated_chain = []
    for step in chain:
        if isinstance(step, dict) and int(step.get("sequence") or 0) == int(
            approval.get("sequence") or 0
        ):
            step = {
                **step,
                "status": decision,
                "decision": decision,
                "reason": reason,
                "decided_at": decided_at,
                "decided_by": approval.get("assigned_user_id"),
                "decided_by_name": approval.get("assigned_user_name"),
            }
        updated_chain.append(step)
    source_status = record.get("status")
    if decision == "Rejected":
        source_status = "Rejected"
    elif updated_chain and all(
        str(step.get("status")) == "Approved"
        for step in updated_chain
        if isinstance(step, dict)
    ):
        source_status = "Approved"
    promotion_result = None
    rollback_result = None
    if source_collection == CHANGE_CONTROL_COLLECTION and source_id:
        if decision == "Rejected":
            rollback_result = await rollback_proposed_changes(
                db, str(source_id), decided_at, reason
            )
        elif source_status == "Approved":
            promotion_result = await promote_proposed_changes(
                db, str(source_id), decided_at
            )
            try:
                from services.pm.helpers.sop_cdc import (
                    apply_approved_change_control_to_sop,
                )

                cc = await db[CHANGE_CONTROL_COLLECTION].find_one(
                    {"change_control_id": str(source_id)}, {"_id": 0}
                )
                if cc:
                    sop_results = await apply_approved_change_control_to_sop(db, cc)
                    if sop_results:
                        promotion_result = promotion_result or {}
                        promotion_result["sop_promotions"] = sop_results
            except Exception:
                pass
    await db[source_collection].update_one(
        record_lookup_query(str(source_id)),
        {
            "$set": {
                "approval_chain": updated_chain,
                "approval_workflow": updated_chain,
                "approval_last_decision": decision,
                "approval_last_decision_at": decided_at,
                "status": source_status,
                "proposed_change_promotion": promotion_result,
                "proposed_change_rollback": rollback_result,
                "effective_at": (
                    decided_at
                    if source_status == "Approved"
                    else record.get("effective_at")
                ),
                "updated_at": utc_now(),
            }
        },
    )


async def record_approval_decision(
    db: Any,
    *,
    approval: dict,
    approval_id: str,
    decision: str,
    reason: str | None,
    signature_meaning: str | None,
    decision_source: str,
) -> dict:
    normalized_decision = decision.strip().title()
    decided_at = utc_now()
    update = {
        "status": normalized_decision,
        "decision": normalized_decision,
        "reason": reason,
        "signature_meaning": signature_meaning,
        "decision_source": decision_source,
        "decided_at": decided_at,
        "updated_at": decided_at,
    }
    update_result = await db["approvals"].update_one(
        {
            "approval_id": approval_id,
            "status": {"$nin": list(TERMINAL_APPROVAL_STATUSES)},
        },
        {"$set": update},
    )
    if update_result.modified_count != 1:
        raise ApprovalAlreadyDecidedError("Approval has already been decided.")
    updated_approval = {**approval, **update}
    await _apply_approval_decision_to_source(
        db, updated_approval, normalized_decision, reason, decided_at
    )
    approvals = await _approval_records_for_source(db, approval.get("source_id"))

    decision_event = {
        "event": "approval_decision_submitted",
        "event_type": "approval-decision",
        "approval_id": approval_id,
        "source_collection": approval.get("source_collection"),
        "source_id": approval.get("source_id"),
        "source_title": approval.get("source_title"),
        "decision": normalized_decision,
        "reason": reason,
        "signature_meaning": signature_meaning,
        "decision_source": decision_source,
        "assigned_user_id": approval.get("assigned_user_id"),
        "assigned_user_name": approval.get("assigned_user_name"),
        "assigned_user_email": approval.get("assigned_user_email"),
        "decided_at": decided_at.isoformat(),
        "approvals": approvals,
        "requires_electronic_signature": True,
        "signature_valid": True,
        "signature_id": f"approval-signature-{approval_id}",
        "target_node": "Electronic Signature",
    }
    decision_subject = None
    try:
        decision_subject = await nats_store.publish_approval_decision_event(
            decision_event
        )
    except Exception:
        pass

    await append_audit_entry(
        db,
        collection="approvals",
        action=f"approval_{normalized_decision.lower()}",
        record_id=approval_id,
        before=approval,
        after=updated_approval,
        update={
            "$set": update,
            "change_reason": reason if normalized_decision == "Rejected" else None,
            "signature_meaning": signature_meaning,
        },
        result={
            "downstream_decision_subject": decision_subject
            or settings.NATS_APPROVAL_DECISIONS_SUBJECT,
            "approval_audit_status": "pending_electronic_signature",
            "target_node": "Electronic Signature",
        },
    )
    return {
        "approval": updated_approval,
        "decision_event": decision_event,
        "audit_event": None,
        "decision_subject": decision_subject
        or settings.NATS_APPROVAL_DECISIONS_SUBJECT,
        "audit_subject": None,
        "approval_audit_status": "pending_electronic_signature",
        "approvals": approvals,
    }
