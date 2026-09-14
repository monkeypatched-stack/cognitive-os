import json
import re
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from fastapi import HTTPException, status

from services.common.config import settings
from services.common.neo4j_mirror import _neo4j_label, _singular, cleanup_neo4j_bloat

_driver = None
CANVAS_MONGO_ENTITY_MAP = {
    "plants": ("industrial_plants", "plant_id"),
    "lines": ("industrial_lines", "line_id"),
    "stages": ("industrial_stages", "stage_id"),
    "workstations": ("workstations", "workstation_id"),
    "machines": ("pharmaceutical_machines", "machine_id"),
    "equipment": ("pharmaceutical_equipment", "equipment_id"),
    "parts": ("machine_parts", "part_id"),
    "machine_parts": ("machine_parts", "part_id"),
    "tools": ("tools", "tool_id"),
    "equipment_groups": ("equipment_groups", "equipment_group_id"),
    "machine_groups": ("machine_groups", "machine_group_id"),
    "raw_material_groups": ("raw_material_groups", "raw_material_group_id"),
    "tag_groups": ("tag_groups", "tag_group_id"),
    "products": ("products", "product_id"),
}


def _neo4j_relationship_type(value: Any) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).upper()
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate or not candidate[0].isalpha():
        return "CANVAS_LINK"
    return candidate


def _get_driver():
    global _driver
    if _driver is None:
        try:
            neo4j = import_module("neo4j")
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Neo4j driver is not installed. Install project requirements.",
            ) from exc
        _driver = neo4j.AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


async def close_graph_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def _raise_neo4j_http_error(exc: Exception) -> None:
    await close_graph_driver()
    error_name = exc.__class__.__name__
    neo4j_code = getattr(exc, "code", "") or ""
    if error_name == "AuthError" or "Security" in neo4j_code:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Neo4j authentication failed. Set NEO4J_USER/NEO4J_PASSWORD "
                "to match the running Neo4j instance, or recreate the Neo4j "
                "data volume if you want to use the default dev credentials."
            ),
        ) from exc
    if error_name in {"ServiceUnavailable", "SessionExpired"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Neo4j is unavailable: {exc}",
        ) from exc
    raise exc


def is_graph_key(key: str) -> bool:
    return (
        key
        in {
            "factory-layout-symbol-canvas",
            "factory-layout-symbol-canvas:seed-version",
            "document-management-cad-canvas",
            "pid-symbol-canvas-nodes",
            "indus-dashboard-process-definition-steps",
            "indus-dashboard-process-definition-prechecks",
            "indus-dashboard-process-definition-postchecks",
            "indus-dashboard-process-definition-constraints",
            "indus-dashboard-corrective-actions",
            "supply-chain-graph-tabs",
            "supply-chain-graph-rows",
            "supply-chain-graph-canvas",
            "supply-chain-graph-canvas-positions",
            "supply-chain-graph-canvas-links",
            "supply-chain-graph-canvas-visual-nodes",
        }
        or key.endswith("-canvas-positions")
        or key.endswith("-canvas-links")
        or key.endswith("-canvas-visual-nodes")
        or key.startswith("supply-chain-graph-rows-")
        or key.startswith("indus-dashboard-process-definition-")
        or key == "indus-dashboard-corrective-actions"
        or "-canvas-" in key
    )


def graph_id_for_key(key: str) -> str:
    if key.startswith("supply-chain-graph-") and "-canvas-" in key:
        prefix = key.removesuffix("-canvas-positions")
        prefix = prefix.removesuffix("-canvas-links")
        prefix = prefix.removesuffix("-canvas-visual-nodes")
        return prefix.replace("supply-chain-graph-", "supply-chain-graph:", 1)
    if key.startswith("supply-chain-graph-rows-"):
        tab_id = key.removeprefix("supply-chain-graph-rows-")
        return f"supply-chain-graph:{tab_id}"
    if key.startswith("supply-chain-graph"):
        return "supply-chain-graph"
    if key.endswith("-canvas-positions"):
        return key.removesuffix("-canvas-positions")
    if key.endswith("-canvas-links"):
        return key.removesuffix("-canvas-links")
    if key.endswith("-canvas-visual-nodes"):
        return key.removesuffix("-canvas-visual-nodes")
    if key.endswith("-canvas-nodes"):
        return key.removesuffix("-nodes")
    return key


def state_type_for_key(key: str) -> str:
    if key.endswith("-canvas-positions"):
        return "positions"
    if key.endswith("-canvas-links"):
        return "links"
    if key.endswith("-canvas-visual-nodes") or key.endswith("-canvas-nodes"):
        return "visual_nodes"
    if key.startswith("supply-chain-graph-rows"):
        return "rows"
    if key == "supply-chain-graph-tabs":
        return "tabs"
    if key.endswith(":seed-version"):
        return "seed_version"
    if key.endswith("-canvas"):
        return "canvas"
    if (
        key.startswith("indus-dashboard-process-definition-")
        or key == "indus-dashboard-corrective-actions"
    ):
        return "canvas"
    return "state"


def _item_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    value = item.get("id") or item.get("nodeId") or item.get("key")
    if value is None and isinstance(item.get("data"), dict):
        value = item["data"].get("id")
    return str(value) if value is not None else None


def _edge_endpoints(item: dict) -> tuple[str | None, str | None]:
    source = (
        item.get("source")
        or item.get("source_node_id")
        or item.get("sourceNodeId")
        or item.get("sourceId")
        or item.get("from")
        or item.get("fromId")
        or item.get("start")
    )
    target = (
        item.get("target")
        or item.get("target_node_id")
        or item.get("targetNodeId")
        or item.get("targetId")
        or item.get("to")
        or item.get("toId")
        or item.get("end")
    )
    return (
        str(source) if source is not None else None,
        str(target) if target is not None else None,
    )


def _canvas_node_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    value = item.get("node_id") or item.get("nodeId") or item.get("id")
    return str(value) if value is not None else None


def _canvas_endpoint_name(node_id: str) -> str:
    return node_id.split(":")[-1]


def _canvas_endpoint_type(node_id: str) -> str:
    parts = [part for part in node_id.split(":") if part]
    if len(parts) < 2:
        return "record"
    return _singular(parts[-2])


def _canvas_entity_ref(node_id: str) -> tuple[str, str, str] | None:
    parts = [part for part in node_id.split(":") if part]
    if len(parts) < 2:
        return None
    segment = parts[-2]
    entity_id = parts[-1]
    mapped = CANVAS_MONGO_ENTITY_MAP.get(segment)
    if mapped:
        collection, id_field = mapped
        return collection, id_field, entity_id
    node_type = _singular(segment)
    return segment, f"{node_type}_id", entity_id


def _canvas_edge_id(item: dict) -> str:
    source, target = _edge_endpoints(item)
    return str(
        item.get("edge_id")
        or item.get("id")
        or item.get("linkId")
        or f"{source}->{target}"
    )


def _canvas_nodes(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}

    nodes: dict[str, dict[str, Any]] = {}
    positions = value.get("positions")
    if not isinstance(positions, dict) and isinstance(value.get("data"), dict):
        positions = value["data"].get("positions")
    if isinstance(positions, dict):
        for node_id, position in positions.items():
            nodes[str(node_id)] = {"position": position}

    for item in value.get("nodes", []) if isinstance(value.get("nodes"), list) else []:
        node_id = _canvas_node_id(item)
        if not node_id:
            continue
        nodes.setdefault(node_id, {})
        nodes[node_id]["node"] = item
        nodes[node_id].setdefault("position", {"x": item.get("x"), "y": item.get("y")})

    snapshots = value.get("node_snapshots")
    data = value.get("data") if isinstance(value.get("data"), dict) else {}
    if not isinstance(snapshots, list) and isinstance(data, dict):
        snapshots = data.get("node_snapshots")
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            node_id = _canvas_node_id(snapshot)
            if not node_id:
                continue
            nodes.setdefault(node_id, {})
            nodes[node_id]["snapshot"] = snapshot
            nodes[node_id].setdefault("position", snapshot.get("position"))

    return nodes


def _canvas_links(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []

    links = []
    for key in ("edges", "links", "connections"):
        items = value.get(key)
        if isinstance(items, list):
            links.extend(item for item in items if isinstance(item, dict))

    data = value.get("data") if isinstance(value.get("data"), dict) else {}
    if isinstance(data, dict):
        for key in ("links", "edges", "connections"):
            items = data.get(key)
            if isinstance(items, list):
                links.extend(item for item in items if isinstance(item, dict))

    deduped = {}
    for item in links:
        deduped[_canvas_edge_id(item)] = item
    return list(deduped.values())


async def _hydrate_canvas_nodes(nodes: dict[str, dict[str, Any]]) -> None:
    if not nodes:
        return
    try:
        from services.common.db import get_database

        db = get_database()
    except Exception:
        db = None
    if db is None:
        return

    for node_id, node_data in nodes.items():
        ref = _canvas_entity_ref(node_id)
        if not ref:
            continue
        collection, id_field, entity_id = ref
        try:
            doc = await db[collection].find_one(
                {
                    "$or": [
                        {id_field: entity_id},
                        {"id": entity_id},
                        {"group_id": entity_id},
                    ]
                }
            )
        except Exception:
            continue
        if doc:
            doc = dict(doc)
            doc.pop("_id", None)
            node_data["payload"] = doc


def _iter_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for nested_key in ("nodes", "links", "edges", "rows", "items"):
            nested = value.get(nested_key)
            if isinstance(nested, list):
                return nested
        return [
            (
                {"id": key, **item}
                if isinstance(item, dict)
                else {"id": key, "value": item}
            )
            for key, item in value.items()
        ]
    return []


def _graph_state_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, default=str, sort_keys=sort_keys)


async def save_graph_state(user_id: str, key: str, value: Any) -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    graph_id = graph_id_for_key(key)
    state_type = state_type_for_key(key)
    value_json = _graph_state_json(value)
    updated_at = datetime.now(timezone.utc).isoformat()
    driver = _get_driver()

    try:
        async with driver.session() as session:
            await session.execute_write(
                _save_state_tx,
                user_id,
                graph_id,
                key,
                state_type,
                value_json,
                updated_at,
            )
            if state_type in {"visual_nodes", "rows", "positions"}:
                await session.execute_write(
                    _sync_nodes_tx, user_id, graph_id, state_type, value, updated_at
                )
            elif state_type == "links":
                links = _canvas_links(value)
                existing_node_ids = await session.execute_read(
                    _existing_canvas_node_ids_tx, user_id, graph_id
                )
                endpoint_nodes: dict[str, dict[str, Any]] = {
                    node_id: {} for node_id in existing_node_ids
                }
                await session.execute_write(
                    _sync_links_tx,
                    user_id,
                    graph_id,
                    value,
                    updated_at,
                    set(existing_node_ids),
                )
                await session.execute_write(
                    _sync_canvas_links_tx,
                    user_id,
                    graph_id,
                    links,
                    endpoint_nodes,
                    updated_at,
                )
            elif state_type == "canvas":
                nodes = _canvas_nodes(value)
                links = _canvas_links(value)
                await _hydrate_canvas_nodes(nodes)
                await session.execute_write(
                    _sync_canvas_tx, user_id, graph_id, nodes, links, updated_at
                )
            await cleanup_neo4j_bloat()
    except HTTPException:
        raise
    except Exception as exc:
        await _raise_neo4j_http_error(exc)


async def get_graph_state(user_id: str, key: str) -> str | None:
    driver = _get_driver()
    try:
        async with driver.session() as session:
            result = await session.execute_read(_get_state_tx, user_id, key)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        await _raise_neo4j_http_error(exc)


async def delete_graph_state(user_id: str, key: str) -> bool:
    driver = _get_driver()
    try:
        async with driver.session() as session:
            return await session.execute_write(_delete_state_tx, user_id, key)
    except HTTPException:
        raise
    except Exception as exc:
        await _raise_neo4j_http_error(exc)


async def delete_canvas_node(canvas_node_id: str) -> bool:
    driver = _get_driver()
    try:
        async with driver.session() as session:
            deleted = await session.execute_write(
                _delete_canvas_node_tx, canvas_node_id
            )
            if deleted:
                await session.execute_write(
                    _remove_canvas_node_from_states_tx, canvas_node_id
                )
        await cleanup_neo4j_bloat()
        return deleted
    except HTTPException:
        raise
    except Exception as exc:
        await _raise_neo4j_http_error(exc)


async def _save_state_tx(
    tx,
    user_id: str,
    graph_id: str,
    key: str,
    state_type: str,
    value_json: str,
    updated_at: str,
) -> None:
    await tx.run(
        """
        MERGE (u:User {id: $user_id})
        MERGE (g:Graph {user_id: $user_id, graph_id: $graph_id})
        SET g.updated_at = $updated_at
        MERGE (u)-[:OWNS_GRAPH]->(g)
        MERGE (s:GraphState {user_id: $user_id, key: $key})
        SET s.graph_id = $graph_id,
            s.state_type = $state_type,
            s.value_json = $value_json,
            s.updated_at = $updated_at
        MERGE (g)-[:HAS_STATE]->(s)
        """,
        user_id=user_id,
        graph_id=graph_id,
        key=key,
        state_type=state_type,
        value_json=value_json,
        updated_at=updated_at,
    )


async def _get_state_tx(tx, user_id: str, key: str) -> str | None:
    result = await tx.run(
        "MATCH (s:GraphState {user_id: $user_id, key: $key}) RETURN s.value_json AS value_json",
        user_id=user_id,
        key=key,
    )
    record = await result.single()
    return record["value_json"] if record else None


async def _delete_state_tx(tx, user_id: str, key: str) -> bool:
    result = await tx.run(
        """
        MATCH (s:GraphState {user_id: $user_id, key: $key})
        WITH collect(s) AS states, count(s) AS deleted
        FOREACH (state IN states | DETACH DELETE state)
        RETURN deleted
        """,
        user_id=user_id,
        key=key,
    )
    record = await result.single()
    return bool(record and record["deleted"])


async def _delete_canvas_node_tx(tx, canvas_node_id: str) -> bool:
    result = await tx.run(
        """
        MATCH (n)
        WHERE (
            coalesce(n.source, "frontend_canvas") = "frontend_canvas"
            OR coalesce(n.collection, "") STARTS WITH "canvas_"
          )
          AND (
            n.canvas_node_id = $canvas_node_id
            OR n.node_id = $canvas_node_id
            OR n.id = $canvas_node_id
            OR n.canvas_node_id ENDS WITH ":" + $canvas_node_id
          )
        WITH collect(n) AS nodes, count(n) AS deleted
        FOREACH (node IN nodes | DETACH DELETE node)
        RETURN deleted
        """,
        canvas_node_id=canvas_node_id,
    )
    record = await result.single()
    return bool(record and record["deleted"])


async def _remove_canvas_node_from_states_tx(tx, canvas_node_id: str) -> None:
    result = await tx.run(
        """
        MATCH (s:GraphState)
        WHERE s.value_json CONTAINS $canvas_node_id
        RETURN s.user_id AS user_id, s.key AS key, s.value_json AS value_json
        """,
        canvas_node_id=canvas_node_id,
    )
    records = await result.data()
    for record in records:
        cleaned = _remove_canvas_node_from_value(
            json.loads(record["value_json"]),
            canvas_node_id,
        )
        await tx.run(
            """
            MATCH (s:GraphState {user_id: $user_id, key: $key})
            SET s.value_json = $value_json,
                s.updated_at = $updated_at
            """,
            user_id=record["user_id"],
            key=record["key"],
            value_json=_graph_state_json(cleaned),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )


def _references_canvas_node(value: Any, canvas_node_id: str) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value == canvas_node_id or value.endswith(f":{canvas_node_id}")
    if isinstance(value, dict):
        return any(
            _references_canvas_node(item, canvas_node_id) for item in value.values()
        )
    if isinstance(value, list):
        return any(_references_canvas_node(item, canvas_node_id) for item in value)
    return str(value) == canvas_node_id


def _remove_canvas_node_from_value(value: Any, canvas_node_id: str) -> Any:
    if isinstance(value, list):
        cleaned = []
        for item in value:
            if isinstance(item, dict):
                item_id = _canvas_node_id(item) or _item_id(item)
                source, target = _edge_endpoints(item)
                if (
                    _references_canvas_node(item_id, canvas_node_id)
                    or _references_canvas_node(source, canvas_node_id)
                    or _references_canvas_node(target, canvas_node_id)
                ):
                    continue
            cleaned.append(_remove_canvas_node_from_value(item, canvas_node_id))
        return cleaned

    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if _references_canvas_node(key, canvas_node_id):
            continue
        if key in {"nodes", "links", "edges", "items", "rows"} and isinstance(
            item, list
        ):
            cleaned[key] = _remove_canvas_node_from_value(item, canvas_node_id)
            continue
        if key == "positions" and isinstance(item, dict):
            cleaned[key] = {
                position_key: position
                for position_key, position in item.items()
                if not _references_canvas_node(position_key, canvas_node_id)
            }
            continue
        cleaned[key] = _remove_canvas_node_from_value(item, canvas_node_id)
    return cleaned


async def _sync_nodes_tx(
    tx, user_id: str, graph_id: str, state_type: str, value: Any, updated_at: str
) -> None:
    if state_type == "positions" and isinstance(value, dict):
        for node_id, position in value.items():
            await tx.run(
                """
                MERGE (n:GraphNode {user_id: $user_id, graph_id: $graph_id, node_id: $node_id})
                SET n.position_json = $position_json,
                    n.updated_at = $updated_at
                """,
                user_id=user_id,
                graph_id=graph_id,
                node_id=str(node_id),
                position_json=_graph_state_json(position),
                updated_at=updated_at,
            )
        return

    for item in _iter_items(value):
        node_id = _item_id(item)
        if not node_id:
            continue
        await tx.run(
            """
            MERGE (g:Graph {user_id: $user_id, graph_id: $graph_id})
            MERGE (n:GraphNode {user_id: $user_id, graph_id: $graph_id, node_id: $node_id})
            SET n.payload_json = $payload_json,
                n.updated_at = $updated_at
            MERGE (g)-[:HAS_NODE]->(n)
            """,
            user_id=user_id,
            graph_id=graph_id,
            node_id=node_id,
            payload_json=_graph_state_json(item),
            updated_at=updated_at,
        )


async def _sync_links_tx(
    tx,
    user_id: str,
    graph_id: str,
    value: Any,
    updated_at: str,
    valid_node_ids: set[str] | None = None,
) -> None:
    await tx.run(
        "MATCH ()-[r:GRAPH_LINK {user_id: $user_id, graph_id: $graph_id}]->() DELETE r",
        user_id=user_id,
        graph_id=graph_id,
    )
    for item in _iter_items(value):
        if not isinstance(item, dict):
            continue
        source, target = _edge_endpoints(item)
        if not source or not target:
            continue
        if valid_node_ids is not None and (
            source not in valid_node_ids or target not in valid_node_ids
        ):
            continue
        link_id = str(item.get("id") or item.get("linkId") or f"{source}->{target}")
        await tx.run(
            """
            MERGE (g:Graph {user_id: $user_id, graph_id: $graph_id})
            MERGE (source:GraphNode {user_id: $user_id, graph_id: $graph_id, node_id: $source})
            MERGE (target:GraphNode {user_id: $user_id, graph_id: $graph_id, node_id: $target})
            MERGE (g)-[:HAS_NODE]->(source)
            MERGE (g)-[:HAS_NODE]->(target)
            MERGE (source)-[r:GRAPH_LINK {user_id: $user_id, graph_id: $graph_id, link_id: $link_id}]->(target)
            SET r.payload_json = $payload_json,
                r.updated_at = $updated_at
            """,
            user_id=user_id,
            graph_id=graph_id,
            source=source,
            target=target,
            link_id=link_id,
            payload_json=_graph_state_json(item),
            updated_at=updated_at,
        )


async def _sync_canvas_tx(
    tx,
    user_id: str,
    graph_id: str,
    nodes: dict[str, dict[str, Any]],
    links: list[dict],
    updated_at: str,
) -> None:
    await _prune_canvas_nodes_tx(tx, user_id, graph_id, sorted(nodes))

    for node_id, node_data in nodes.items():
        await _upsert_canvas_node_tx(
            tx, user_id, graph_id, node_id, node_data, updated_at
        )
    await _sync_canvas_links_tx(tx, user_id, graph_id, links, nodes, updated_at)


async def _existing_canvas_node_ids_tx(tx, user_id: str, graph_id: str) -> list[str]:
    result = await tx.run(
        """
        MATCH (n)
        WHERE n.canvas_node_id IS NOT NULL
          AND coalesce(n.source, 'frontend_canvas') = 'frontend_canvas'
          AND n.user_id = $user_id
          AND n.graph_id = $graph_id
        RETURN collect(DISTINCT n.canvas_node_id) AS node_ids
        """,
        user_id=user_id,
        graph_id=graph_id,
    )
    record = await result.single()
    return list(record["node_ids"] or []) if record else []


async def _prune_canvas_nodes_tx(
    tx, user_id: str, graph_id: str, current_node_ids: list[str]
) -> None:
    await tx.run(
        """
        MATCH (n)
        WHERE n.canvas_node_id IS NOT NULL
          AND coalesce(n.source, 'frontend_canvas') = 'frontend_canvas'
          AND n.user_id = $user_id
          AND (
            n.graph_id = $graph_id
            OR n.graph_id IS NULL
          )
          AND NOT n.canvas_node_id IN $current_node_ids
        DETACH DELETE n
        """,
        user_id=user_id,
        graph_id=graph_id,
        current_node_ids=current_node_ids,
    )


async def _sync_canvas_links_tx(
    tx,
    user_id: str,
    graph_id: str,
    links: list[dict],
    endpoint_nodes: dict[str, dict[str, Any]],
    updated_at: str,
) -> None:
    await tx.run(
        """
        MATCH ()-[r]->()
        WHERE r.user_id = $user_id
          AND r.graph_id = $graph_id
          AND r.edge_id IS NOT NULL
        DELETE r
        """,
        user_id=user_id,
        graph_id=graph_id,
    )

    for item in links:
        source, target = _edge_endpoints(item)
        if not source or not target:
            continue
        if source not in endpoint_nodes or target not in endpoint_nodes:
            continue
        await _upsert_canvas_node_tx(
            tx, user_id, graph_id, source, endpoint_nodes.get(source, {}), updated_at
        )
        await _upsert_canvas_node_tx(
            tx, user_id, graph_id, target, endpoint_nodes.get(target, {}), updated_at
        )
        await _upsert_canvas_link_tx(
            tx, user_id, graph_id, item, source, target, updated_at
        )


async def _upsert_canvas_node_tx(
    tx,
    user_id: str,
    graph_id: str,
    node_id: str,
    node_data: dict[str, Any],
    updated_at: str,
) -> None:
    name = _canvas_endpoint_name(node_id)
    node_type = _canvas_endpoint_type(node_id)
    label = _neo4j_label(node_type)
    snapshot = node_data.get("snapshot") or node_data.get("node") or {}
    payload = node_data.get("payload")
    if payload is None and isinstance(node_data.get("snapshot"), dict):
        payload = node_data["snapshot"].get("row") or node_data["snapshot"]
    if payload is None and isinstance(node_data.get("node"), dict):
        payload = node_data["node"].get("data") or node_data["node"]
    if payload is None:
        payload = {
            "canvas_node_id": node_id,
            "entity_id": name,
            "type": node_type,
            "name": name,
        }
    display_name = name
    if isinstance(payload, dict) and payload.get("name"):
        display_name = str(payload["name"])
    await tx.run(
        f"""
        MERGE (n:{label} {{entity_id: $entity_id, type: $node_type}})
        SET n.name = CASE
                WHEN $name = $entity_id AND n.name IS NOT NULL AND n.name <> n.entity_id THEN n.name
                ELSE $name
            END,
            n.user_id = $user_id,
            n.graph_id = $graph_id,
            n.collection = coalesce(n.collection, $collection),
            n.canvas_node_id = $node_id,
            n.source = coalesce(n.source, 'frontend_canvas'),
            n.payload_json = $payload_json,
            n.canvas_position_json = $position_json,
            n.canvas_snapshot_json = $snapshot_json,
            n.updated_at = $updated_at
        """,
        entity_id=name,
        node_type=node_type,
        name=display_name,
        collection=f"canvas_{node_type}",
        node_id=node_id,
        user_id=user_id,
        graph_id=graph_id,
        payload_json=_graph_state_json(payload or {}, sort_keys=True),
        position_json=_graph_state_json(node_data.get("position"), sort_keys=True),
        snapshot_json=_graph_state_json(snapshot, sort_keys=True),
        updated_at=updated_at,
    )


async def _upsert_canvas_link_tx(
    tx,
    user_id: str,
    graph_id: str,
    item: dict,
    source_node_id: str,
    target_node_id: str,
    updated_at: str,
) -> None:
    source_name = _canvas_endpoint_name(source_node_id)
    source_type = _canvas_endpoint_type(source_node_id)
    target_name = _canvas_endpoint_name(target_node_id)
    target_type = _canvas_endpoint_type(target_node_id)
    link_id = _canvas_edge_id(item)
    await tx.run(
        """
        MATCH (source {canvas_node_id: $source_node_id, graph_id: $graph_id})
        MATCH (target {canvas_node_id: $target_node_id, graph_id: $graph_id})
        MERGE (source)-[r:CANVAS_LINK {edge_id: $link_id}]->(target)
        SET r.source_node_id = $source_node_id,
            r.target_node_id = $target_node_id,
            r.user_id = $user_id,
            r.graph_id = $graph_id,
            r.name = coalesce($name, $label, $link_id),
            r.label = coalesce($label, $name, $link_id),
            r.source_handle = $source_handle,
            r.target_handle = $target_handle,
            r.flow_color = $flow_color,
            r.flow_enabled = $flow_enabled,
            r.route_mode = $route_mode,
            r.sourceHandle = $source_handle,
            r.targetHandle = $target_handle,
            r.flowColor = $flow_color,
            r.flowEnabled = $flow_enabled,
            r.arrowHead = $arrow_head,
            r.routeMode = $route_mode,
            r.points = $points,
            r.payload_json = $payload_json,
            r.updated_at = $updated_at
        """,
        user_id=user_id,
        graph_id=graph_id,
        link_id=link_id,
        source_name=source_name,
        source_type=source_type,
        target_name=target_name,
        target_type=target_type,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        name=item.get("name"),
        label=item.get("label"),
        source_handle=item.get("sourceHandle"),
        target_handle=item.get("targetHandle"),
        flow_color=item.get("flowColor"),
        flow_enabled=item.get("flowEnabled"),
        arrow_head=item.get("arrowHead"),
        route_mode=item.get("routeMode"),
        points=_graph_state_json(item.get("points", []), sort_keys=True),
        payload_json=_graph_state_json(item, sort_keys=True),
        updated_at=updated_at,
    )
