"""Shared type-mapping tables for the microservice generator templates."""

from __future__ import annotations

_SA_TYPES: dict[str, str] = {
    "str": "String(255)",
    "text": "Text",
    "int": "Integer",
    "float": "Float",
    "bool": "Boolean",
    "datetime": "DateTime(timezone=True)",
    "uuid": "Uuid",
    "list": "JSON",
    "dict": "JSON",
    "decimal": "Numeric(12, 4)",
}

_PY_TYPES: dict[str, str] = {
    "str": "str",
    "text": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "uuid": "UUID",
    "list": "list[Any]",
    "dict": "dict[str, Any]",
    "decimal": "Decimal",
}

_MONGO_TYPES: dict[str, str] = {
    "str": "str",
    "text": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "uuid": "UUID",
    "list": "list[Any]",
    "dict": "dict[str, Any]",
    "decimal": "Decimal",
}
