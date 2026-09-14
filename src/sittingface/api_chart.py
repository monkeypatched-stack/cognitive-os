"""Service domain catalogue and DDD API chart renderer.

Shared between monkeypatched CLI and ApiChartAgent so neither
creates a circular import.
"""

from __future__ import annotations


def service_domain(service_type: str) -> dict:
    """Return service-specific domain model keyed by service_type slug."""
    slug = service_type.lower().replace(" ", "-").replace("_", "-")

    catalogue = {
        "todo": {
            "description": "Todo task management application",
            "aggregate": "Todo",
            "aggregate_plural": "Todos",
            "entities": ["TodoItem"],
            "value_objects": ["Title", "DueDate", "Priority", "Status"],
            "domain_events": [
                "TodoCreated",
                "TodoCompleted",
                "TodoDeleted",
                "TodoReassigned",
            ],
            "invariants": [
                {
                    "id": "TODO-INV-001",
                    "rule": "title_not_empty",
                    "statement": "A Todo must have a non-empty title of at most 200 characters.",
                    "severity": "critical",
                    "rejection": "REJECTED — Todo title is empty or exceeds 200 chars.",
                },
                {
                    "id": "TODO-INV-002",
                    "rule": "cannot_complete_deleted",
                    "statement": "A deleted Todo must never transition to completed.",
                    "severity": "critical",
                    "rejection": "REJECTED — Deleted Todo marked completed.",
                },
                {
                    "id": "TODO-INV-003",
                    "rule": "due_date_not_past_on_create",
                    "statement": "DueDate must not be set to a date in the past at creation time.",
                    "severity": "high",
                    "rejection": "REJECTED — DueDate is in the past.",
                },
            ],
            "endpoints": [
                "POST   /todos/           — create todo",
                "GET    /todos/           — list todos (filter by status, priority, assignee)",
                "GET    /todos/{id}       — get todo by ID",
                "PATCH  /todos/{id}       — update title, due date, priority",
                "POST   /todos/{id}/complete — mark complete",
                "DELETE /todos/{id}       — soft-delete",
            ],
        },
        "work-order": {
            "description": "Work order management for maintenance and operations",
            "aggregate": "WorkOrder",
            "aggregate_plural": "WorkOrders",
            "entities": ["Task", "Assignment", "Attachment"],
            "value_objects": [
                "WorkOrderNumber",
                "Priority",
                "Status",
                "Location",
                "ScheduledWindow",
            ],
            "domain_events": [
                "WorkOrderCreated",
                "WorkOrderAssigned",
                "WorkOrderStarted",
                "WorkOrderCompleted",
                "WorkOrderCancelled",
                "TaskAdded",
            ],
            "invariants": [
                {
                    "id": "WO-INV-001",
                    "rule": "number_unique",
                    "statement": "WorkOrderNumber must be globally unique and immutable after creation.",
                    "severity": "critical",
                    "rejection": "REJECTED — Duplicate or mutated WorkOrderNumber.",
                },
                {
                    "id": "WO-INV-002",
                    "rule": "must_be_assigned_before_start",
                    "statement": "A WorkOrder must have at least one Assignment before transitioning to In Progress.",
                    "severity": "critical",
                    "rejection": "REJECTED — WorkOrder started without assignment.",
                },
                {
                    "id": "WO-INV-003",
                    "rule": "completed_requires_all_tasks_done",
                    "statement": "A WorkOrder may only be completed when all its Tasks are in a terminal state.",
                    "severity": "critical",
                    "rejection": "REJECTED — WorkOrder completed with open tasks.",
                },
                {
                    "id": "WO-INV-004",
                    "rule": "cancelled_not_restartable",
                    "statement": "A cancelled WorkOrder must never transition back to any active state.",
                    "severity": "high",
                    "rejection": "REJECTED — Cancelled WorkOrder reactivated.",
                },
            ],
            "endpoints": [
                "POST   /work-orders/                    — create work order",
                "GET    /work-orders/                    — list (filter by status, priority, location, assignee)",
                "GET    /work-orders/{id}                — get detail with tasks and assignments",
                "PATCH  /work-orders/{id}               — update description, scheduled window, priority",
                "POST   /work-orders/{id}/assign        — assign technician",
                "POST   /work-orders/{id}/start         — transition to In Progress",
                "POST   /work-orders/{id}/complete      — close work order",
                "POST   /work-orders/{id}/cancel        — cancel with reason",
                "POST   /work-orders/{id}/tasks         — add task",
                "PATCH  /work-orders/{id}/tasks/{tid}   — update task status",
            ],
        },
        "inventory": {
            "description": "Inventory and stock management",
            "aggregate": "InventoryItem",
            "aggregate_plural": "InventoryItems",
            "entities": ["StockMovement", "Supplier", "Location"],
            "value_objects": [
                "SKU",
                "Quantity",
                "ReorderPoint",
                "UnitOfMeasure",
                "LotNumber",
            ],
            "domain_events": [
                "ItemCreated",
                "StockReceived",
                "StockIssued",
                "StockAdjusted",
                "ReorderTriggered",
                "ItemDiscontinued",
            ],
            "invariants": [
                {
                    "id": "INV-INV-001",
                    "rule": "sku_unique",
                    "statement": "SKU must be globally unique and immutable after creation.",
                    "severity": "critical",
                    "rejection": "REJECTED — Duplicate or mutated SKU.",
                },
                {
                    "id": "INV-INV-002",
                    "rule": "quantity_never_negative",
                    "statement": "Stock quantity must never go below zero.",
                    "severity": "critical",
                    "rejection": "REJECTED — Stock quantity is negative.",
                },
                {
                    "id": "INV-INV-003",
                    "rule": "issue_requires_sufficient_stock",
                    "statement": "A stock issue must be rejected if available quantity is less than requested.",
                    "severity": "critical",
                    "rejection": "REJECTED — Issue exceeds available stock.",
                },
                {
                    "id": "INV-INV-004",
                    "rule": "reorder_auto_triggered",
                    "statement": "A ReorderTriggered event must be raised whenever quantity drops at or below ReorderPoint.",
                    "severity": "high",
                    "rejection": "REJECTED — Reorder not triggered at threshold.",
                },
            ],
            "endpoints": [
                "POST   /inventory/                        — create item",
                "GET    /inventory/                        — list (filter by location, supplier, low-stock)",
                "GET    /inventory/{id}                    — get item detail with movements",
                "PATCH  /inventory/{id}                   — update description, reorder point",
                "POST   /inventory/{id}/receive           — receive stock (quantity, lot, supplier)",
                "POST   /inventory/{id}/issue             — issue stock (quantity, destination)",
                "POST   /inventory/{id}/adjust            — manual adjustment with reason",
                "GET    /inventory/{id}/movements         — stock movement history",
            ],
        },
        "crm": {
            "description": "Customer relationship management",
            "aggregate": "Contact",
            "aggregate_plural": "Contacts",
            "entities": ["Interaction", "Opportunity", "Note"],
            "value_objects": [
                "Email",
                "Phone",
                "Address",
                "LifecycleStage",
                "OpportunityStage",
            ],
            "domain_events": [
                "ContactCreated",
                "InteractionLogged",
                "OpportunityOpened",
                "OpportunityWon",
                "OpportunityLost",
                "ContactMerged",
            ],
            "invariants": [
                {
                    "id": "CRM-INV-001",
                    "rule": "email_unique",
                    "statement": "No two Contacts may share the same email address.",
                    "severity": "critical",
                    "rejection": "REJECTED — Duplicate email on Contact.",
                },
                {
                    "id": "CRM-INV-002",
                    "rule": "opportunity_value_positive",
                    "statement": "An Opportunity value must be a positive decimal.",
                    "severity": "high",
                    "rejection": "REJECTED — Opportunity value is not positive.",
                },
                {
                    "id": "CRM-INV-003",
                    "rule": "closed_opportunity_immutable",
                    "statement": "A Won or Lost Opportunity must never be reopened or have its stage changed.",
                    "severity": "critical",
                    "rejection": "REJECTED — Closed Opportunity mutated.",
                },
            ],
            "endpoints": [
                "POST   /contacts/                        — create contact",
                "GET    /contacts/                        — list (search by name, email, stage)",
                "GET    /contacts/{id}                    — get with interactions and opportunities",
                "PATCH  /contacts/{id}                   — update contact details",
                "POST   /contacts/{id}/interactions      — log interaction (call, email, meeting)",
                "POST   /contacts/{id}/opportunities     — open opportunity",
                "PATCH  /contacts/{id}/opportunities/{oid} — update opportunity stage/value",
                "POST   /contacts/merge                  — merge duplicate contacts",
            ],
        },
    }

    aliases = {
        "todos": "todo",
        "task": "todo",
        "tasks": "todo",
        "workorder": "work-order",
        "work_order": "work-order",
        "wo": "work-order",
        "stock": "inventory",
        "warehouse": "inventory",
        "contacts": "crm",
        "customer": "crm",
    }
    key = aliases.get(slug, slug)

    if key in catalogue:
        return catalogue[key]

    title = service_type.replace("-", " ").replace("_", " ").title()
    return {
        "description": f"{title} management service",
        "aggregate": title.replace(" ", ""),
        "aggregate_plural": title.replace(" ", "") + "s",
        "entities": [f"{title.replace(' ', '')}Item"],
        "value_objects": ["Status", "Reference"],
        "domain_events": [
            f"{title.replace(' ', '')}Created",
            f"{title.replace(' ', '')}Updated",
            f"{title.replace(' ', '')}Deleted",
        ],
        "invariants": [
            {
                "id": f"{slug.upper()[:6]}-INV-001",
                "rule": "reference_unique",
                "statement": f"Every {title} must have a unique, immutable reference identifier.",
                "severity": "critical",
                "rejection": f"REJECTED — Duplicate {title} reference.",
            },
            {
                "id": f"{slug.upper()[:6]}-INV-002",
                "rule": "deleted_not_mutable",
                "statement": f"A deleted {title} must not accept further mutations.",
                "severity": "critical",
                "rejection": f"REJECTED — Mutated deleted {title}.",
            },
        ],
        "endpoints": [
            f"POST   /{slug}s/       — create",
            f"GET    /{slug}s/       — list",
            f"GET    /{slug}s/{{id}} — get by ID",
            f"PATCH  /{slug}s/{{id}} — update",
            f"DELETE /{slug}s/{{id}} — soft-delete",
        ],
    }


def render_api_chart(service_type: str, domain: dict) -> str:
    """Render a service-specific DDD API values.yaml as a YAML string."""
    import yaml as _yaml

    svc_name = service_type.lower().replace(" ", "-").replace("_", "-")
    agg = domain["aggregate"]
    entities_str = ", ".join(domain["entities"])
    vos_str = ", ".join(domain["value_objects"])
    events_str = ", ".join(domain["domain_events"])
    invariants = domain["invariants"] + [
        {
            "id": f"{svc_name.upper()[:6]}-AUTH-001",
            "rule": "auth_on_every_protected_route",
            "statement": "Every non-public route must declare the get_current_user dependency.",
            "severity": "critical",
            "rejection": "REJECTED — Protected route missing auth dependency.",
        },
        {
            "id": f"{svc_name.upper()[:6]}-LOG-001",
            "rule": "lemon_structured_logging",
            "statement": "Every request handler must emit at least one structured Lemon log entry with component, correlation_id, and user_id.",
            "severity": "high",
            "rejection": "REJECTED — Handler missing Lemon structured log.",
        },
        {
            "id": f"{svc_name.upper()[:6]}-OBS-001",
            "rule": "sentry_middleware_only",
            "statement": "Sentry capture must only happen in the global exception middleware, never inside business logic.",
            "severity": "high",
            "rejection": "REJECTED — Manual sentry_sdk.capture_exception in business logic.",
        },
        {
            "id": f"{svc_name.upper()[:6]}-OBS-002",
            "rule": "otel_middleware_first",
            "statement": "OpenTelemetryMiddleware must be registered before any route-level middleware.",
            "severity": "high",
            "rejection": "REJECTED — OpenTelemetry middleware out of order.",
        },
        {
            "id": f"{svc_name.upper()[:6]}-DDD-001",
            "rule": "domain_no_framework_imports",
            "statement": "domain/ files must never import from fastapi, motor, httpx, or any infrastructure package.",
            "severity": "critical",
            "rejection": "REJECTED — Domain layer contains framework import.",
        },
        {
            "id": f"{svc_name.upper()[:6]}-DDD-002",
            "rule": "domain_events_on_mutation",
            "statement": f"Every {agg} mutation must raise and publish at least one domain event.",
            "severity": "high",
            "rejection": f"REJECTED — {agg} mutation produces no domain event.",
        },
    ]

    cot_steps = [
        {
            "step": 1,
            "title": "Domain layer — entities, value objects, aggregates, events, repository ABC",
            "description": f"Pure Python only. {agg} aggregate root enforces invariants, emits {events_str}. Subdirs: entities/, value_objects/, aggregates/, repositories/, events/, services/, specifications/, exceptions/, policies/",
        },
        {
            "step": 2,
            "title": "Application layer — commands, queries, handlers, dto",
            "description": "Handlers orchestrate: load aggregate → invoke behavior → persist → emit events. No framework imports. Subdirs: commands/, queries/, handlers/, dto/",
        },
        {
            "step": 3,
            "title": "Infrastructure layer — Motor repository, database connection",
            "description": f"Motor{agg}Repository in infrastructure/persistence/motor/. AsyncIOMotorClient factory in infrastructure/database/. No SQLAlchemy.",
        },
        {
            "step": 4,
            "title": "API layer — FastAPI router, Pydantic schemas, JWT dependencies",
            "description": "router.py calls handlers ONLY. All protected routes use Depends(get_current_user) from dependencies.py.",
        },
        {
            "step": 5,
            "title": "Main + settings — lifespan, Sentry, /health, structured logging",
            "description": "Lifespan: Sentry → Motor → yield → close. /health endpoint. Timing middleware.",
        },
    ]

    rejected = [inv["rejection"] for inv in invariants]
    data = {
        "module": {
            "name": f"{svc_name}-api",
            "description": domain["description"],
        },
        "preamble": {
            "statement": (
                f"You are a senior software architect generating a production-grade FastAPI service "
                f"for {domain['description']}. The service is structured around Domain-Driven Design "
                f"with a {agg} aggregate root, entities [{entities_str}], and value objects [{vos_str}]. "
                f"Authentication uses JWT Bearer tokens validated in a FastAPI dependency. "
                f"All requests are structured-logged through the Lemon logger with correlation-ID propagation. "
                f"Errors are captured in Sentry via a global middleware. "
                f"All inbound/outbound spans are emitted via OpenTelemetry middleware to the Lemon collector."
            )
        },
        "invariants": invariants,
        "cot": {"steps": cot_steps},
        "reviewGate": {
            "mode": "constitutional",
            "outcomes": {
                "approved": f"APPROVED — {svc_name} API conforms to all DDD, auth, and observability invariants.",
                "rejected": rejected,
            },
        },
    }
    return _yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
