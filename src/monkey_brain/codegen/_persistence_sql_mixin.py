"""PersistenceSqlMixin — templates for database.py, models.py, crud.py, alembic (SQL flavor)."""

from __future__ import annotations

from ._types import _SA_TYPES, _PY_TYPES


class PersistenceSqlMixin:
    """Generates the SQLAlchemy storage layer: database, models, CRUD, alembic."""

    def _database_sql(self) -> str:
        db = self.s.db
        url_env = db.url_env
        url_prefix = "postgresql+asyncpg" if db.type == "postgresql" else "sqlite+aiosqlite"

        pool_config = (
            f"    pool_size={db.pool_size},\n    max_overflow={db.max_overflow},\n    pool_pre_ping=True,\n"
            if db.type == "postgresql"
            else ""
        )

        return f'''\
"""Async database engine and session factory."""
from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_raw_url = os.environ.get({url_env!r}, "{url_prefix}:///./dev.db")
# Rewrite scheme for asyncpg/aiosqlite if user supplied plain URL
if _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
if _raw_url.startswith("sqlite:///"):
    _raw_url = _raw_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

engine = create_async_engine(
    _raw_url,
{pool_config}    echo=os.environ.get("SQL_ECHO", "false").lower() == "true",
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
'''

    def _models_sql(self) -> str:
        sa_imports = self._sa_imports()
        columns = self._sql_columns()
        ts_cols = ""
        if self.s.timestamps:
            ts_cols = (
                "    created_at: Mapped[datetime] = mapped_column(\n"
                "        DateTime(timezone=True), default=datetime.utcnow, nullable=False\n"
                "    )\n"
                "    updated_at: Mapped[datetime] = mapped_column(\n"
                "        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False\n"
                "    )\n"
            )
        sd_col = ""
        if self.s.soft_delete:
            sd_col = (
                "    deleted_at: Mapped[datetime | None] = mapped_column(\n"
                "        DateTime(timezone=True), nullable=True, index=True\n"
                "    )\n"
            )

        idx_lines = self._sql_indexes()

        return f'''\
"""SQLAlchemy 2.0 ORM model."""
from __future__ import annotations

{sa_imports}


class Base(DeclarativeBase):
    pass


class {self.cls}(Base):
    __tablename__ = {self.tbl!r}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
{columns}{ts_cols}{sd_col}{idx_lines}
'''

    def _sa_imports(self) -> str:
        # "Uuid" is always needed (the id column); DateTime is needed
        # whenever timestamps/soft_delete add a DateTime column even if
        # no declared field itself is a datetime.
        type_imports_set = {"Uuid"}
        if self.s.timestamps or self.s.soft_delete:
            type_imports_set.add("DateTime")
        for f in self.s.fields:
            sa_type = _SA_TYPES.get(f.type, "String(255)")
            type_imports_set.add(sa_type.split("(", 1)[0])
        type_imports = sorted(type_imports_set)
        if self.s.fields and any(f.foreign_key for f in self.s.fields):
            type_imports.append("ForeignKey")
        if any(f.index or f.unique for f in self.s.fields) or self.s.soft_delete:
            type_imports.append("Index")

        py_types_used = {f.type for f in self.s.fields}
        lines = [
            "from datetime import datetime",
            "from uuid import UUID, uuid4",
        ]
        if "decimal" in py_types_used:
            lines.append("from decimal import Decimal")
        if "list" in py_types_used or "dict" in py_types_used:
            lines.append("from typing import Any")
        lines += [
            "",
            "from sqlalchemy import " + ", ".join(sorted(type_imports)),
            "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column",
        ]
        return "\n".join(lines)

    def _sql_columns(self) -> str:
        lines = []
        for f in self.s.fields:
            sa_type = _SA_TYPES.get(f.type, "String(255)")
            py_type = _PY_TYPES.get(f.type, "str")
            nullable_suffix = " | None" if f.nullable or not f.required else ""
            mapped_type = f"Mapped[{py_type}{nullable_suffix}]"

            parts = [sa_type]
            if f.unique:
                parts.append("unique=True")
            if f.index:
                parts.append("index=True")
            if f.nullable or not f.required:
                parts.append("nullable=True")
            if f.default is not None:
                parts.append(f"default={f.default!r}")
            if f.foreign_key:
                parts.append(f'ForeignKey("{f.foreign_key}")')

            col_args = ", ".join(parts)
            lines.append(f"    {f.name}: {mapped_type} = mapped_column({col_args})")
        return "\n".join(lines) + "\n" if lines else ""

    def _sql_indexes(self) -> str:
        return ""

    def _crud_sql(self) -> str:
        sd_filter = ".where({cls}.deleted_at.is_(None))".format(cls=self.cls) if self.s.soft_delete else ""
        sd_delete = (
            (
                f"    if soft:\n"
                f"        obj.deleted_at = datetime.utcnow()\n"
                f"        obj.updated_at = datetime.utcnow()\n"
                f"    else:\n"
                f"        await db.delete(obj)\n"
            )
            if self.s.soft_delete
            else ("    await db.delete(obj)\n")
        )
        soft_param = ", soft: bool = True" if self.s.soft_delete else ""
        ts_update = "    obj.updated_at = datetime.utcnow()\n" if self.s.timestamps else ""

        filter_fields = "\n".join(
            f"    if {f.name} is not None:\n        q = q.where({self.cls}.{f.name} == {f.name})"
            for f in self.s.fields
            if f.index or f.unique
        )
        filter_params = ", ".join(
            f"{f.name}: {_PY_TYPES.get(f.type, 'str')} | None = None" for f in self.s.fields if f.index or f.unique
        )
        if filter_params:
            filter_params = f",\n    {filter_params}"

        return f'''\
"""CRUD repository — all database operations for {self.cls}."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import ConflictError, NotFoundError
from .models import {self.cls}
from .schemas import {self.cls}Create, {self.cls}Patch, {self.cls}Update


async def create_{self.n}(db: AsyncSession, data: {self.cls}Create) -> {self.cls}:
    obj = {self.cls}(**data.model_dump(exclude_none=False))
    db.add(obj)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError() from exc
    await db.refresh(obj)
    return obj


async def get_{self.n}(db: AsyncSession, id: UUID) -> {self.cls} | None:
    q = select({self.cls}).where({self.cls}.id == id){sd_filter}
    return (await db.execute(q)).scalar_one_or_none()


async def list_{self.tbl}(
    db: AsyncSession,
    limit: int = {self.s.pagination.default_limit},
    offset: int = 0{filter_params},
) -> tuple[list[{self.cls}], int]:
    q = select({self.cls}){sd_filter}
{filter_fields}
    total_q = select(func.count()).select_from(q.subquery())
    total: int = (await db.execute(total_q)).scalar_one()
    items = (await db.execute(q.order_by({self.cls}.id).offset(offset).limit(limit))).scalars().all()
    return list(items), total


async def update_{self.n}(db: AsyncSession, id: UUID, data: {self.cls}Update) -> {self.cls} | None:
    obj = await get_{self.n}(db, id)
    if obj is None:
        return None
    for key, val in data.model_dump().items():
        setattr(obj, key, val)
{ts_update}    await db.flush()
    await db.refresh(obj)
    return obj


async def patch_{self.n}(db: AsyncSession, id: UUID, data: {self.cls}Patch) -> {self.cls} | None:
    obj = await get_{self.n}(db, id)
    if obj is None:
        return None
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(obj, key, val)
{ts_update}    await db.flush()
    await db.refresh(obj)
    return obj


async def delete_{self.n}(db: AsyncSession, id: UUID{soft_param}) -> bool:
    obj = await get_{self.n}(db, id)
    if obj is None:
        return False
{sd_delete}    await db.flush()
    return True
'''

    def _alembic_ini(self) -> str:
        return f"""\
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""

    def _alembic_env(self) -> str:
        return f'''\
"""Alembic environment — async SQLAlchemy migrations."""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from {self.n}.database import _raw_url
from {self.n}.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", _raw_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=_raw_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {{}}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(lambda conn: context.configure(conn, target_metadata=target_metadata))
        async with connection.begin():
            await connection.run_sync(lambda _: context.run_migrations())
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
'''
