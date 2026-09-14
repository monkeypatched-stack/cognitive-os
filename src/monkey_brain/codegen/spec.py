"""MicroserviceSpec — canonical input schema for the code generator.

User defines model fields + DB type. Generator produces complete production microservice.
"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, model_validator

FieldType = Literal["str", "text", "int", "float", "bool", "datetime", "uuid", "list", "dict", "decimal"]
DBType = Literal["postgresql", "mongodb", "sqlite"]
AuthType = Literal["bearer", "api_key", "none"]


class FieldSpec(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    type: FieldType = "str"
    required: bool = True
    unique: bool = False
    index: bool = False
    nullable: bool = False
    default: Optional[Any] = None
    description: str = ""
    # Validators
    enum: list[str] = Field(default_factory=list)
    gt: Optional[float] = None
    lt: Optional[float] = None
    ge: Optional[float] = None
    le: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    regex: Optional[str] = None
    # Relations (SQL only)
    foreign_key: Optional[str] = None  # "table_name.column"

    @model_validator(mode="after")
    def enum_only_for_str(self) -> "FieldSpec":
        if self.enum and self.type not in ("str", "int"):
            raise ValueError("enum is only supported for str and int fields")
        return self


class DBSpec(BaseModel):
    type: DBType = "postgresql"
    url_env: str = "DATABASE_URL"
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=100)


class AuthSpec(BaseModel):
    enabled: bool = False
    type: AuthType = "bearer"
    secret_env: str = "JWT_SECRET"
    api_key_header: str = "X-API-Key"
    api_key_env: str = "API_KEY"


class PaginationSpec(BaseModel):
    enabled: bool = True
    default_limit: int = Field(default=20, ge=1, le=1000)
    max_limit: int = Field(default=100, ge=1, le=10000)


class MicroserviceSpec(BaseModel):
    name: str = Field(
        ...,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Resource name, snake_case (e.g. 'products')",
    )
    domain: str = Field(default="default", description="Domain context for OpenAPI tags")
    description: str = ""
    version: str = "1.0.0"
    port: int = Field(default=8000, ge=1024, le=65535)
    db: DBSpec = Field(default_factory=DBSpec)
    fields: list[FieldSpec] = Field(default_factory=list, min_length=1)
    timestamps: bool = True
    soft_delete: bool = False
    auth: AuthSpec = Field(default_factory=AuthSpec)
    pagination: PaginationSpec = Field(default_factory=PaginationSpec)

    @model_validator(mode="after")
    def no_reserved_field_names(self) -> "MicroserviceSpec":
        reserved = {"id", "created_at", "updated_at", "deleted_at"}
        for f in self.fields:
            if f.name in reserved:
                raise ValueError(f"Field '{f.name}' is reserved — it is auto-generated")
        return self

    @property
    def class_name(self) -> str:
        return "".join(p.capitalize() for p in self.name.split("_"))

    @property
    def table_name(self) -> str:
        return self.name if self.name.endswith("s") else f"{self.name}s"

    @property
    def is_sql(self) -> bool:
        return self.db.type in ("postgresql", "sqlite")

    @property
    def is_mongo(self) -> bool:
        return self.db.type == "mongodb"
