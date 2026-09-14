from typing import Any
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Depends, Query, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError
import httpx
from neo4j import AsyncGraphDatabase

from services.common.db import get_database
from services.common.config import settings
from services.common.auth import permission_ids
from services.common.n8n_auth import n8n_webhook_auth_headers
from services.auth.helpers import nats_store
from services.auth.helpers.tokens import decode_access_token
from services.auth.helpers.graph_store import (
    delete_canvas_node as delete_neo4j_canvas_node,
)
from services.process_definitions.utils import validate_outbound_url
from services.process_definitions.models.process_definition import (
    PaginatedProcessDefinitionCanvasLayoutResponse,
    ProcessDefinitionCreate,
    ProcessDefinitionCanvasAttachment,
    ProcessDefinitionCanvasHyperlink,
    ProcessDefinitionCanvasLayoutCreate,
    ProcessDefinitionCanvasLayoutResponse,
    ProcessDefinitionCanvasLayoutUpdate,
    ProcessDefinitionCanvasBackendCall,
    ProcessDefinitionCanvasFlowSimulationRequest,
    ProcessDefinitionCanvasFlowSimulationResponse,
    ProcessDefinitionCanvasNodeExecutionRequest,
    ProcessDefinitionCanvasNodeExecutionResponse,
    ProcessDefinitionCanvasNodePosition,
    ProcessDefinitionCanvasNodeProperties,
    ProcessDefinitionCanvasNodePropertiesUpdate,
    ProcessDefinitionCanvasPageId,
    ProcessDefinitionCanvasWebhookDispatchRequest,
    ProcessDefinitionCanvasWebhookDispatchResponse,
    ProcessDefinitionCanvasWebhookEvent,
    ProcessDefinitionUpdate,
    ProcessDefinitionResponse,
    PaginatedProcessDefinitionResponse,
    ProcessDefinitionEndpointInfo,
    normalize_canvas_page_id,
)
from services.process_definitions.models.process_node_catalog import (
    ProcessNodeCatalogItem,
    ProcessNodeCatalogResponse,
)
from services.process_definitions.helpers import process_definition as crud
from services.process_definitions.helpers.process_node_catalog import (
    get_catalog_item,
    get_node_catalog,
)
from services.process_definitions.node_services.executor import (
    execute_canvas_node_service,
)
from services.process_definitions.node_services.flow_simulator import (
    simulate_canvas_flow,
)
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()
log = logging.getLogger("uvicorn.error")

bearer_scheme = HTTPBearer()

PERMISSION_ALIASES = {
    "perm-view-process-definitions": {"perm-view-workflows"},
    "perm-create-process-definitions": {"perm-create-workflows"},
    "perm-update-process-definitions": {"perm-update-workflows"},
    "perm-delete-process-definitions": {"perm-delete-workflows"},
}


async def _publish_canvas_webhook_nats_event(
    *,
    layout_id: str,
    canvas_node_id: str,
    event: ProcessDefinitionCanvasWebhookEvent,
    node: dict | None = None,
) -> None:
    event_type = f"canvas-webhook-{event.direction or 'event'}"
    payload = {
        "event_id": event.event_id or str(uuid4()),
        "event_type": event_type,
        "source": "process_definition-service",
        "layout_id": layout_id,
        "canvas_node_id": canvas_node_id,
        "node_id": (node or {}).get("node_id") or (node or {}).get("id"),
        "node_name": (node or {}).get("name") or (node or {}).get("title"),
        "provider": event.provider,
        "direction": event.direction,
        "status": event.status,
        "status_code": event.status_code,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "payload": event.model_dump(mode="json"),
    }
    try:
        await nats_store.publish_typed_event(payload, event_type=event_type)
    except Exception:
        log.exception(
            "Failed to publish canvas webhook event to NATS event_type=%s layout_id=%s canvas_node_id=%s",
            event_type,
            layout_id,
            canvas_node_id,
        )


def _webhook_settings_from_node(node: dict) -> dict:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    provider = str(
        node.get("automationProvider")
        or metadata.get("automationProvider")
        or "webhook"
    ).lower()
    direction = str(
        node.get("automationDirection")
        or metadata.get("automationDirection")
        or "outgoing"
    ).lower()
    url = str(
        node.get("webhookUrl")
        or node.get("webhook_url")
        or node.get("n8nWebhookUrl")
        or metadata.get("webhookUrl")
        or metadata.get("webhook_url")
        or metadata.get("n8nWebhookUrl")
        or ""
    ).strip()
    method = str(
        node.get("webhookMethod")
        or node.get("n8nMethod")
        or metadata.get("webhookMethod")
        or metadata.get("n8nMethod")
        or "POST"
    ).upper()
    secret = str(
        node.get("webhookSecret") or metadata.get("webhookSecret") or ""
    ).strip()
    return {
        "provider": provider,
        "direction": direction,
        "url": url,
        "method": method,
        "secret": secret,
    }


async def _request_json_or_body(request: Request) -> dict:
    try:
        value = await request.json()
        return value if isinstance(value, dict) else {"value": value}
    except Exception:
        body = await request.body()
        return {"body": body.decode("utf-8", errors="replace")}


def _node_type_from_record(node: dict) -> str | None:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return node.get("node_type") or metadata.get("nodeType") or metadata.get("type")


def _backend_call_from_node(
    node: dict, override: ProcessDefinitionCanvasBackendCall | None = None
) -> ProcessDefinitionCanvasBackendCall | None:
    if override is not None:
        return override
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    backend = node.get("backend")
    if not isinstance(backend, dict):
        backend = metadata.get("backend")
    if not isinstance(backend, dict):
        backend = {}
    backend = dict(backend)
    if config.get("url") and not backend.get("url"):
        backend["url"] = config["url"]
    if config.get("method"):
        backend["method"] = config["method"]
    headers = dict(backend.get("headers") or {})
    if isinstance(config.get("headers"), dict):
        headers.update(
            {
                str(key): str(value)
                for key, value in config["headers"].items()
                if value is not None
            }
        )
    if config.get("client_key") and "x-client-key" not in {
        key.lower(): value for key, value in headers.items()
    }:
        headers["x-client-key"] = str(config["client_key"])
    if config.get("client_secret") and "x-client-secret" not in {
        key.lower(): value for key, value in headers.items()
    }:
        headers["x-client-secret"] = str(config["client_secret"])
    backend["headers"] = headers
    if not backend.get("service"):
        backend["service"] = "process_definition"
    if not backend.get("action"):
        backend["action"] = _node_type_from_record(node)
    return ProcessDefinitionCanvasBackendCall(**backend)


def _process_definition_endpoint_catalog() -> list[ProcessDefinitionEndpointInfo]:
    base = "/api/v1/process-definitions"
    return [
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/endpoints",
            description="List process_definition backend endpoints.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET", path=f"{base}/", description="List process definitions."
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/by-owner/{{owner}}",
            description="List process definitions by owner.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/by-status/{{status_value}}",
            description="List process definitions by status.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/by-tag/{{tag}}",
            description="List process definitions by tag.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/{{process_definition_id}}",
            description="Get one process_definition.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST", path=f"{base}/", description="Create a process_definition."
        ),
        ProcessDefinitionEndpointInfo(
            method="PATCH",
            path=f"{base}/{{process_definition_id}}",
            description="Update a process_definition.",
        ),
        ProcessDefinitionEndpointInfo(
            method="PATCH",
            path=f"{base}/{{process_definition_id}}/status/{{status_value}}",
            description="Update process_definition status.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/{{process_definition_id}}",
            description="Delete a process_definition.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/node-catalog",
            description="List supported canvas node types.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/node-catalog/{{identifier}}",
            description="Get one canvas node type by label, type, or key.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas",
            description="List process_definition canvas layouts.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/by-process_definition/{{process_definition_id}}",
            description="List canvas layouts by process_definition.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/by-page/{{page_id}}",
            description="List canvas layouts by page.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas",
            description="Create a process_definition canvas layout.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/{{layout_id}}",
            description="Get a process_definition canvas layout.",
        ),
        ProcessDefinitionEndpointInfo(
            method="PATCH",
            path=f"{base}/canvas/{{layout_id}}",
            description="Update a process_definition canvas layout.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/simulate",
            description="Simulate a full canvas flow and return a node-level trace.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/drop",
            description="Add a typed backend-capable node to a canvas.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/execute",
            description="Execute a canvas node backend action.",
        ),
        ProcessDefinitionEndpointInfo(
            method="GET",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/properties",
            description="Get canvas node properties.",
        ),
        ProcessDefinitionEndpointInfo(
            method="PATCH",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/properties",
            description="Update canvas node properties.",
        ),
        ProcessDefinitionEndpointInfo(
            method="PUT",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/metadata",
            description="Replace canvas node metadata.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/hyperlinks",
            description="Add a canvas node hyperlink.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/hyperlinks/{{index}}",
            description="Remove a canvas node hyperlink.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/attachments",
            description="Add a canvas node attachment.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/attachments/{{index}}",
            description="Remove a canvas node attachment.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/webhook/incoming",
            description="Receive an incoming webhook for a canvas node.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/nodes/{{canvas_node_id}}/webhook/dispatch",
            description="Dispatch a canvas node webhook/http call.",
        ),
        ProcessDefinitionEndpointInfo(
            method="POST",
            path=f"{base}/canvas/{{layout_id}}/clear",
            description="Clear canvas nodes and edges.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/{{layout_id}}/board",
            description="Clear canvas nodes and edges.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/{{layout_id}}/nodes",
            description="Clear canvas nodes and edges.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/nodes/{{canvas_node_id}}",
            description="Delete a canvas node from layouts and graph mirror.",
        ),
        ProcessDefinitionEndpointInfo(
            method="DELETE",
            path=f"{base}/canvas/{{layout_id}}",
            description="Delete a canvas layout.",
        ),
    ]


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/endpoints", response_model=list[ProcessDefinitionEndpointInfo])
async def list_process_definition_endpoints(
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    return _process_definition_endpoint_catalog()


@router.get("/")
async def list_process_definitions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    """Return a paginated list of all process definitions."""
    process_definitions, total = await crud.get_all(db, page=page, page_size=page_size)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": process_definitions,
    }


# ── Filtered list endpoints ───────────────────────────────────────────────────


@router.get("/by-owner/{owner}", response_model=list[ProcessDefinitionResponse])
async def list_process_definitions_by_owner(
    owner: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    """Return all process definitions belonging to the given owner."""
    return await crud.get_by_owner(db, owner)


@router.get(
    "/by-status/{status_value}", response_model=PaginatedProcessDefinitionResponse
)
async def list_process_definitions_by_status(
    status_value: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    """Return a paginated list of process definitions filtered by lifecycle status."""
    process_definitions, total = await crud.get_by_status(
        db, status_value, page=page, page_size=page_size
    )
    return PaginatedProcessDefinitionResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=process_definitions,
    )


@router.get("/by-tag/{tag}", response_model=list[ProcessDefinitionResponse])
async def list_process_definitions_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    """Return all process definitions that carry the given tag."""
    return await crud.get_by_tag(db, tag)


# ── Canvas layouts ────────────────────────────────────────────────────────────


@router.get("/canvas/node-catalog", response_model=ProcessNodeCatalogResponse)
async def get_canvas_node_catalog(
    _: dict = Depends(get_current_user),
):
    return get_node_catalog()


@router.get("/canvas/node-catalog/{identifier}", response_model=ProcessNodeCatalogItem)
async def get_canvas_node_catalog_item(
    identifier: str,
    _: dict = Depends(get_current_user),
):
    item = get_catalog_item(identifier)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition node catalog item '{identifier}' not found",
        )
    return item


@router.get("/canvas/{layout_id}")
async def get_canvas_layout(
    layout_id: str,
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        async with driver.session() as session:
            nodes_result = await session.run("""
                MATCH (n)
                WHERE n:Plant OR n:Line OR n:Stage OR n:Workstation OR n:Machine
                   OR n:Equipment OR n:Device OR n:Worker OR n:Role OR n:SOP
                RETURN labels(n) AS labels, properties(n) AS props
                """)
            nodes = []
            async for record in nodes_result:
                labels = record["labels"]
                props = dict(record["props"])
                label = labels[0] if labels else "Unknown"
                entity_id = props.get("entity_id") or props.get("id")
                name = props.get("name") or props.get("title") or entity_id
                nodes.append(
                    {
                        "node_id": f"{label.lower()}s:{entity_id}",
                        "entity_id": entity_id,
                        "collection": f"{label.lower()}s",
                        "name": name,
                        "properties": props,
                    }
                )

            edges_result = await session.run("""
                MATCH (a)-[r]->(b)
                WHERE (a:Plant OR a:Line OR a:Stage OR a:Workstation OR a:Machine
                       OR a:Equipment OR a:Device OR a:Worker OR a:Role OR a:SOP)
                  AND (b:Plant OR b:Line OR b:Stage OR b:Workstation OR b:Machine
                       OR b:Equipment OR b:Device OR b:Worker OR b:Role OR b:SOP)
                RETURN labels(a) AS sl, properties(a) AS sp,
                       type(r) AS rel,
                       labels(b) AS tl, properties(b) AS tp
                """)
            edges = []
            async for record in edges_result:
                sl = record["sl"][0] if record["sl"] else "Unknown"
                tl = record["tl"][0] if record["tl"] else "Unknown"
                sp = dict(record["sp"])
                tp = dict(record["tp"])
                s_id = sp.get("entity_id") or sp.get("id")
                t_id = tp.get("entity_id") or tp.get("id")
                s_node = f"{sl.lower()}s:{s_id}"
                t_node = f"{tl.lower()}s:{t_id}"
                rel = record["rel"]
                edges.append(
                    {
                        "edge_id": f"{s_node}->{t_node}:{rel}",
                        "source_node_id": s_node,
                        "target_node_id": t_node,
                        "relationship_type": rel,
                        "label": rel.replace("_", " ").title(),
                    }
                )

            return {
                "layout_id": layout_id,
                "page_id": layout_id,
                "nodes": nodes,
                "edges": edges,
                "links": edges,
                "connections": [],
                "visual_nodes": [],
            }
    finally:
        await driver.close()


@router.post(
    "/canvas/{layout_id}/simulate",
    response_model=ProcessDefinitionCanvasFlowSimulationResponse,
)
async def simulate_canvas_layout_flow(
    layout_id: str,
    data: ProcessDefinitionCanvasFlowSimulationRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    try:
        result = await simulate_canvas_flow(
            db=db,
            layout_id=layout_id,
            payload=data.payload,
            start_node_id=data.start_node_id,
            max_steps=data.max_steps,
            persist_trace=data.persist_trace,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return ProcessDefinitionCanvasFlowSimulationResponse(**result)


@router.post(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/execute",
    response_model=ProcessDefinitionCanvasNodeExecutionResponse,
)
async def execute_canvas_node(
    layout_id: str,
    canvas_node_id: str,
    data: ProcessDefinitionCanvasNodeExecutionRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    node = await crud.get_canvas_node_record(db, layout_id, canvas_node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )

    node_type = _node_type_from_record(node)
    backend = _backend_call_from_node(node, data.backend)
    event_payload = {
        "source": "sentinel-x-process_definition-service",
        "layoutId": layout_id,
        "nodeId": canvas_node_id,
        "nodeType": node_type,
        "node": node,
        "backend": backend.model_dump() if backend else None,
        "payload": data.payload,
    }
    event = ProcessDefinitionCanvasWebhookEvent(
        event_id=str(uuid4()),
        provider="process_definition-node",
        direction="execution",
        payload=event_payload,
        status=(
            "queued" if backend and not backend.url and not data.dry_run else "executed"
        ),
    )

    status_code = None
    response_body = None
    error = None
    ok = True
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    if not data.dry_run:
        result = await execute_canvas_node_service(
            db=db,
            layout_id=layout_id,
            node_type=node_type,
            node=node,
            backend=backend,
            config=config,
            payload=data.payload,
        )
        ok = result.ok
        status_code = result.status_code
        response_body = result.response_body
        error = result.error
        if node_type in {"mqtt out", "websocket out", "tcp out", "udp out"}:
            event.status = "sent" if ok else "failed"
        elif node_type in {"mqtt in", "websocket in", "tcp in", "udp in"}:
            event.status = "registered" if ok else "failed"
        else:
            event.status = (
                response_body.get("status", "executed")
                if isinstance(response_body, dict)
                else (
                    "sent"
                    if ok and backend and backend.url
                    else "executed" if ok else "failed"
                )
            )
        event.status_code = status_code
        event.response_body = response_body
        event.error = error

    await _publish_canvas_webhook_nats_event(
        layout_id=layout_id,
        canvas_node_id=canvas_node_id,
        event=event,
        node=node,
    )
    await crud.record_canvas_node_webhook_event(db, layout_id, canvas_node_id, event)
    return ProcessDefinitionCanvasNodeExecutionResponse(
        ok=ok,
        layout_id=layout_id,
        node_id=canvas_node_id,
        node_type=node_type,
        backend=backend,
        status_code=status_code,
        response_body=response_body,
        error=error,
        event=event,
    )


@router.post(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/webhook/incoming",
    response_model=ProcessDefinitionCanvasNodeProperties,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_canvas_node_webhook_event(
    layout_id: str,
    canvas_node_id: str,
    request: Request,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    node = await crud.get_canvas_node_record(db, layout_id, canvas_node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    webhook_settings = _webhook_settings_from_node(node)
    configured_secret = webhook_settings["secret"]
    supplied_secret = (
        request.headers.get("x-canvas-webhook-secret")
        or request.query_params.get("secret")
        or ""
    )
    if configured_secret and supplied_secret != configured_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid canvas webhook secret",
        )
    payload = await _request_json_or_body(request)
    event = ProcessDefinitionCanvasWebhookEvent(
        event_id=str(uuid4()),
        provider=webhook_settings["provider"],
        direction="incoming",
        payload=payload,
        headers={key: value for key, value in request.headers.items()},
        status="received",
    )
    await _publish_canvas_webhook_nats_event(
        layout_id=layout_id,
        canvas_node_id=canvas_node_id,
        event=event,
        node=node,
    )
    properties = await crud.record_canvas_node_webhook_event(
        db, layout_id, canvas_node_id, event
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.post(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/webhook/dispatch",
    response_model=ProcessDefinitionCanvasWebhookDispatchResponse,
)
async def dispatch_canvas_node_webhook_event(
    layout_id: str,
    canvas_node_id: str,
    data: ProcessDefinitionCanvasWebhookDispatchRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    node = await crud.get_canvas_node_record(db, layout_id, canvas_node_id)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    webhook_settings = _webhook_settings_from_node(node)
    url = (data.url or webhook_settings["url"]).strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No webhook URL configured for this canvas node",
        )
    if not validate_outbound_url(url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL is not allowed (SSRF protection)",
        )
    method = (data.method or webhook_settings["method"] or "POST").upper()
    connected = await crud.get_connected_canvas_nodes(db, layout_id, canvas_node_id)
    payload = data.payload or {}
    if not payload:
        payload = {
            "source": "sentinel-x-process_definition-service",
            "layoutId": layout_id,
            "nodeId": canvas_node_id,
            "provider": webhook_settings["provider"],
            "direction": "outgoing",
            "node": node,
            "incoming": connected["incoming"],
            "outgoing": connected["outgoing"],
        }
    event = ProcessDefinitionCanvasWebhookEvent(
        event_id=str(uuid4()),
        provider=webhook_settings["provider"],
        direction="outgoing",
        payload=payload,
        status="pending",
    )
    headers = dict(data.headers or {})
    if webhook_settings["provider"] == "n8n":
        headers = n8n_webhook_auth_headers(headers)
    if webhook_settings["secret"]:
        headers.setdefault("x-canvas-webhook-secret", webhook_settings["secret"])
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(method, url, json=payload, headers=headers)
        try:
            response_body = response.json()
        except Exception:
            response_body = response.text
        event.status = "sent" if response.is_success else "failed"
        event.status_code = response.status_code
        event.response_body = response_body
        await _publish_canvas_webhook_nats_event(
            layout_id=layout_id,
            canvas_node_id=canvas_node_id,
            event=event,
            node=node,
        )
        return ProcessDefinitionCanvasWebhookDispatchResponse(
            ok=response.is_success,
            status_code=response.status_code,
            response_body=response_body,
            event=event,
        )
    except httpx.HTTPError as exc:
        event.status = "failed"
        event.error = str(exc)
        await _publish_canvas_webhook_nats_event(
            layout_id=layout_id,
            canvas_node_id=canvas_node_id,
            event=event,
            node=node,
        )
        return ProcessDefinitionCanvasWebhookDispatchResponse(
            ok=False, error=str(exc), event=event
        )


@router.get(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/properties",
    response_model=ProcessDefinitionCanvasNodeProperties,
)
async def get_canvas_node_properties(
    layout_id: str,
    canvas_node_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    properties = await crud.get_canvas_node_properties(db, layout_id, canvas_node_id)
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.patch(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/properties",
    response_model=ProcessDefinitionCanvasNodeProperties,
)
async def update_canvas_node_properties(
    layout_id: str,
    canvas_node_id: str,
    data: ProcessDefinitionCanvasNodePropertiesUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.update_canvas_node_properties(
        db, layout_id, canvas_node_id, data
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.put(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/metadata",
    response_model=ProcessDefinitionCanvasNodeProperties,
)
async def replace_canvas_node_metadata(
    layout_id: str,
    canvas_node_id: str,
    metadata: dict[str, Any],
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.replace_canvas_node_metadata(
        db, layout_id, canvas_node_id, metadata
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.post(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/hyperlinks",
    response_model=ProcessDefinitionCanvasNodeProperties,
    status_code=status.HTTP_201_CREATED,
)
async def add_canvas_node_hyperlink(
    layout_id: str,
    canvas_node_id: str,
    hyperlink: ProcessDefinitionCanvasHyperlink,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.add_canvas_node_hyperlink(
        db, layout_id, canvas_node_id, hyperlink
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.delete(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/hyperlinks/{index}",
    response_model=ProcessDefinitionCanvasNodeProperties,
)
async def remove_canvas_node_hyperlink(
    layout_id: str,
    canvas_node_id: str,
    index: int,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.remove_canvas_node_list_item(
        db, layout_id, canvas_node_id, "hyperlinks", index
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.post(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/attachments",
    response_model=ProcessDefinitionCanvasNodeProperties,
    status_code=status.HTTP_201_CREATED,
)
async def add_canvas_node_attachment(
    layout_id: str,
    canvas_node_id: str,
    attachment: ProcessDefinitionCanvasAttachment,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.add_canvas_node_attachment(
        db, layout_id, canvas_node_id, attachment
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.delete(
    "/canvas/{layout_id}/nodes/{canvas_node_id}/attachments/{index}",
    response_model=ProcessDefinitionCanvasNodeProperties,
)
async def remove_canvas_node_attachment(
    layout_id: str,
    canvas_node_id: str,
    index: int,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    properties = await crud.remove_canvas_node_list_item(
        db, layout_id, canvas_node_id, "attachments", index
    )
    if properties is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
        )
    return properties


@router.post(
    "/canvas/{layout_id}/nodes/drop",
    response_model=ProcessDefinitionCanvasLayoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_dropped_canvas_node(
    layout_id: str,
    data: ProcessDefinitionCanvasNodePosition,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.add_canvas_node(db, layout_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition canvas layout '{layout_id}' not found",
        )
    return updated


@router.delete("/canvas/nodes/{canvas_node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canvas_node(
    canvas_node_id: str,
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        async with driver.session() as session:
            parts = canvas_node_id.split(":", 1)
            if len(parts) == 2:
                collection, entity_id = parts
                label = collection.rstrip("s").capitalize()
                result = await session.run(
                    f"MATCH (n:{label} {{entity_id: $eid}}) DETACH DELETE n RETURN count(n) AS c",
                    eid=entity_id,
                )
                record = await result.single()
                if record and record["c"] > 0:
                    return
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ProcessDefinition canvas node '{canvas_node_id}' not found",
            )
    finally:
        await driver.close()


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/{process_definition_id}", response_model=ProcessDefinitionResponse)
async def get_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    """Fetch a single process_definition by its unique process_definition_id."""
    record = await crud.get_by_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=ProcessDefinitionResponse, status_code=status.HTTP_201_CREATED
)
async def create_process_definition(
    data: ProcessDefinitionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-process-definitions")),
):
    """Register a new process_definition. Returns 409 if process_definition_id already exists."""
    if await crud.get_by_id(db, data.process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessDefinition '{data.process_definition_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{process_definition_id}", response_model=ProcessDefinitionResponse)
async def update_process_definition(
    process_definition_id: str,
    data: ProcessDefinitionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    """Partially update a process_definition's fields. Returns 404 if the process_definition does not exist."""
    try:
        updated = await crud.update(db, process_definition_id, data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
    return updated


@router.post(
    "/{process_definition_id}/submit", response_model=ProcessDefinitionResponse
)
async def submit_process_definition_for_review(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.submit_for_review(db, process_definition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft process definition '{process_definition_id}' not found",
        )
    return updated


@router.post(
    "/{process_definition_id}/approve", response_model=ProcessDefinitionResponse
)
async def approve_process_definition(
    process_definition_id: str,
    approved_by: str | None = Query(None),
    make_effective: bool = Query(True),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-update-process-definitions")),
):
    approver = (
        approved_by
        or current_user.get("sub")
        or current_user.get("user_id")
        or current_user.get("username")
    )
    updated = await crud.approve(
        db, process_definition_id, approved_by=approver, make_effective=make_effective
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reviewable process definition '{process_definition_id}' not found",
        )
    return updated


@router.post(
    "/{process_definition_id}/retire", response_model=ProcessDefinitionResponse
)
async def retire_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.retire(db, process_definition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
    return updated


@router.post(
    "/{process_definition_id}/new-revision",
    response_model=ProcessDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_process_definition_revision(
    process_definition_id: str,
    new_process_definition_id: str = Query(...),
    revision: str = Query(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-process-definitions")),
):
    if await crud.get_by_id(db, new_process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessDefinition '{new_process_definition_id}' already exists",
        )
    created = await crud.create_new_revision(
        db, process_definition_id, new_process_definition_id, revision
    )
    if not created:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
    return created


@router.post("/backfill/recipe-routes")
async def backfill_recipe_routes_as_process_definitions(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-process-definitions")),
):
    return await crud.backfill_recipe_routes(db)


@router.patch(
    "/{process_definition_id}/status", response_model=ProcessDefinitionResponse
)
async def update_process_definition_status(
    process_definition_id: str,
    status_value: str = Query(..., alias="value"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    """Update only the lifecycle status of a process_definition."""
    updated = await crud.update_status(db, process_definition_id, status_value)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{process_definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-definitions")),
):
    """Permanently remove a process_definition record. Returns 404 if the process_definition does not exist."""
    if not await crud.delete(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessDefinition '{process_definition_id}' not found",
        )
