from datetime import datetime, date, timedelta, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# ─── Literals (mirror TS union types) ────────────────────────────────────────

MachineStatus = Literal[
    "Operational", "Maintenance", "Idle", "Quarantined", "Decommissioned"
]

MachineTypeCategory = Literal[
    "Mechanical Machine",
    "Electrical Machine",
    "Hydraulic Machine",
    "Pneumatic Machine",
    "CNC / Automated Machine",
    "Industrial Robot",
    "Autonomous System",
    "Embedded / Smart Machine",
    "Heavy Industrial Machine",
    "Other",
]

AreaClassification = Literal[
    "Grade A",
    "Grade B",
    "Grade C",
    "Grade D",
    "ISO 5",
    "ISO 6",
    "ISO 7",
    "ISO 8",
    "Unclassified",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_machine_status(value):
    if value == "Active":
        return "Operational"
    return value


def _roll_maintenance(
    last: Optional[str],
    freq: Optional[int],
) -> tuple[Optional[str], Optional[str]]:
    """
    Given a last_maintenance_date (ISO string) and a frequency in days,
    return (rolled_last, next_due) where rolled_last has been advanced
    by one interval per overdue cycle until next_due is in the future.

    Mirrors the useEffect roll-forward logic in MachineDialog.tsx exactly.
    """
    if not last or not freq or freq <= 0:
        return last, None

    last_date = date.fromisoformat(last)
    today = date.today()
    delta = timedelta(days=freq)

    next_due = last_date + delta

    # Advance one cycle at a time until next_due is in the future
    while next_due <= today:
        last_date = next_due
        next_due = last_date + delta

    return last_date.isoformat(), next_due.isoformat()


# ─── Base (mirrors PharmaceuticalMachine TS type) ────────────────────────────


class PharmaceuticalMachine(BaseModel):
    machine_id: str
    serial_no: str
    model_no: str
    type_category: MachineTypeCategory
    manufacturer: str
    manufacturer_country: Optional[str] = None

    # Location
    plant_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None

    # Taxonomy
    family_id: Optional[str] = None
    class_id: Optional[str] = None
    subclass_id: Optional[str] = None

    area_classification: AreaClassification = "Unclassified"
    asset_tag: Optional[str] = None
    workstation_id: Optional[str] = None

    # Dates stored as ISO strings (string | null in TS)
    purchase_date: Optional[str] = None
    installation_date: Optional[str] = None
    warranty_expiry_date: Optional[str] = None
    mfg_date: Optional[str] = None

    service_life_years: Optional[float] = None
    name: Optional[str] = None
    description: Optional[str] = None
    status: MachineStatus = "Idle"

    # ── Maintenance schedule ──────────────────────────────────────────────────
    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = Field(default=None, ge=1)
    # Computed & stored — never set manually; always derived by the validator.
    maintenance_next_due_date: Optional[str] = None

    @model_validator(mode="after")
    def installation_after_purchase(self) -> "PharmaceuticalMachine":
        if self.purchase_date and self.installation_date:
            if self.installation_date < self.purchase_date:
                raise ValueError(
                    "'installation_date' cannot be before 'purchase_date'."
                )
        return self

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_machine_status(value)

    @model_validator(mode="after")
    def compute_maintenance_schedule(self) -> "PharmaceuticalMachine":
        """
        Auto-roll last_maintenance_date and recompute maintenance_next_due_date.
        Mirrors the useEffect in MachineDialog.tsx: advances last_maintenance_date
        by one interval per overdue cycle so next_due is always a future date.
        Any caller-supplied maintenance_next_due_date is ignored and overwritten.
        """
        rolled_last, next_due = _roll_maintenance(
            self.last_maintenance_date,
            self.maintenance_frequency_days,
        )
        self.last_maintenance_date = rolled_last
        self.maintenance_next_due_date = next_due
        return self


# ─── Create (identical shape to base) ────────────────────────────────────────


class PharmaceuticalMachineCreate(PharmaceuticalMachine):
    pass


# ─── Update (all fields optional) ────────────────────────────────────────────


class PharmaceuticalMachineUpdate(BaseModel):
    machine_id: Optional[str] = None
    serial_no: Optional[str] = None
    model_no: Optional[str] = None
    type_category: Optional[MachineTypeCategory] = None
    manufacturer: Optional[str] = None
    manufacturer_country: Optional[str] = None

    # Location
    plant_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None

    # Taxonomy
    family_id: Optional[str] = None
    class_id: Optional[str] = None
    subclass_id: Optional[str] = None

    area_classification: Optional[AreaClassification] = None
    asset_tag: Optional[str] = None
    workstation_id: Optional[str] = None
    purchase_date: Optional[str] = None
    installation_date: Optional[str] = None
    warranty_expiry_date: Optional[str] = None
    mfg_date: Optional[str] = None
    service_life_years: Optional[float] = None
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[MachineStatus] = None

    # ── Maintenance schedule ──────────────────────────────────────────────────
    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = Field(default=None, ge=1)
    # Read-only from the client's perspective — recomputed on every update.
    maintenance_next_due_date: Optional[str] = None

    @model_validator(mode="after")
    def installation_after_purchase(self) -> "PharmaceuticalMachineUpdate":
        if self.purchase_date and self.installation_date:
            if self.installation_date < self.purchase_date:
                raise ValueError(
                    "'installation_date' cannot be before 'purchase_date'."
                )
        return self

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_machine_status(value)

    @model_validator(mode="after")
    def compute_maintenance_schedule(self) -> "PharmaceuticalMachineUpdate":
        """
        Recompute maintenance_next_due_date whenever last_maintenance_date or
        maintenance_frequency_days is included in a PATCH payload.
        Only runs if at least one of the two fields is present in this update.
        """
        if (
            self.last_maintenance_date is None
            and self.maintenance_frequency_days is None
        ):
            return self
        rolled_last, next_due = _roll_maintenance(
            self.last_maintenance_date,
            self.maintenance_frequency_days,
        )
        self.last_maintenance_date = rolled_last
        self.maintenance_next_due_date = next_due
        return self


# ─── Response ─────────────────────────────────────────────────────────────────


class PharmaceuticalMachineResponse(PharmaceuticalMachine):
    class Config:
        from_attributes = True


# ─── Pagination ───────────────────────────────────────────────────────────────


class PaginatedMachineResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PharmaceuticalMachineResponse]
