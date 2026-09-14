#!/usr/bin/env python3
"""Generate cerebellum capability values.yaml files from the capability source tree."""

from pathlib import Path
import yaml

CAPABILITIES_DIR = Path("src/cerebellum/capabilities")
CHARTS_DIR = Path("somatic/charts")

# Capability definitions: (name, id, platform, description, tags, operations)
CAPS = [
    (
        "mongodb",
        "cap-db-mongodb-001",
        "Database/Document",
        "MongoDB document database driver for CRUD operations and change streams",
        ["database", "mongodb", "document", "nosql", "motor", "pymongo"],
        [
            (
                "find",
                "READ",
                "/{collection}/find",
                "Query documents",
                ["collection"],
                ["filter", "projection", "sort", "limit"],
            ),
            (
                "insert_one",
                "CREATE",
                "/{collection}/insert",
                "Insert a document",
                ["collection", "document"],
                [],
            ),
            (
                "update_one",
                "UPDATE",
                "/{collection}/update",
                "Update a document",
                ["collection", "filter", "update"],
                ["upsert"],
            ),
            (
                "delete_one",
                "DELETE",
                "/{collection}/delete",
                "Delete a document",
                ["collection", "filter"],
                [],
            ),
        ],
    ),
    (
        "redis",
        "cap-db-redis-001",
        "Database/KeyValue",
        "Redis key-value store for caching and session state",
        ["database", "redis", "cache", "session"],
        [
            ("get", "READ", "/{key}", "Get a value", ["key"], []),
            ("set", "CREATE", "/{key}", "Set a value", ["key", "value"], ["ttl"]),
            ("delete", "DELETE", "/{key}", "Delete a key", ["key"], []),
            (
                "expire",
                "UPDATE",
                "/{key}/expire",
                "Set TTL on a key",
                ["key", "ttl"],
                [],
            ),
        ],
    ),
    (
        "neo4j",
        "cap-db-neo4j-001",
        "Database/Graph",
        "Neo4j graph database for entity relationships",
        ["database", "neo4j", "graph", "cypher"],
        [
            (
                "query",
                "READ",
                "/query",
                "Execute a Cypher query",
                ["cypher"],
                ["params"],
            ),
            (
                "create_node",
                "CREATE",
                "/node",
                "Create a node",
                ["labels", "properties"],
                [],
            ),
            (
                "create_relationship",
                "CREATE",
                "/relationship",
                "Create a relationship",
                ["start_id", "end_id", "type"],
                ["properties"],
            ),
            ("delete_node", "DELETE", "/node/{id}", "Delete a node", ["id"], []),
        ],
    ),
    (
        "influxdb",
        "cap-db-influxdb-001",
        "Database/TimeSeries",
        "InfluxDB time-series database for events and metrics",
        ["database", "influxdb", "time-series", "events"],
        [
            (
                "write_lp",
                "CREATE",
                "/write_lp",
                "Write line protocol data",
                ["lines"],
                [],
            ),
            ("query_sql", "READ", "/query_sql", "Query with SQL", ["sql"], []),
            ("query_influxql", "READ", "/query", "Query with InfluxQL", ["query"], []),
        ],
    ),
    (
        "elasticsearch",
        "cap-db-elasticsearch-001",
        "Database/Search",
        "Elasticsearch for full-text search and audit indexing",
        ["database", "elasticsearch", "search", "audit"],
        [
            (
                "index",
                "CREATE",
                "/{index}/doc",
                "Index a document",
                ["index", "document"],
                ["id"],
            ),
            (
                "search",
                "READ",
                "/{index}/_search",
                "Search documents",
                ["index", "query"],
                ["size"],
            ),
            (
                "delete",
                "DELETE",
                "/{index}/doc/{id}",
                "Delete a document",
                ["index", "id"],
                [],
            ),
        ],
    ),
    (
        "rest_api",
        "cap-api-rest-001",
        "API/REST",
        "Generic REST API client with auth and rate limiting",
        ["api", "rest", "http", "client"],
        [
            (
                "request",
                "READ",
                "/proxy",
                "Execute an HTTP request",
                ["method", "url"],
                ["headers", "body"],
            )
        ],
    ),
    (
        "graphql",
        "cap-api-graphql-001",
        "API/GraphQL",
        "GraphQL API client for schema-driven queries",
        ["api", "graphql", "query"],
        [
            (
                "query",
                "READ",
                "/query",
                "Execute a GraphQL query",
                ["query"],
                ["variables"],
            ),
            (
                "mutate",
                "CREATE",
                "/mutate",
                "Execute a GraphQL mutation",
                ["mutation"],
                ["variables"],
            ),
        ],
    ),
    (
        "webhook",
        "cap-api-webhook-001",
        "API/Webhook",
        "Webhook receiver and dispatcher",
        ["api", "webhook", "event"],
        [
            (
                "register",
                "CREATE",
                "/register",
                "Register a webhook endpoint",
                ["url", "events"],
                [],
            ),
            (
                "dispatch",
                "CREATE",
                "/dispatch",
                "Dispatch a webhook event",
                ["event", "payload"],
                [],
            ),
        ],
    ),
    (
        "mongodb_driver",
        "cap-db-mongodb-002",
        "Database/Document",
        "MongoDB async driver wrapper",
        ["database", "mongodb", "async"],
        [
            ("connect", "CREATE", "/connect", "Connect to MongoDB", ["url"], []),
            ("disconnect", "DELETE", "/disconnect", "Disconnect from MongoDB", [], []),
        ],
    ),
    (
        "elasticsearch_driver",
        "cap-db-elasticsearch-002",
        "Database/Search",
        "Elasticsearch async client wrapper",
        ["database", "elasticsearch", "async"],
        [
            ("connect", "CREATE", "/connect", "Connect to Elasticsearch", ["url"], []),
            (
                "bulk_index",
                "CREATE",
                "/bulk",
                "Bulk index documents",
                ["index", "documents"],
                [],
            ),
        ],
    ),
    (
        "agents",
        "cap-agent-001",
        "Agent/Discovery",
        "External agent discovery and communication",
        ["agent", "discovery", "federation"],
        [
            ("discover", "READ", "/discover", "Discover available agents", [], []),
            (
                "invoke",
                "CREATE",
                "/invoke",
                "Invoke an external agent",
                ["agent_id", "task"],
                [],
            ),
        ],
    ),
    (
        "browsers",
        "cap-browser-001",
        "Browser/Automation",
        "Browser automation for web scraping and testing",
        ["browser", "automation", "playwright"],
        [
            ("navigate", "READ", "/navigate", "Navigate to a URL", ["url"], []),
            ("screenshot", "READ", "/screenshot", "Take a screenshot", ["url"], []),
            (
                "extract",
                "READ",
                "/extract",
                "Extract text from a page",
                ["url", "selector"],
                [],
            ),
        ],
    ),
    (
        "cloud",
        "cap-cloud-001",
        "Cloud/Infrastructure",
        "Multi-cloud resource management",
        ["cloud", "aws", "azure", "gcp"],
        [
            (
                "list_resources",
                "READ",
                "/resources",
                "List cloud resources",
                ["provider"],
                ["region"],
            ),
            (
                "provision",
                "CREATE",
                "/provision",
                "Provision a resource",
                ["provider", "type", "config"],
                [],
            ),
        ],
    ),
    (
        "communication",
        "cap-comm-001",
        "Communication/Messaging",
        "Multi-channel communication (email, SMS, chat)",
        ["communication", "email", "sms", "slack"],
        [
            (
                "send",
                "CREATE",
                "/send",
                "Send a message",
                ["channel", "to", "message"],
                ["subject"],
            )
        ],
    ),
    (
        "opensource",
        "cap-ent-opensource-001",
        "Enterprise/OpenSource",
        "Open-source enterprise system integrations (ERPNext, Odoo, etc.)",
        ["enterprise", "erp", "opensource"],
        [
            ("list_systems", "READ", "/systems", "List available systems", [], []),
            (
                "connect",
                "CREATE",
                "/connect",
                "Connect to an enterprise system",
                ["system", "config"],
                [],
            ),
        ],
    ),
    (
        "proprietary",
        "cap-ent-proprietary-001",
        "Enterprise/Proprietary",
        "Proprietary enterprise system integrations (SAP, Oracle, etc.)",
        ["enterprise", "erp", "sap", "oracle"],
        [
            ("list_systems", "READ", "/systems", "List available systems", [], []),
            (
                "connect",
                "CREATE",
                "/connect",
                "Connect to an enterprise system",
                ["system", "config"],
                [],
            ),
        ],
    ),
    (
        "streaming",
        "cap-event-001",
        "Event/Streaming",
        "Event streaming for Kafka, NATS, RabbitMQ",
        ["event", "streaming", "kafka", "nats"],
        [
            (
                "publish",
                "CREATE",
                "/publish",
                "Publish an event",
                ["topic", "event"],
                [],
            ),
            (
                "subscribe",
                "READ",
                "/subscribe",
                "Subscribe to events",
                ["topic"],
                ["callback"],
            ),
        ],
    ),
    (
        "infrastructure",
        "cap-infra-001",
        "Infrastructure/Container",
        "Container and orchestration management (Docker, K8s)",
        ["infrastructure", "docker", "kubernetes"],
        [
            ("list_containers", "READ", "/containers", "List containers", [], []),
            (
                "deploy",
                "CREATE",
                "/deploy",
                "Deploy a service",
                ["name", "image"],
                ["replicas"],
            ),
        ],
    ),
    (
        "productivity",
        "cap-prod-001",
        "Productivity/Office",
        "Productivity tool integrations (Calendar, Drive, etc.)",
        ["productivity", "calendar", "drive"],
        [
            (
                "list_events",
                "READ",
                "/events",
                "List calendar events",
                ["date_range"],
                [],
            ),
            (
                "create_event",
                "CREATE",
                "/events",
                "Create a calendar event",
                ["title", "start", "end"],
                [],
            ),
        ],
    ),
    (
        "robotics",
        "cap-robot-001",
        "Robotics/IoT",
        "Robotics and IoT protocol integrations (ROS, MQTT, OPC-UA)",
        ["robotics", "iot", "mqtt", "opcua"],
        [
            (
                "publish_sensor",
                "CREATE",
                "/sensor",
                "Publish sensor data",
                ["topic", "value"],
                [],
            ),
            (
                "subscribe_sensor",
                "READ",
                "/sensor/subscribe",
                "Subscribe to sensor data",
                ["topic"],
                [],
            ),
        ],
    ),
    (
        "runtimes",
        "cap-runtime-001",
        "Runtime/Execution",
        "Multi-language runtime execution (Python, Node, JVM)",
        ["runtime", "execution", "sandbox"],
        [
            (
                "execute",
                "CREATE",
                "/execute",
                "Execute code in a runtime",
                ["language", "code"],
                ["timeout"],
            ),
            ("list_runtimes", "READ", "/runtimes", "List available runtimes", [], []),
        ],
    ),
    (
        "search",
        "cap-search-001",
        "Search/Web",
        "Web search and document search capabilities",
        ["search", "web", "document"],
        [
            (
                "web_search",
                "READ",
                "/web",
                "Search the web",
                ["query"],
                ["max_results"],
            ),
            (
                "doc_search",
                "READ",
                "/docs",
                "Search documents",
                ["query", "collection"],
                [],
            ),
        ],
    ),
    (
        "source_control",
        "cap-scm-001",
        "SourceControl/Git",
        "Source control integration (GitHub, GitLab, Bitbucket)",
        ["source-control", "git", "github"],
        [
            ("list_repos", "READ", "/repos", "List repositories", [], []),
            (
                "create_pr",
                "CREATE",
                "/pull-request",
                "Create a pull request",
                ["repo", "title", "branch"],
                ["body"],
            ),
        ],
    ),
    (
        "storage",
        "cap-storage-001",
        "Storage/Object",
        "Object storage (S3, MinIO, local filesystem)",
        ["storage", "s3", "object"],
        [
            (
                "upload",
                "CREATE",
                "/upload",
                "Upload a file",
                ["bucket", "key", "data"],
                [],
            ),
            ("download", "READ", "/download", "Download a file", ["bucket", "key"], []),
            ("list", "READ", "/list", "List objects", ["bucket"], ["prefix"]),
        ],
    ),
    (
        "workflows",
        "cap-workflow-001",
        "Workflow/Orchestration",
        "Workflow orchestration (n8n, Temporal, Airflow)",
        ["workflow", "orchestration", "n8n", "temporal"],
        [
            (
                "start",
                "CREATE",
                "/start",
                "Start a workflow",
                ["workflow_id"],
                ["input"],
            ),
            ("status", "READ", "/status/{id}", "Get workflow status", ["id"], []),
            ("cancel", "DELETE", "/cancel/{id}", "Cancel a workflow", ["id"], []),
        ],
    ),
]


def build_values(name, id_, platform, description, tags, operations):
    """Build values.yaml content for a capability."""
    ops = []
    for op_name, method, path, desc, req, opt in operations:
        ops.append(
            {
                "name": op_name,
                "method": method,
                "path": path,
                "description": desc,
                "required_params": req,
                "optional_params": opt,
                "paginated": False,
                "pagination_cursor_field": "",
            }
        )

    return {
        "capability": {
            "name": name,
            "id": id_,
            "platform": platform,
            "description": description,
            "version": "1.0.0",
            "status": "active",
            "authored_by": "Prashun Javeri",
            "approved_by": "Prashun Javeri",
            "effective_date": "2026-06-27",
            "review_date": "2026-12-27",
            "module_id": "cerebellum",
            "module_name": "Cerebellum",
            "plant_id": "",
            "line_id": "",
            "stage_id": "",
            "tags": tags,
            "references": [f"src/cerebellum/capabilities/{name.replace('_driver', '').replace('cap-', '')}.py"],
            "document_ids": [],
            "notes": "",
            "remarks": "",
        },
        "auth": {
            "type": "none",
            "header_name": "",
            "format_template": "",
            "scopes": [],
            "refresh_endpoint": "",
            "ttl_seconds": None,
        },
        "endpoint": {
            "base_url": f"http://localhost:8000/{name}",
            "protocol": "http",
            "version": "1.0.0",
            "tls_verify": False,
            "timeout_seconds": 30,
        },
        "operations": ops,
        "rate_limit": {
            "requests_per_hour": 1000,
            "requests_per_minute": None,
            "burst_capacity": None,
            "strategy": "fixed_window",
            "backoff_multiplier": 2.0,
            "max_retries": 3,
            "retry_on_status": [500, 502, 503],
        },
        "error_handling": {
            "transient_statuses": [500, 502, 503, 504],
            "permanent_statuses": [400, 401, 403, 404],
            "retry_delay_ms": 1000,
            "circuit_breaker_threshold": 5,
            "circuit_breaker_window_seconds": 60,
        },
        "tests": {
            "mock_responses": {},
            "integration_tests": [f"test_{name}_connect", f"test_{name}_execute"],
            "test_scenarios": ["happy_path", "timeout", "error"],
        },
    }


def main():
    for name, id_, platform, desc, tags, ops in CAPS:
        values = build_values(name, id_, platform, desc, tags, ops)

        chart_dir = CHARTS_DIR / "cerebellum" / "capabilities" / name
        chart_dir.mkdir(parents=True, exist_ok=True)

        with open(chart_dir / "values.yaml", "w") as f:
            yaml.dump(values, f, default_flow_style=False, sort_keys=False)

        print(f"  OK {name} → {chart_dir}/values.yaml")

    print(f"\nGenerated {len(CAPS)} capability charts")


if __name__ == "__main__":
    main()
