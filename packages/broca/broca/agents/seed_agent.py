"""SeedAgent — seeds the database with synthetic data after codegen and before serve.

Pipeline position: runs after service_gen (codegen), before serve.

Steps
-----
1. Discover Pydantic models in the generated service directory.
2. Extract field schemas from the model source (regex; no import required).
3. Ask the LLM to generate seed records that conform to each model.
4. Validate each record using the live Pydantic model when importable,
   or fall back to field-type checking when not.
5. Insert validated records into MongoDB via Motor.

The Pydantic model is the validator — never insert records that fail validation.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.seed")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))

# Number of seed records to generate per model
_SEED_COUNT = 5


def _to_collection_name(class_name: str) -> str:
    """WorkOrder → work_orders, Batch → batches."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", class_name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s).lower()
    # Simple pluralisation: append s, fix common irregular forms
    if s.endswith("y") and not s.endswith("ey"):
        s = s[:-1] + "ies"
    elif not s.endswith("s"):
        s = s + "s"
    return s


# Non-entity suffixes — application-layer DTOs, commands, events, etc. are not seeded
_SKIP_SUFFIXES = (
    "Command",
    "Query",
    "Handler",
    "Request",
    "Response",
    "Event",
    "Exception",
    "Error",
    "Config",
    "Settings",
    "Schema",
    "DTO",
    "Dto",
    "Base",
)

# Directories to skip — only domain/ holds seedable root entities
_SKIP_DIRS = {"application", "api", "infrastructure", "app", "apps"}

# Class names that are almost always embedded sub-documents, not root collection docs
_SUBDOC_NAMES = {
    "Attachment",
    "Address",
    "LineItem",
    "Metadata",
    "Tag",
    "Label",
    "Coordinate",
    "Location",
    "GeoPoint",
    "PhoneNumber",
    "Money",
    "Price",
    "Period",
    "DateRange",
    "Audit",
    "AuditInfo",
    "CreatedBy",
    "UpdatedBy",
    "ContactInfo",
    "Note",
    "Comment",
}


def _is_seedable(class_name: str, source_file: Path, service_dir: Path) -> bool:
    """Return True only for domain entity/aggregate models worth seeding."""
    if any(class_name.endswith(s) for s in _SKIP_SUFFIXES):
        return False
    if class_name in _SUBDOC_NAMES:
        logger.debug("[seed] skipping sub-document model: %s", class_name)
        return False
    # Skip if not in the domain layer
    try:
        rel = source_file.relative_to(service_dir)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            return False
    except ValueError:
        pass
    return True


def _discover_models(service_dir: Path) -> list[dict[str, Any]]:
    """Walk service_dir/domain/ for Pydantic BaseModel subclasses that represent entities.

    Returns list of {class_name, collection, source_file, fields: [{name, type, required}]}.
    Skips application-layer commands, queries, events, and other non-entity models.
    """
    models: list[dict[str, Any]] = []
    for py in sorted(service_dir.rglob("*.py")):
        try:
            src = py.read_text(errors="replace")
        except Exception:
            continue
        if "BaseModel" not in src:
            continue
        try:
            tree = ast.parse(src, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [(b.id if isinstance(b, ast.Name) else getattr(b, "attr", None)) for b in node.bases]
            if "BaseModel" not in bases:
                continue
            if not _is_seedable(node.name, py, service_dir):
                logger.debug("[seed] skipping non-entity model: %s (%s)", node.name, py.name)
                continue
            fields: list[dict[str, str]] = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fname = stmt.target.id
                    if fname.startswith("_"):
                        continue
                    ftype = ast.unparse(stmt.annotation) if hasattr(ast, "unparse") else "Any"
                    required = stmt.value is None  # no default → required
                    fields.append({"name": fname, "type": ftype, "required": required})
            if fields:
                models.append(
                    {
                        "class_name": node.name,
                        "collection": _to_collection_name(node.name),
                        "source_file": str(py),
                        "fields": fields,
                    }
                )
    return models


def _extract_json_array(raw: str) -> list[dict]:
    """Extract a JSON array from LLM output — handles fences, prose, partial wrapping."""
    raw = raw.strip()
    # Try markdown fence first (most specific)
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Bare array anywhere in the response
    m = re.search(r"(\[.*\])", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Full response as JSON
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except Exception:
        pass
    return []


async def _llm_seed(model_info: dict[str, Any], service_slug: str, count: int) -> list[dict]:
    """Ask LLM for `count` realistic seed records for a Pydantic model."""
    fields_desc = "\n".join(
        f"  {f['name']}: {f['type']} {'(required)' if f['required'] else '(optional)'}" for f in model_info["fields"]
    )
    system = (
        "You generate realistic synthetic seed data for a software application. "
        "Return ONLY a valid JSON array. No prose, no markdown fences, no explanation. "
        "Start your response with [ and end with ]."
    )
    prompt = (
        f"Generate {count} realistic seed records for the Pydantic model '{model_info['class_name']}' "
        f"in the '{service_slug}' service.\n\nFields:\n{fields_desc}\n\n"
        f"Return a JSON array of {count} objects with all required fields populated. "
        f"Use realistic values relevant to a {service_slug} domain. "
        f"Output ONLY the JSON array — nothing else."
    )

    raw = ""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"),
                    "prompt": f"{system}\n\n{prompt}",
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                },
            )
            if r.status_code == 200:
                raw = r.json().get("response", "")
                logger.debug("[seed] ollama raw (%d chars): %.200s", len(raw), raw)
    except Exception as exc:
        logger.debug("[seed] ollama call failed: %s", exc)

    if not raw.strip():
        try:
            import anthropic

            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                client = anthropic.Anthropic(api_key=key)
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = msg.content[0].text
                logger.debug("[seed] anthropic raw (%d chars): %.200s", len(raw), raw)
        except Exception as exc:
            logger.debug("[seed] anthropic fallback failed: %s", exc)

    if not raw.strip():
        logger.warning("[seed] both LLM backends returned empty for %s", model_info["class_name"])
        return []

    records = _extract_json_array(raw)
    if not records:
        logger.warning(
            "[seed] JSON extraction failed for %s — raw: %.300s",
            model_info["class_name"],
            raw,
        )
    return records


def _validate_pydantic(model_info: dict, records: list[dict], service_dir: Path) -> list[dict]:
    """Try to import the live Pydantic model and validate each record.

    Falls back to field-presence checking if import fails.
    Returns only records that pass validation.
    """
    src_file = Path(model_info["source_file"])
    validated: list[dict] = []

    # Attempt live Pydantic validation
    try:
        module_path = src_file.relative_to(service_dir)
        module_name = ".".join(module_path.with_suffix("").parts)
        if str(service_dir.parent) not in sys.path:
            sys.path.insert(0, str(service_dir.parent))
        import importlib

        mod = importlib.import_module(f"{service_dir.name}.{module_name}")
        model_cls = getattr(mod, model_info["class_name"])
        for rec in records:
            try:
                model_cls(**rec)
                validated.append(rec)
            except Exception as exc:
                logger.debug("[seed] validation failed for %s: %s", model_info["class_name"], exc)
        logger.info(
            "[seed] %s: %d/%d records passed Pydantic validation",
            model_info["class_name"],
            len(validated),
            len(records),
        )
        return validated
    except Exception:
        pass

    # Fallback: check required fields are present
    required = {f["name"] for f in model_info["fields"] if f["required"]}
    for rec in records:
        if required.issubset(rec.keys()):
            validated.append(rec)
    logger.info(
        "[seed] %s: %d/%d records passed field-presence check",
        model_info["class_name"],
        len(validated),
        len(records),
    )
    return validated


async def _insert_records(mongo_uri: str, db_name: str, collection: str, records: list[dict]) -> int:
    """Insert records into MongoDB. Returns count inserted."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        if records:
            result = await db[collection].insert_many(records)
            return len(result.inserted_ids)
    except Exception as exc:
        logger.warning("[seed] insert failed for %s.%s: %s", db_name, collection, exc)
    return 0


class SeedAgent(BaseETASSAgent):
    """Seeds the database with Pydantic-validated synthetic data.

    Runs after service_gen (codegen), before serve.
    Uses the generated Pydantic models as validators — records that fail
    validation are dropped before insertion.

    Context keys
    ------------
    service_slug   str    Service name, e.g. "work-order".
    output_dir     str    Root of generated service directory.
    mongo_uri      str    MongoDB connection URI (default: $MONGODB_URI or localhost).
    db_name        str    Database name (default: service_slug with hyphens → underscores).
    seed_count     int    Records per model (default: 5).
    """

    agent_type = "seed"
    description = (
        "Seeds MongoDB with Pydantic-validated synthetic data for a generated service. "
        "Runs after codegen, before serve."
    )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict[str, Any]) -> dict[str, Any]:
        service_slug = str(context.get("service_slug", "")).strip()
        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"seeded": False}, observations=["no service_slug in context"])

        output_dir_raw = context.get("output_dir") or str(_REPO / "generated")
        service_dir = Path(output_dir_raw) / service_slug
        if not service_dir.exists():
            self._reward(False, 0.1)
            return self._result(
                payload={
                    "seeded": False,
                    "error": f"service dir not found: {service_dir}",
                },
                observations=[f"generated service directory missing: {service_dir}"],
            )

        mongo_uri = context.get("mongo_uri") or os.environ.get("MONGODB_URI") or "mongodb://localhost:27017"
        db_name = context.get("db_name") or service_slug.replace("-", "_")
        seed_count = int(context.get("seed_count", _SEED_COUNT))

        # 1. Discover Pydantic models
        models = _discover_models(service_dir)
        if not models:
            self._reward(False, 0.3)
            return self._result(
                payload={"seeded": False, "reason": "no Pydantic models found"},
                observations=[f"no BaseModel subclasses found in {service_dir}"],
            )
        logger.info("[seed] discovered %d models in %s", len(models), service_dir)

        # 2–4. Generate → validate → insert per model
        summary: dict[str, int] = {}
        for m in models:
            logger.info("[seed] seeding %s → %s.%s", m["class_name"], db_name, m["collection"])
            records = await _llm_seed(m, service_slug, seed_count)
            if not records:
                logger.warning("[seed] LLM returned no records for %s", m["class_name"])
                summary[m["collection"]] = 0
                continue
            validated = _validate_pydantic(m, records, service_dir)
            inserted = await _insert_records(mongo_uri, db_name, m["collection"], validated)
            summary[m["collection"]] = inserted
            logger.info(
                "[seed] inserted %d records into %s.%s",
                inserted,
                db_name,
                m["collection"],
            )

        total = sum(summary.values())
        success = total > 0
        self._reward(success, 0.9 if success else 0.2)
        obs = [f"{col}: {n} records" for col, n in summary.items()]
        return self._result(
            payload={
                "seeded": success,
                "service_slug": service_slug,
                "db": db_name,
                "collections": summary,
                "total_inserted": total,
                "models_discovered": len(models),
            },
            observations=obs or ["no records inserted"],
        )
