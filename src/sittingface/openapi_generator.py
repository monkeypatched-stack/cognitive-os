"""Generate OpenAPI specs from somatic capability and agent charts."""

from __future__ import annotations

from pathlib import Path

import yaml


def generate_capability_openapi(cap_values: dict) -> dict:
    """Generate OpenAPI spec from a capability values.yaml."""
    cap = cap_values.get("capability", {})
    operations = cap_values.get("operations", []) or cap_values.get("definition", {}).get("operations", [])
    endpoint = cap_values.get("endpoint", {})
    auth = cap_values.get("auth", {}) or cap_values.get("definition", {}).get("auth", {})

    base_url = endpoint.get("base_url", "http://localhost:8000")
    cap_name = cap.get("name", "unknown")
    description = cap.get("description", "")

    paths = {}
    for op in operations:
        method = op.get("method", "GET").lower()
        path = op.get("path", "/")
        paths[path] = {
            method: {
                "summary": op.get("description", ""),
                "operationId": f"{cap_name}_{op.get('name', 'unknown')}",
                "parameters": [
                    {
                        "name": p,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                    for p in op.get("required_params", [])
                ]
                + [
                    {
                        "name": p,
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                    for p in op.get("optional_params", [])
                ],
                "responses": {
                    "200": {"description": "Success"},
                    "400": {"description": "Bad request"},
                    "500": {"description": "Internal server error"},
                },
            }
        }

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": f"{cap_name} API",
            "description": description,
            "version": cap.get("version", "1.0.0"),
        },
        "servers": [{"url": base_url, "description": "Capability endpoint"}],
        "paths": paths,
        "components": {
            "securitySchemes": (
                {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                    }
                }
                if auth.get("type") == "bearer"
                else {}
            )
        },
    }

    return spec


def generate_agent_openapi(agent_values: dict) -> dict:
    """Generate OpenAPI spec from an agent values.yaml."""
    agent = agent_values.get("agent", {})
    identity = agent_values.get("identity", {})
    model = agent_values.get("model", {})
    tools = agent_values.get("tools", [])

    agent_name = agent.get("name", "unknown")
    description = agent.get("description", "")
    role = identity.get("role", "")

    paths = {
        "/query": {
            "post": {
                "summary": f"Send a query to {agent_name}",
                "operationId": f"{agent_name}_query",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string", "maxLength": 4096},
                                    "session_id": {"type": "string"},
                                    "context": {"type": "object"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Agent response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "answer": {"type": "string"},
                                        "confidence": {"type": "number"},
                                        "tools_used": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/health": {
            "get": {
                "summary": f"{agent_name} health check",
                "responses": {"200": {"description": "Healthy"}},
            }
        },
        "/tools": {
            "get": {
                "summary": f"List available tools for {agent_name}",
                "responses": {
                    "200": {
                        "description": "Tool list",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    }

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": f"{agent_name} API",
            "description": f"{description}\n\nRole: {role}\n\nModel: {model.get('provider', '?')}/{model.get('name', '?')}",
            "version": agent.get("version", "1.0.0"),
        },
        "paths": paths,
    }

    if tools:
        spec["components"] = {
            "schemas": {
                "Tool": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "description": {"type": "string"},
                    },
                }
            }
        }

    return spec


def generate_all_openapi(charts_dir: Path, output_dir: Path) -> list[Path]:
    """Generate OpenAPI specs for all capability and agent charts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []

    # Capability charts
    for values_file in sorted(charts_dir.rglob("values.yaml")):
        values = yaml.safe_load(values_file.read_text()) or {}
        if values.get("capability"):
            spec = generate_capability_openapi(values)
            name = values.get("capability", {}).get("name", values_file.parent.name)
            out_file = output_dir / f"{name}.openapi.yaml"
            out_file.write_text(yaml.dump(spec, default_flow_style=False, sort_keys=False))
            generated.append(out_file)
        elif values.get("agent"):
            spec = generate_agent_openapi(values)
            name = values.get("agent", {}).get("name", values_file.parent.name)
            out_file = output_dir / f"{name}.openapi.yaml"
            out_file.write_text(yaml.dump(spec, default_flow_style=False, sort_keys=False))
            generated.append(out_file)

    return generated
