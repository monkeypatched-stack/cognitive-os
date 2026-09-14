"""OQL QueryCompiler — compiles OQL queries into native database queries.

Business agents never see this translation.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.oql.query import Query, Operation, SortDirection

logger = logging.getLogger("agentos.oql.compiler")


class NativeQuery:
    """Compiled native database query."""

    def __init__(self, database: str, query_type: str, query: Any, params: dict | None = None):
        self.database = database
        self.query_type = query_type
        self.query = query
        self.params = params or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "query_type": self.query_type,
            "query": self.query,
            "params": self.params,
        }


class QueryCompiler:
    """Compiles OQL queries into native database queries."""

    def compile(self, query: Query, database: str) -> NativeQuery:
        """Compile an OQL query into a native database query."""
        if database in ("postgresql", "mysql", "sqlite"):
            return self._compile_sql(query, database)
        elif database == "mongodb":
            return self._compile_mongo(query)
        elif database == "neo4j":
            return self._compile_cypher(query)
        elif database == "redis":
            return self._compile_redis(query)
        elif database == "influxdb":
            return self._compile_influx(query)
        elif database == "elasticsearch":
            return self._compile_elasticsearch(query)
        else:
            return self._compile_generic(query, database)

    def _compile_sql(self, query: Query, database: str) -> NativeQuery:
        """Compile OQL to SQL."""
        table = query.entity.lower() + "s"
        op = query.operation

        if op == Operation.SELECT:
            fields = ", ".join(query.fields) if query.fields else "*"
            sql = f"SELECT {fields} FROM {table}"
            params = {}

            where_parts = []
            for key, value in query.filters.items():
                if isinstance(value, list):
                    placeholders = ", ".join(f":{key}_{i}" for i in range(len(value)))
                    where_parts.append(f"{key} IN ({placeholders})")
                    for i, v in enumerate(value):
                        params[f"{key}_{i}"] = v
                elif isinstance(value, dict):
                    for op_name, op_value in value.items():
                        sql_op = {
                            "gt": ">",
                            "gte": ">=",
                            "lt": "<",
                            "lte": "<=",
                            "ne": "!=",
                            "like": "LIKE",
                            "in": "IN",
                        }.get(op_name, "=")
                        where_parts.append(f"{key} {sql_op} :{key}")
                        params[key] = op_value
                else:
                    where_parts.append(f"{key} = :{key}")
                    params[key] = value

            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)

            if query.sort:
                order_parts = []
                for s in query.sort:
                    dir_str = "ASC" if s.direction == SortDirection.ASC else "DESC"
                    order_parts.append(f"{s.field} {dir_str}")
                sql += " ORDER BY " + ", ".join(order_parts)

            if query.limit is not None:
                sql += f" LIMIT {query.limit}"
            if query.offset > 0:
                sql += f" OFFSET {query.offset}"

            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.INSERT:
            fields = list(query.data.keys()) if query.data else []
            placeholders = ", ".join(f":{f}" for f in fields)
            sql = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})"
            params = {f: query.data.get(f) for f in fields}
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.UPDATE:
            set_parts = []
            params = {}
            for key, value in (query.data or {}).items():
                set_parts.append(f"{key} = :{key}")
                params[key] = value

            where_parts = []
            for key, value in query.filters.items():
                where_parts.append(f"{key} = :filter_{key}")
                params[f"filter_{key}"] = value

            sql = f"UPDATE {table} SET {', '.join(set_parts)}"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.DELETE:
            where_parts = []
            params = {}
            for key, value in query.filters.items():
                where_parts.append(f"{key} = :{key}")
                params[key] = value

            sql = f"DELETE FROM {table}"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.UPSERT:
            fields = list(query.data.keys()) if query.data else []
            placeholders = ", ".join(f":{f}" for f in fields)
            key_field = query.upsert_key or fields[0] if fields else "id"
            sql = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders}) ON CONFLICT ({key_field}) DO UPDATE SET {', '.join(f'{f} = EXCLUDED.{f}' for f in fields)}"
            params = {f: query.data.get(f) for f in fields}
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.COUNT:
            sql = f"SELECT COUNT(*) as count FROM {table}"
            params = {}
            where_parts = []
            for key, value in query.filters.items():
                where_parts.append(f"{key} = :{key}")
                params[key] = value
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op == Operation.EXISTS:
            sql = f"SELECT 1 FROM {table} WHERE 1=1"
            params = {}
            for key, value in query.filters.items():
                sql += f" AND {key} = :{key}"
                params[key] = value
            sql += " LIMIT 1"
            return NativeQuery(database=database, query_type="sql", query=sql, params=params)

        elif op in (
            Operation.BATCH_INSERT,
            Operation.BATCH_UPDATE,
            Operation.BATCH_DELETE,
        ):
            return self._compile_batch_sql(query, database)

        return NativeQuery(
            database=database,
            query_type="sql",
            query=f"SELECT 1 FROM {table}",
            params={},
        )

    def _compile_batch_sql(self, query: Query, database: str) -> NativeQuery:
        """Compile batch operations to SQL."""
        table = query.entity.lower() + "s"
        batch = query.batch_data or []

        if query.operation == Operation.BATCH_INSERT and batch:
            fields = list(batch[0].keys())
            placeholders = ", ".join(f":{f}" for f in fields)
            sql = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})"
            return NativeQuery(
                database=database,
                query_type="sql_batch",
                query=sql,
                params={"batch": batch},
            )

        elif query.operation == Operation.BATCH_UPDATE and batch:
            return NativeQuery(
                database=database,
                query_type="sql_batch",
                query=f"UPDATE {table}",
                params={"batch": batch},
            )

        elif query.operation == Operation.BATCH_DELETE and batch:
            return NativeQuery(
                database=database,
                query_type="sql_batch",
                query=f"DELETE FROM {table}",
                params={"batch": batch},
            )

        return NativeQuery(
            database=database,
            query_type="sql",
            query=f"SELECT 1 FROM {table}",
            params={},
        )

    def _compile_mongo(self, query: Query) -> NativeQuery:
        """Compile OQL to MongoDB query."""
        collection = query.entity.lower() + "s"
        op = query.operation

        if op == Operation.SELECT:
            projection = {f: 1 for f in query.fields} if query.fields else None
            sort_list = [(s.field, 1 if s.direction == SortDirection.ASC else -1) for s in query.sort]

            mongo_query = {
                "collection": collection,
                "operation": "find",
                "filter": query.filters,
                "projection": projection,
                "sort": sort_list,
                "limit": query.limit or 0,
                "skip": query.offset,
            }
            return NativeQuery(database="mongodb", query_type="mongo", query=mongo_query)

        elif op == Operation.INSERT:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "insert_one",
                    "document": query.data or {},
                },
            )

        elif op == Operation.UPDATE:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "update_one",
                    "filter": query.filters,
                    "update": {"$set": query.data or {}},
                },
            )

        elif op == Operation.DELETE:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "delete_one",
                    "filter": query.filters,
                },
            )

        elif op == Operation.UPSERT:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "update_one",
                    "filter": query.filters,
                    "update": {"$set": query.data or {}},
                    "upsert": True,
                },
            )

        elif op == Operation.COUNT:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "count_documents",
                    "filter": query.filters,
                },
            )

        elif op == Operation.EXISTS:
            return NativeQuery(
                database="mongodb",
                query_type="mongo",
                query={
                    "collection": collection,
                    "operation": "count_documents",
                    "filter": query.filters,
                    "limit": 1,
                },
            )

        return NativeQuery(
            database="mongodb",
            query_type="mongo",
            query={"collection": collection, "operation": "find"},
        )

    def _compile_cypher(self, query: Query) -> NativeQuery:
        """Compile OQL to Cypher query."""
        label = query.entity
        op = query.operation

        if op == Operation.SELECT:
            fields = ", ".join(f"n.{f}" for f in query.fields) if query.fields else "n"
            cypher = f"MATCH (n:{label})"
            params = {}

            where_parts = []
            for key, value in query.filters.items():
                where_parts.append(f"n.{key} = ${key}")
                params[key] = value

            if where_parts:
                cypher += " WHERE " + " AND ".join(where_parts)

            cypher += f" RETURN {fields}"

            if query.sort:
                order_parts = []
                for s in query.sort:
                    dir_str = "ASC" if s.direction == SortDirection.ASC else "DESC"
                    order_parts.append(f"n.{s.field} {dir_str}")
                cypher += " ORDER BY " + ", ".join(order_parts)

            if query.limit is not None:
                cypher += f" LIMIT {query.limit}"

            return NativeQuery(database="neo4j", query_type="cypher", query=cypher, params=params)

        elif op == Operation.INSERT:
            props = ", ".join(f"{k}: ${k}" for k in (query.data or {}).keys())
            cypher = f"CREATE (n:{label} {{{props}}})"
            params = dict(query.data or {})
            return NativeQuery(database="neo4j", query_type="cypher", query=cypher, params=params)

        elif op == Operation.UPDATE:
            set_parts = []
            params = {}
            for key, value in (query.data or {}).items():
                set_parts.append(f"n.{key} = ${key}")
                params[key] = value

            where_parts = []
            for key, value in query.filters.items():
                where_parts.append(f"n.{key} = ${key}")
                params[key] = value

            cypher = f"MATCH (n:{label})"
            if where_parts:
                cypher += " WHERE " + " AND ".join(where_parts)
            cypher += f" SET {', '.join(set_parts)}"
            return NativeQuery(database="neo4j", query_type="cypher", query=cypher, params=params)

        elif op == Operation.DELETE:
            where_parts = []
            params = {}
            for key, value in query.filters.items():
                where_parts.append(f"n.{key} = ${key}")
                params[key] = value

            cypher = f"MATCH (n:{label})"
            if where_parts:
                cypher += " WHERE " + " AND ".join(where_parts)
            cypher += " DELETE n"
            return NativeQuery(database="neo4j", query_type="cypher", query=cypher, params=params)

        elif op == Operation.COUNT:
            where_parts = []
            params = {}
            for key, value in query.filters.items():
                where_parts.append(f"n.{key} = ${key}")
                params[key] = value

            cypher = f"MATCH (n:{label})"
            if where_parts:
                cypher += " WHERE " + " AND ".join(where_parts)
            cypher += " RETURN count(n) as count"
            return NativeQuery(database="neo4j", query_type="cypher", query=cypher, params=params)

        return NativeQuery(
            database="neo4j",
            query_type="cypher",
            query=f"MATCH (n:{label}) RETURN n",
            params={},
        )

    def _compile_redis(self, query: Query) -> NativeQuery:
        """Compile OQL to Redis operations."""
        prefix = query.entity.lower()
        op = query.operation

        if op == Operation.SELECT:
            if query.filters.get("id"):
                key = f"{prefix}:{query.filters['id']}"
                return NativeQuery(
                    database="redis",
                    query_type="redis",
                    query={"command": "GET", "key": key},
                )
            pattern = f"{prefix}:*"
            return NativeQuery(
                database="redis",
                query_type="redis",
                query={
                    "command": "SCAN",
                    "pattern": pattern,
                    "count": query.limit or 100,
                },
            )

        elif op == Operation.INSERT:
            entity_id = (query.data or {}).get("id", "new")
            key = f"{prefix}:{entity_id}"
            return NativeQuery(
                database="redis",
                query_type="redis",
                query={"command": "SET", "key": key, "value": query.data},
            )

        elif op == Operation.UPDATE:
            entity_id = query.filters.get("id", "unknown")
            key = f"{prefix}:{entity_id}"
            return NativeQuery(
                database="redis",
                query_type="redis",
                query={"command": "SET", "key": key, "value": query.data},
            )

        elif op == Operation.DELETE:
            entity_id = query.filters.get("id", "unknown")
            key = f"{prefix}:{entity_id}"
            return NativeQuery(
                database="redis",
                query_type="redis",
                query={"command": "DELETE", "key": key},
            )

        return NativeQuery(
            database="redis",
            query_type="redis",
            query={"command": "KEYS", "pattern": f"{prefix}:*"},
        )

    def _compile_influx(self, query: Query) -> NativeQuery:
        """Compile OQL to InfluxDB query."""
        measurement = query.entity.lower()
        op = query.operation

        if op == Operation.SELECT:
            flux = (
                f'from(bucket: "default") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "{measurement}")'
            )

            if query.filters.get("start"):
                flux = f'from(bucket: "default") |> range(start: {query.filters["start"]}) |> filter(fn: (r) => r._measurement == "{measurement}")'

            if query.fields:
                field_filters = " or ".join(f'r._field == "{f}"' for f in query.fields)
                flux += f" |> filter(fn: (r) => {field_filters})"

            if query.limit:
                flux += f" |> limit(n: {query.limit})"

            return NativeQuery(database="influxdb", query_type="flux", query=flux)

        elif op == Operation.INSERT:
            return NativeQuery(
                database="influxdb",
                query_type="flux",
                query=f'from(bucket: "default") |> to(bucket: "default")',
                params={"measurement": measurement, "data": query.data},
            )

        return NativeQuery(
            database="influxdb",
            query_type="flux",
            query=f'from(bucket: "default") |> filter(fn: (r) => r._measurement == "{measurement}")',
        )

    def _compile_elasticsearch(self, query: Query) -> NativeQuery:
        """Compile OQL to Elasticsearch query."""
        index = query.entity.lower()
        op = query.operation

        if op == Operation.SELECT:
            es_query = {"query": {"bool": {"must": []}}}

            for key, value in query.filters.items():
                if isinstance(value, str):
                    es_query["query"]["bool"]["must"].append({"match": {key: value}})
                elif isinstance(value, dict):
                    for op_name, op_value in value.items():
                        range_op = {
                            "gt": "gt",
                            "gte": "gte",
                            "lt": "lt",
                            "lte": "lte",
                        }.get(op_name, "term")
                        es_query["query"]["bool"]["must"].append({"range": {key: {range_op: op_value}}})
                else:
                    es_query["query"]["bool"]["must"].append({"term": {key: value}})

            if not es_query["query"]["bool"]["must"]:
                es_query["query"] = {"match_all": {}}

            if query.fields:
                es_query["_source"] = query.fields

            if query.sort:
                es_query["sort"] = [{s.field: {"order": s.direction.value}} for s in query.sort]

            if query.limit:
                es_query["size"] = query.limit
            if query.offset:
                es_query["from"] = query.offset

            return NativeQuery(
                database="elasticsearch",
                query_type="elasticsearch",
                query={"index": index, "body": es_query},
            )

        elif op == Operation.COUNT:
            if query.filters:
                must = [{"term": {k: v}} for k, v in query.filters.items()]
                body = {"query": {"bool": {"must": must}}}
            else:
                body = {"query": {"match_all": {}}}
            return NativeQuery(
                database="elasticsearch",
                query_type="elasticsearch",
                query={"index": index, "body": body},
            )

        return NativeQuery(
            database="elasticsearch",
            query_type="elasticsearch",
            query={"index": index, "body": {"query": {"match_all": {}}}},
        )

    def _compile_generic(self, query: Query, database: str) -> NativeQuery:
        """Generic fallback compilation."""
        return NativeQuery(database=database, query_type="generic", query=query.to_dict())
