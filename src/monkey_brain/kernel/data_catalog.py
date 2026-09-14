"""Data catalog — what actually exists in the database, in a form an LLM can be handed.

When the resolver cannot find an agent it asks an LLM to build one. That LLM was told which
n8n node types it may use, and nothing whatsoever about the data. So it invented the data. For
`LineResolverAgent` it produced, and we activated, a workflow containing:

    const pumpData = $input.first().json.body;
    const lines = pumpData.lines; const pumpNumbers = pumpData.pumpNumbers;
    if (pumpNumbers[i] === '3') { ... }

`lines`, `pumpNumbers`, and the literal '3' are all fictions. The real database has
`instruments` and `pharmaceutical_equipment` keyed by `line_id`, on lines LINE-TAB-001 and
LINE-CAP-001. There is no line 3 and there are no pumps. The generated agent could not have
been right, because nothing in the prompt made being right possible.

This introspects the database and renders a compact schema summary — collections, field names,
and a few real identifier values — so a generator is at least building against the world as it
is. Cached: the shape of a database changes far more slowly than agents are generated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger("agentos.data_catalog")

DB_ENV = "MANUFACTURING_DB"
DEFAULT_DB = "demo"
URL_ENV = "MONGODB_URL"
DEFAULT_URL = "mongodb://localhost:27017"

TTL_SECONDS = 600
MAX_COLLECTIONS = 25
MAX_FIELDS = 14
MAX_SAMPLES = 3

# Fields worth showing real values for — an LLM that has seen "LINE-TAB-001" will not invent
# "line 3". Values of everything else are withheld: a catalog is a description of shape, and
# should not become an exfiltration channel for row contents.
_ID_FIELDS = (
    "line_id",
    "plant_id",
    "stage_id",
    "workstation_id",
    "category",
    "type",
    "status",
)

# The catalog goes into an LLM prompt, so it is an ALLOW-LIST, never "every collection in the
# database". The demo database also holds agentos_api_keys, agentos_api_key_email_otps
# (otp_hash), part11_login_events and users — describing those to a model, field by field,
# would be handing out a map of the credential store for no benefit to any agent. Domain data
# only: these are the collections an agent has a legitimate reason to reason about.
DOMAIN_COLLECTIONS = frozenset(
    {
        "instruments",
        "pharmaceutical_equipment",
        "pharmaceutical_machines",
        "machine_parts",
        "workstations",
        "plant_locations",
        "calibration_records",
        "work_orders",
        "production_batches",
        "sops",
        "checklists",
        "downtime",
        "devices",
        "scada_tags",
        "plc_controllers",
    }
)

_cache: tuple[float, str] | None = None


def _db_name() -> str:
    return os.getenv(DB_ENV, DEFAULT_DB)


def _introspect() -> str:
    from pymongo import MongoClient

    client = MongoClient(os.getenv(URL_ENV, DEFAULT_URL), serverSelectionTimeoutMS=3000)
    db = client[_db_name()]
    present = set(db.list_collection_names())
    names = sorted(DOMAIN_COLLECTIONS & present)[:MAX_COLLECTIONS]
    if not names:
        return ""

    lines = [f"Database `{_db_name()}` (MongoDB). Collections and their real fields:"]
    for name in names:
        collection = db[name]
        count = collection.estimated_document_count()
        if not count:
            continue
        sample = collection.find_one({}, {"_id": 0})
        if not sample:
            continue

        fields = list(sample.keys())[:MAX_FIELDS]
        lines.append(f"\n- {name} ({count} documents)")
        lines.append(f"    fields: {', '.join(fields)}")

        for field in _ID_FIELDS:
            if field not in sample:
                continue
            try:
                values = [str(v) for v in collection.distinct(field) if v][:MAX_SAMPLES]
            except Exception:
                continue
            if values:
                lines.append(f"    {field} values: {', '.join(values)}")

    return "\n".join(lines)


def _catalog_sync() -> str:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < TTL_SECONDS:
        return _cache[1]
    try:
        text = _introspect()
    except Exception as exc:
        logger.warning("[data_catalog] introspection failed: %s", exc)
        return ""
    _cache = (now, text)
    return text


async def schema_summary() -> str:
    """A compact description of the real data, or "" if the database can't be reached.

    Empty string is a meaningful answer: it means we cannot tell a generator what exists, and
    a generator that is told nothing must not be encouraged to guess (see prompt_fragment).
    """
    return await asyncio.to_thread(_catalog_sync)


async def prompt_fragment() -> str:
    """The catalog as a prompt block, with the rule that makes it useful.

    Handing over the schema is only half the fix. The other half is forbidding the behaviour
    that filled the vacuum: inventing collections, fields and identifiers that sound right.
    """
    summary = await schema_summary()
    if not summary:
        return (
            "\n\nDATA: the database could not be inspected, so you do not know what data "
            "exists. Do NOT invent collections, fields, or identifiers. Build the agent so "
            "that it reports that it has no data access, rather than returning fabricated "
            "records.\n"
        )
    return (
        "\n\nREAL DATA AVAILABLE — build against this, and only this:\n"
        f"{summary}\n\n"
        "Rules:\n"
        "- Use ONLY the collections, field names and identifier values shown above. Never "
        "invent a collection, a field, or an id. If the question refers to something that is "
        "not there (an asset type, a line, a plant), the correct behaviour is to report that "
        "it does not exist and list what does — not to produce a plausible-looking answer.\n"
        "- Whatever data the agent reports, it must also report where that data came from, as "
        "a `sources` list of {source, count}. An answer with no source is treated as "
        "ungrounded by the caller and may be withheld entirely.\n"
    )


def invalidate() -> None:
    global _cache
    _cache = None
