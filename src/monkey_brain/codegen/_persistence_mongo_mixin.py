"""PersistenceMongoMixin — templates for database.py, models.py, crud.py (Mongo flavor)."""

from __future__ import annotations

from ._types import _MONGO_TYPES


class PersistenceMongoMixin:
    """Generates the Motor/Beanie storage layer: database, models, CRUD."""

    def _database_mongo(self) -> str:
        url_env = self.s.db.url_env
        return f'''\
"""Motor / Beanie async MongoDB connection."""
from __future__ import annotations

import os

import motor.motor_asyncio
from beanie import init_beanie

from .models import {self.cls}

_MONGO_URL = os.environ.get({url_env!r}, "mongodb://localhost:27017")
_DB_NAME = os.environ.get("MONGO_DB", "{self.n}_db")

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


async def init_db() -> None:
    global _client
    _client = motor.motor_asyncio.AsyncIOMotorClient(_MONGO_URL)
    await init_beanie(database=_client[_DB_NAME], document_models=[{self.cls}])


async def close_db() -> None:
    if _client is not None:
        _client.close()


async def ping_db() -> bool:
    try:
        if _client is None:
            return False
        await _client.admin.command("ping")
        return True
    except Exception:
        return False
'''

    def _models_mongo(self) -> str:
        types_needed = {f.type for f in self.s.fields}
        imports = ["from __future__ import annotations", ""]
        imports.append("from typing import Any, Optional")
        if "datetime" in types_needed or self.s.timestamps or self.s.soft_delete:
            imports.append("from datetime import datetime")
        if "uuid" in types_needed:
            imports.append("from uuid import UUID")
        if "decimal" in types_needed:
            imports.append("from decimal import Decimal")
        imports += [
            "",
            "from beanie import Document, Indexed",
            "from pydantic import Field",
            "from typing import Annotated",
        ]

        fields = []
        for f in self.s.fields:
            py_type = _MONGO_TYPES.get(f.type, "str")
            if f.enum:
                literals = ", ".join(repr(e) for e in f.enum)
                py_type = f"Literal[{literals}]"
            if f.unique:
                py_type = f"Annotated[{py_type}, Indexed(unique=True)]"
            elif f.index:
                py_type = f"Annotated[{py_type}, Indexed()]"
            optional = f.nullable or not f.required
            field_type = f"Optional[{py_type}]" if optional else py_type
            default_part = " = None" if optional else (f" = {f.default!r}" if f.default is not None else "")
            fields.append(f"    {f.name}: {field_type}{default_part}")

        ts_fields = ""
        if self.s.timestamps:
            ts_fields = (
                "    created_at: datetime = Field(default_factory=datetime.utcnow)\n"
                "    updated_at: datetime = Field(default_factory=datetime.utcnow)\n"
            )
        sd_field = ""
        if self.s.soft_delete:
            sd_field = "    deleted_at: Optional[datetime] = None\n"

        body = "\n".join(fields)
        return f'''\
"""Beanie ODM document model."""
{chr(10).join(imports)}


class {self.cls}(Document):
{body}
{ts_fields}{sd_field}
    class Settings:
        name = {self.tbl!r}
        use_revision = True
'''

    def _crud_mongo(self) -> str:
        sd_filter = '{"deleted_at": None}' if self.s.soft_delete else "{}"
        sd_delete = (
            '    await obj.set({"deleted_at": datetime.utcnow(), "updated_at": datetime.utcnow()})\n'
            if self.s.soft_delete
            else "    await obj.delete()\n"
        )

        return f'''\
"""CRUD repository — Motor/Beanie operations for {self.cls}."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId

from .exceptions import ConflictError
from .models import {self.cls}
from .schemas import {self.cls}Create, {self.cls}Patch, {self.cls}Update


async def create_{self.n}(data: {self.cls}Create) -> {self.cls}:
    obj = {self.cls}(**data.model_dump())
    await obj.insert()
    return obj


async def get_{self.n}(id: str) -> {self.cls} | None:
    filter_q: dict[str, Any] = {{"_id": PydanticObjectId(id), **{sd_filter}}}
    return await {self.cls}.find_one(filter_q)


async def list_{self.tbl}(
    limit: int = {self.s.pagination.default_limit},
    offset: int = 0,
) -> tuple[list[{self.cls}], int]:
    query = {self.cls}.find({sd_filter})
    total = await query.count()
    items = await query.skip(offset).limit(limit).to_list()
    return items, total


async def update_{self.n}(id: str, data: {self.cls}Update) -> {self.cls} | None:
    obj = await get_{self.n}(id)
    if obj is None:
        return None
    update_data = data.model_dump()
    if hasattr(obj, "updated_at"):
        update_data["updated_at"] = datetime.utcnow()
    await obj.set(update_data)
    return obj


async def patch_{self.n}(id: str, data: {self.cls}Patch) -> {self.cls} | None:
    obj = await get_{self.n}(id)
    if obj is None:
        return None
    update_data = data.model_dump(exclude_unset=True)
    if update_data and hasattr(obj, "updated_at"):
        update_data["updated_at"] = datetime.utcnow()
    if update_data:
        await obj.set(update_data)
    return obj


async def delete_{self.n}(id: str) -> bool:
    obj = await get_{self.n}(id)
    if obj is None:
        return False
{sd_delete}    return True
'''
