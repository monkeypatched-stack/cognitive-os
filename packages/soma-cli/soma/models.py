"""Soma models — Pydantic models mirroring the constitutional CRD schema."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── Enums ─────────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"


class ReviewMode(str, Enum):
    constitutional = "constitutional"
    functional = "functional"
    syntactic = "syntactic"


class ResourceKind(str, Enum):
    ConstitutionalModule = "ConstitutionalModule"
    ConstitutionalPrinciple = "ConstitutionalPrinciple"
    ConstitutionalInvariant = "ConstitutionalInvariant"
    ConstitutionalDeliverable = "ConstitutionalDeliverable"
    ConstitutionalPrompt = "ConstitutionalPrompt"
    ConstitutionalProcess = "ConstitutionalProcess"


# ── Base ──────────────────────────────────────────────────────────────────────


class ResourceMetadata(BaseModel):
    name: str
    module: str | None = None
    layer: int | None = None
    alias: str | None = None
    biological_role: str | None = Field(None, alias="biologicalRole")
    software_role: str | None = Field(None, alias="softwareRole")
    phase: str | None = None
    source: str | None = None
    id: int | None = None
    severity: Severity | None = None

    model_config = {"populate_by_name": True}


class ConstitutionalResource(BaseModel):
    api_version: str = Field(alias="apiVersion", default="constitution.agentos.io/v1")
    kind: ResourceKind
    metadata: ResourceMetadata
    spec: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


# ── Typed spec models ─────────────────────────────────────────────────────────


class ModuleSpec(BaseModel):
    owns: list[str] = Field(default_factory=list)
    never_owns: list[str] = Field(default_factory=list, alias="neverOwns")
    submodules: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    exposes: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class PrincipleSpec(BaseModel):
    statement: str
    rationale: str | None = None
    examples: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class InvariantSpec(BaseModel):
    rule: str
    rationale: str | None = None
    audit_check: str | None = Field(None, alias="auditCheck")
    rejection: str

    model_config = {"populate_by_name": True}


class DeliverableSpec(BaseModel):
    artifact: str
    description: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, alias="acceptanceCriteria")

    model_config = {"populate_by_name": True}


class CoTStep(BaseModel):
    step: int
    instruction: str
    constraint: str | None = None
    audit_gate: bool = Field(False, alias="auditGate")

    model_config = {"populate_by_name": True}


class ReviewOutcomes(BaseModel):
    approved: str
    rejected: list[str] = Field(default_factory=list)


class ReviewGate(BaseModel):
    mode: ReviewMode = ReviewMode.constitutional
    criteria: list[str] = Field(default_factory=list)
    outcomes: ReviewOutcomes


class PromptSpec(BaseModel):
    preamble: str | None = None
    cot: dict[str, list[CoTStep]] = Field(default_factory=dict)
    review_gate: ReviewGate | None = Field(None, alias="reviewGate")

    model_config = {"populate_by_name": True}


# ── Chart manifest ─────────────────────────────────────────────────────────────


class ChartMetadata(BaseModel):
    name: str
    version: str
    description: str | None = None
    api_version: str = Field("v2", alias="apiVersion")

    model_config = {"populate_by_name": True}


class Chart(BaseModel):
    metadata: ChartMetadata
    values: dict[str, Any] = Field(default_factory=dict)
    resources: list[ConstitutionalResource] = Field(default_factory=list)


class SomaRelease(BaseModel):
    name: str
    charts: list[Chart] = Field(default_factory=list)
    global_values: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_resources(self) -> list[ConstitutionalResource]:
        return [r for chart in self.charts for r in chart.resources]

    def resources_of_kind(self, kind: ResourceKind) -> list[ConstitutionalResource]:
        return [r for r in self.all_resources if r.kind == kind]

    def module(self, name: str) -> Chart | None:
        return next((c for c in self.charts if c.metadata.name == name), None)
