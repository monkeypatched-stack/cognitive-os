"""Read-only data access for the manufacturing domain agents.

The domain data lives in Mongo `demo` — 41 instruments, 72 pharmaceutical_equipment,
54 workstations, 100 plant_locations, across two real production lines (LINE-TAB-001,
LINE-CAP-001). Note this is NOT the database the API's persistence manager points at
(`DB_NAME`, default "agentos", which is empty) — the domain data was seeded separately, so
these agents address it explicitly.

Every query returns its provenance alongside its rows. That is what makes an answer built on
it *grounded*: `kernel.execute.grounding.extract_step_citations` reads the `sources` key off
an agent's payload, and an answer with no source behind it is marked ungrounded (and, under
MB_GROUNDING=strict, withheld). An agent that invents data therefore cannot cite anything,
and the system will say so.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("manufacturing.agents.data")

DB_ENV = "MANUFACTURING_DB"
DEFAULT_DB = "demo"
URL_ENV = "MONGODB_URL"
DEFAULT_URL = "mongodb://localhost:27017"

# Collections these agents are allowed to read. An allow-list, not a convenience: these
# agents run LLM-planned steps, and the set of things they may read should not be open-ended.
READABLE = frozenset(
    {
        "instruments",
        "pharmaceutical_equipment",
        "workstations",
        "plant_locations",
        "calibration_records",
        "machine_parts",
        "pharmaceutical_machines",
    }
)

_client: Any = None


def db_name() -> str:
    return os.getenv(DB_ENV, DEFAULT_DB)


def source_uri(collection: str) -> str:
    """The provenance string an agent cites for a collection."""
    return f"mongo://{db_name()}/{collection}"


def _get_client() -> Any:
    global _client
    if _client is None:
        from pymongo import MongoClient

        _client = MongoClient(
            os.getenv(URL_ENV, DEFAULT_URL),
            serverSelectionTimeoutMS=4000,
            connectTimeoutMS=4000,
        )
    return _client


class CollectionAbsent(RuntimeError):
    """The collection this agent needs does not exist.

    Raised rather than returning [] so an agent cannot mistake "the data is not there" for
    "the answer is nothing" — the two are different, and only the second is an answer.
    """


def _find_sync(
    collection: str, query: dict, projection: dict | None, limit: int
) -> list[dict]:
    if collection not in READABLE:
        raise ValueError(
            f"collection {collection!r} is not in the manufacturing read allow-list"
        )
    database = _get_client()[db_name()]
    if collection not in database.list_collection_names():
        raise CollectionAbsent(f"{source_uri(collection)} does not exist")
    cursor = database[collection].find(query, projection or {"_id": 0}).limit(limit)
    return list(cursor)


async def find(
    collection: str,
    query: dict | None = None,
    projection: dict | None = None,
    limit: int = 200,
) -> list[dict]:
    """Read rows from an allow-listed collection. Blocking driver, so off the event loop."""
    return await asyncio.to_thread(
        _find_sync, collection, query or {}, projection, limit
    )


def _distinct_sync(collection: str, field: str) -> list[Any]:
    if collection not in READABLE:
        raise ValueError(
            f"collection {collection!r} is not in the manufacturing read allow-list"
        )
    database = _get_client()[db_name()]
    if collection not in database.list_collection_names():
        raise CollectionAbsent(f"{source_uri(collection)} does not exist")
    return [v for v in database[collection].distinct(field) if v]


async def distinct(collection: str, field: str) -> list[Any]:
    return await asyncio.to_thread(_distinct_sync, collection, field)


def cite(collection: str, count: int) -> dict[str, Any]:
    """One citation: what was read, and how much of it came back."""
    return {"source": source_uri(collection), "count": count}


class DataBackedAgentMixin:
    """Turns off BaseDDDAgent's per-step LLM call.

    BaseDDDAgent._impl compiles a workload spec and calls the model on EVERY handle(), to
    enrich `reason()` with model guidance. For a structural stub with nothing to reason about
    that is the only intelligence it has. For an agent whose job is "run this query and report
    what came back" it is pure cost — a model round-trip per step, and a model that cannot
    improve on the database's own answer. These agents are deterministic: the same question
    against the same data gives the same rows, and no LLM is consulted anywhere in the path.
    """

    async def _compile_spec(self, context: dict[str, Any]) -> str:  # noqa: D102
        return ""
