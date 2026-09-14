from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.approval_chains import (
    approval_graph_relationships,
    create_approval_tracking_and_notifications,
    resolve_named_approval_chain,
    write_approval_chain_to_source,
)
from services.common.compliance import (
    CHANGE_CONTROL_COLLECTION,
    compliance_readiness,
    sha256_hex,
    utc_now,
)
from services.common.db import get_database
from services.documents.helpers import document_metadata as crud
from services.documents.helpers import document_workflows as workflow_crud
from services.documents.models.document_workflows import (
    DocumentWorkflowCreate,
    DocumentWorkflowStep,
)
from services.documents.models.document_metadata import (
    DocumentMetadataCreate,
    DocumentMetadataResponse,
    DocumentMetadataUpdate,
    DocumentStatus,
    DocumentType,
    DocumentViewLinkResponse,
    PaginatedDocumentMetadataResponse,
)

router = APIRouter()


DOCUMENT_UPLOAD_CHANGE_STATUSES = [
    "Open",
    "Draft",
    "Submitted",
    "Under Review",
    "Approved",
    "Implementation",
    "Verification",
]


def _current_user_id(current_user: dict | None) -> str:
    if not isinstance(current_user, dict):
        return "system"
    return str(
        current_user.get("user_id")
        or current_user.get("sub")
        or current_user.get("email")
        or "system"
    )


def _change_control_number(now, sequence: int) -> str:
    return f"SX-CHG-{now:%Y}-{sequence:04d}"


async def _ensure_document_upload_change_control_and_workflow(
    db: AsyncIOMotorDatabase, document: dict, current_user: dict | None
) -> None:
    document_id = str(document.get("document_id") or "")
    if not document_id:
        return

    user_id = _current_user_id(current_user)
    now = utc_now()
    document_affected_entities = [
        {
            "entity_id": document_id,
            "id": document_id,
            "name": document.get("document_name") or document_id,
            "type": "document",
            "collection": crud.COLLECTION,
            "status": document.get("document_status"),
            "resolved": True,
        }
    ]
    document_chain, document_basis = await resolve_named_approval_chain(
        db,
        source_collection=crud.COLLECTION,
        source_id=document_id,
        source_document=document,
        affected_entities=document_affected_entities,
        current_user_id=user_id,
    )
    await write_approval_chain_to_source(
        db,
        source_collection=crud.COLLECTION,
        source_id=document_id,
        chain=document_chain,
        basis=document_basis,
    )
    await create_approval_tracking_and_notifications(
        db,
        source_collection=crud.COLLECTION,
        source_id=document_id,
        source_title=document.get("document_name") or document_id,
        chain=document_chain,
        current_user_id=user_id,
    )
    existing_change = await db[CHANGE_CONTROL_COLLECTION].find_one(
        {
            "status": {"$in": DOCUMENT_UPLOAD_CHANGE_STATUSES},
            "affected_entities.entity_id": document_id,
            "change_source": "document_upload",
        },
        {"_id": 0},
    )
    if not existing_change:
        sequence = await db[CHANGE_CONTROL_COLLECTION].count_documents({}) + 1
        change_control_id = f"change_control-{uuid4().hex}"
        change_record = {
            "change_control_id": change_control_id,
            "change_control_number": _change_control_number(now, sequence),
            "standard": "GxP",
            "standards": compliance_readiness()["standards"],
            "standard_id": "ich-q10",
            "change_source": "document_upload",
            "title": f"Document upload approval: {document.get('document_name') or document_id}",
            "description": "Automatically created when a document file was uploaded.",
            "change_type": "Minor",
            "reason": "Uploaded document requires controlled review and approval before release.",
            "justification": "Document upload must enter change control and approval workflow for Part 11 traceability.",
            "initiating_facility": document.get("document_department")
            or document.get("document_project"),
            "affected_entity_class": "Document",
            "affected_entities": [
                {
                    "entity_id": document_id,
                    "id": document_id,
                    "name": document.get("document_name") or document_id,
                    "type": "document",
                    "collection": crud.COLLECTION,
                    "status": document.get("document_status"),
                    "resolved": True,
                }
            ],
            "affected_collections": [crud.COLLECTION],
            "impact_assessment": "Document upload impacts document control and requires review, approval, and release tracking.",
            "impact_assessment_generated": {
                "directly_affected_entities": [document_id],
                "connected_sops": [],
                "linked_work_orders": [],
                "qualified_operators": [],
                "open_batches": [],
                "downstream_compliance_records": [],
                "flags": {
                    "document_approval_required": True,
                    "quality_manager_signoff_required": True,
                },
            },
            "risk_score": 25,
            "approval_tier": "Document QA Approval",
            "approval_mode": "Sequential",
            "validation_required": False,
            "revalidation_required": False,
            "requalification_required": False,
            "sop_revision_required": document.get("document_type")
            in {"Report", "Other"},
            "retraining_required": False,
            "regulatory_notification_required": False,
            "stability_study_required": False,
            "implementation_tasks": [
                {
                    "task_id": f"task-{uuid4().hex}",
                    "title": "Review uploaded document",
                    "owner_role": "Reviewer",
                    "status": "Pending",
                    "evidence_required": False,
                },
                {
                    "task_id": f"task-{uuid4().hex}",
                    "title": "Approve document for controlled use",
                    "owner_role": "Approver",
                    "status": "Pending",
                    "evidence_required": True,
                },
            ],
            "training_records": [],
            "approval_chain": [],
            "approval_workflow": [],
            "approval_assignment_basis": {},
            "approval_chain_resolved": False,
            "approval_chain_resolved_at": None,
            "electronic_signatures": [],
            "effectiveness_check": {
                "required": False,
                "window_days": 0,
                "status": "Not Required",
            },
            "graph_relationships": [
                {
                    "type": "AFFECTS",
                    "target_collection": crud.COLLECTION,
                    "target_id": document_id,
                },
            ],
            "status": "Open",
            "initiating_date": now,
            "initiating_user": user_id,
            "created_at": now,
            "updated_at": now,
            "created_by": user_id,
            "updated_by": user_id,
        }
        change_chain, change_basis = await resolve_named_approval_chain(
            db,
            source_collection=CHANGE_CONTROL_COLLECTION,
            source_id=change_control_id,
            source_document=change_record,
            affected_entities=change_record["affected_entities"],
            current_user_id=user_id,
        )
        change_record.update(
            {
                "approval_chain": change_chain,
                "approval_workflow": change_chain,
                "approval_assignment_basis": change_basis,
                "approval_chain_resolved": True,
                "approval_chain_resolved_at": now,
                "graph_relationships": [
                    {
                        "type": "AFFECTS",
                        "target_collection": crud.COLLECTION,
                        "target_id": document_id,
                    },
                    *approval_graph_relationships(change_chain, change_basis),
                ],
            }
        )
        change_record["record_hash"] = sha256_hex(change_record)
        await db[CHANGE_CONTROL_COLLECTION].insert_one(change_record)
        await create_approval_tracking_and_notifications(
            db,
            source_collection=CHANGE_CONTROL_COLLECTION,
            source_id=change_control_id,
            source_title=change_record["title"],
            chain=change_chain,
            current_user_id=user_id,
        )

    existing_workflows = await workflow_crud.get_by_document_id(db, document_id)
    has_upload_workflow = any(
        (workflow.get("metadata") or {}).get("trigger") == "document_upload"
        for workflow in existing_workflows
    )
    if not has_upload_workflow:
        workflow = DocumentWorkflowCreate(
            document_id=document_id,
            workflow_name=f"Approval workflow: {document.get('document_name') or document_id}",
            workflow_type="Document Approval",
            status="In-Review",
            initiated_by=user_id,
            owner_id=document.get("document_owner") or user_id,
            reviewer_ids=[
                step["assigned_user_id"]
                for step in document_chain
                if step["assigned_user_id"]
            ],
            approver_ids=[
                step["assigned_user_id"]
                for step in document_chain
                if step["assigned_user_id"]
            ],
            steps=[
                DocumentWorkflowStep(
                    step_name=step.get("stage") or step.get("step") or "Approval",
                    sequence=step.get("sequence") or index,
                    assignee_id=step.get("assigned_user_id"),
                    assignee_name=step.get("assigned_user_name"),
                    status="Pending",
                )
                for index, step in enumerate(document_chain, start=1)
            ],
            due_at=document.get("document_due_date"),
            notes="Automatically started from document upload.",
            metadata={
                "trigger": "document_upload",
                "change_control_required": True,
                "approval_assignment_basis": document_basis,
            },
        )
        await workflow_crud.create(db, workflow)


def _parse_tags(tags: str | None) -> list[str]:
    if not tags:
        return []
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def _document_create_from_form(
    document_name: str,
    document_type: DocumentType,
    document_version: str,
    document_url: str,
    document_status: DocumentStatus,
    document_owner: str,
    document_preparer: str,
    document_reviewer: str,
    document_approver: str,
    document_due_date: str,
    created_by: str,
    last_modified_by: str | None,
    document_id: str | None,
    document_tags: str | None,
    document_department: str | None,
    document_project: str | None,
    document_confidentiality: str | None,
    document_retention_period: str | None,
    document_description: str | None,
    file: UploadFile,
) -> DocumentMetadataCreate:
    return DocumentMetadataCreate(
        document_id=document_id,
        document_name=document_name,
        document_type=document_type,
        document_version=document_version,
        document_tags=_parse_tags(document_tags),
        document_url=document_url,
        document_status=document_status,
        document_owner=document_owner,
        document_preparer=document_preparer,
        document_reviewer=document_reviewer,
        document_approver=document_approver,
        document_due_date=document_due_date,
        document_department=document_department,
        document_project=document_project,
        document_confidentiality=document_confidentiality,
        document_retention_period=document_retention_period,
        document_description=document_description,
        created_by=created_by,
        last_modified_by=last_modified_by or created_by,
    )


def _local_file_response(record: dict, as_attachment: bool) -> FileResponse:
    local_path = record.get("local_path")
    document_url = record.get("document_url")
    if not local_path and isinstance(document_url, str):
        parsed_url = urlparse(document_url)
        if parsed_url.scheme in ("", "file"):
            local_path = (
                parsed_url.path if parsed_url.scheme == "file" else document_url
            )
    if not local_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No local file is attached to this document",
        )
    if not Path(local_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local document file is missing",
        )
    return FileResponse(
        local_path,
        media_type=record.get("content_type") or "application/octet-stream",
        filename=record.get("file_name") or record["document_id"],
        content_disposition_type="attachment" if as_attachment else "inline",
    )


def _attachment_url(document_url: str, record: dict) -> str:
    parsed = urlparse(document_url)
    filename = (
        record.get("file_name")
        or Path(parsed.path).name
        or record.get("document_id")
        or "document"
    )
    safe_name = Path(str(filename)).name.replace('"', "")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("download", "true")
    query.setdefault(
        "response-content-disposition", f'attachment; filename="{safe_name}"'
    )
    return urlunparse(
        parsed._replace(query=urlencode(query, doseq=True, quote_via=quote))
    )


def _remote_or_local_file_response(record: dict, *, as_attachment: bool):
    document_url = record.get("document_url")
    if record.get("storage_backend") == "s3" or record.get("s3_key"):
        s3_url = crud.generate_s3_view_url(record, as_attachment=as_attachment)
        if s3_url:
            return RedirectResponse(s3_url)
    if isinstance(document_url, str) and document_url.startswith(
        ("http://", "https://")
    ):
        return RedirectResponse(
            _attachment_url(document_url, record) if as_attachment else document_url
        )
    return _local_file_response(record, as_attachment=as_attachment)


def _view_url_for_record(record: dict, request: Request, expires_in: int) -> str:
    document_url = record.get("document_url")
    if isinstance(document_url, str) and document_url.startswith(
        ("http://", "https://")
    ):
        return document_url
    if record.get("storage_backend") == "s3" or record.get("s3_key"):
        s3_url = crud.generate_s3_view_url(
            record, expires_in=expires_in, as_attachment=False
        )
        if s3_url:
            return s3_url
    return str(
        request.url_for("public_view_document_file", document_id=record["document_id"])
    )


@router.get("/", response_model=PaginatedDocumentMetadataResponse)
async def list_document_metadata(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDocumentMetadataResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/by-type/{document_type}", response_model=list[DocumentMetadataResponse])
async def list_document_metadata_by_type(
    document_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_type(db, document_type)


@router.get("/by-owner/{document_owner}", response_model=list[DocumentMetadataResponse])
async def list_document_metadata_by_owner(
    document_owner: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_owner(db, document_owner)


@router.get(
    "/by-department/{document_department}",
    response_model=list[DocumentMetadataResponse],
)
async def list_document_metadata_by_department(
    document_department: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_department(db, document_department)


@router.get(
    "/by-project/{document_project}", response_model=list[DocumentMetadataResponse]
)
async def list_document_metadata_by_project(
    document_project: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_project(db, document_project)


@router.get("/by-tag/{tag}", response_model=list[DocumentMetadataResponse])
async def list_document_metadata_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_tag(db, tag)


@router.get(
    "/by-status/{document_status}", response_model=list[DocumentMetadataResponse]
)
async def list_document_metadata_by_status(
    document_status: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_status(db, document_status)


@router.get("/count")
async def count_document_metadata(
    document_type: str | None = Query(None),
    document_owner: str | None = Query(None),
    document_department: str | None = Query(None),
    document_project: str | None = Query(None),
    document_status: str | None = Query(None),
    tag: str | None = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    total = await crud.count(
        db,
        document_type=document_type,
        document_owner=document_owner,
        document_department=document_department,
        document_project=document_project,
        document_status=document_status,
        tag=tag,
    )
    return {"total": total}


@router.post(
    "/upload",
    response_model=DocumentMetadataResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_metadata_with_upload(
    document_name: str = Form(...),
    document_type: DocumentType = Form(...),
    document_version: str = Form(...),
    document_owner: str = Form(...),
    document_preparer: str = Form(...),
    document_reviewer: str = Form(...),
    document_approver: str = Form(...),
    document_due_date: str = Form(...),
    created_by: str = Form(...),
    file: UploadFile = File(...),
    document_url: str = Form("pending-upload"),
    document_status: DocumentStatus = Form(DocumentStatus.DRAFT),
    document_id: str | None = Form(None),
    document_tags: str | None = Form(None),
    tags: str | None = Form(None),
    document_department: str | None = Form(None),
    document_project: str | None = Form(None),
    document_confidentiality: str | None = Form(None),
    document_retention_period: str | None = Form(None),
    document_description: str | None = Form(None),
    last_modified_by: str | None = Form(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-documents")),
):
    data = _document_create_from_form(
        document_name=document_name,
        document_type=document_type,
        document_version=document_version,
        document_url=document_url,
        document_status=document_status,
        document_owner=document_owner,
        document_preparer=document_preparer,
        document_reviewer=document_reviewer,
        document_approver=document_approver,
        document_due_date=document_due_date,
        created_by=created_by,
        last_modified_by=last_modified_by,
        document_id=document_id,
        document_tags=document_tags or tags,
        document_department=document_department,
        document_project=document_project,
        document_confidentiality=document_confidentiality,
        document_retention_period=document_retention_period,
        document_description=document_description,
        file=file,
    )
    if data.document_id and await crud.get_by_id(db, data.document_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document metadata '{data.document_id}' already exists",
        )
    record = await crud.create_with_file(db, data, file)
    await _ensure_document_upload_change_control_and_workflow(db, record, current_user)
    return record


@router.post("/{document_id}/upload", response_model=DocumentMetadataResponse)
async def upload_document_file(
    document_id: str,
    file: UploadFile = File(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.attach_file(db, document_id, file)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    await _ensure_document_upload_change_control_and_workflow(db, updated, current_user)
    return updated


@router.get("/{document_id}/view-link", response_model=DocumentViewLinkResponse)
async def get_document_view_link(
    document_id: str,
    request: Request,
    expires_in: int = Query(3600, ge=60, le=604800),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return DocumentViewLinkResponse(
        document_id=document_id,
        storage_backend=record.get("storage_backend"),
        view_url=_view_url_for_record(record, request, expires_in),
        expires_in=expires_in if record.get("storage_backend") == "s3" else None,
    )


@router.get("/{document_id}/view", name="view_document_file")
async def view_document_file(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return _remote_or_local_file_response(record, as_attachment=False)


@router.get("/{document_id}/public-view", name="public_view_document_file")
async def public_view_document_file(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return _remote_or_local_file_response(record, as_attachment=False)


@router.get("/{document_id}/download")
async def download_document_file(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return _remote_or_local_file_response(record, as_attachment=True)


@router.get("/{document_id}/public-download")
async def public_download_document_file(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return _remote_or_local_file_response(record, as_attachment=True)


@router.get("/{document_id}", response_model=DocumentMetadataResponse)
async def get_document_metadata(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_id(db, document_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return record


@router.post(
    "/",
    response_model=DocumentMetadataResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_metadata(
    data: DocumentMetadataCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-documents")),
):
    if data.document_id and await crud.get_by_id(db, data.document_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document metadata '{data.document_id}' already exists",
        )
    record = await crud.create(db, data)
    if (
        record.get("storage_backend")
        or record.get("file_name")
        or record.get("s3_key")
        or record.get("local_path")
    ):
        await _ensure_document_upload_change_control_and_workflow(
            db, record, current_user
        )
    return record


@router.patch("/{document_id}", response_model=DocumentMetadataResponse)
async def update_document_metadata(
    document_id: str,
    data: DocumentMetadataUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.update(db, document_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
    return updated


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_metadata(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-documents")),
):
    if not await crud.delete(db, document_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document metadata '{document_id}' not found",
        )
