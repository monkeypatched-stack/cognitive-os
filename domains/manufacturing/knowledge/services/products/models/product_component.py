from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from services.products.models.product_common import (
    ComponentType,
    UnitOfMeasure,
    utc_now,
    ensure_utc,
)


from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from enum import Enum
from datetime import date, datetime, timezone
from decimal import Decimal


class UnitOfMeasure(str, Enum):
    PIECE = "piece"
    KILOGRAM = "kg"
    GRAM = "g"
    LITER = "liter"
    METER = "meter"
    SQUARE_METER = "sq_meter"
    CUBIC_METER = "cu_meter"
    INCH = "inch"
    FOOT = "foot"
    POUND = "pound"
    OUNCE = "ounce"


class Currency(str, Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    INR = "INR"
    JPY = "JPY"
    CNY = "CNY"


class PartCategory(str, Enum):
    RAW_MATERIAL = "raw_material"
    COMPONENT = "component"
    SUBASSEMBLY = "subassembly"
    CONSUMABLE = "consumable"
    PACKAGING = "packaging"
    FASTENER = "fastener"
    ELECTRONIC = "electronic"
    OTHER = "other"


class ECNStatus(str, Enum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    RELEASED = "released"
    REJECTED = "rejected"
    OBSOLETE = "obsolete"


class HazardClass(str, Enum):
    FLAMMABLE = "flammable"
    CORROSIVE = "corrosive"
    TOXIC = "toxic"
    OXIDIZER = "oxidizer"
    EXPLOSIVE = "explosive"
    RADIOACTIVE = "radioactive"
    IRRITANT = "irritant"
    CARCINOGEN = "carcinogen"


class BOMRelationshipType(str, Enum):
    ASSEMBLY = "assembly"
    SUBASSEMBLY = "subassembly"
    PHANTOM = "phantom"  # Transient assembly — not stocked
    REFERENCE = "reference"  # Informational only, not consumed
    CO_PRODUCT = "co_product"  # By-product produced alongside main output


class BOMComponentType(str, Enum):
    RAW_MATERIAL = "Raw Material"
    COMPONENT = "Component"
    SUBASSEMBLY = "Subassembly"
    FINISHED_GOOD = "Finished Good"
    PACKAGING = "Packaging"
    CONSUMABLE = "Consumable"
    OTHER = "Other"


class UsageType(str, Enum):
    STANDARD = "Standard"
    OPTIONAL = "Optional"
    ALTERNATE = "Alternate"
    SUBSTITUTE = "Substitute"
    PHANTOM = "Phantom"
    REFERENCE = "Reference"


class BOMType(str, Enum):
    ENGINEERING = "Engineering"
    MANUFACTURING = "Manufacturing"
    SERVICE = "Service"
    SALES = "Sales"
    CONFIGURABLE = "Configurable"


class BOMStatus(str, Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    OBSOLETE = "Obsolete"
    ARCHIVED = "Archived"


class LeadTimeStatus(str, Enum):
    IN_STOCK = "In Stock"
    SHORT = "Short"
    MEDIUM = "Medium"
    LONG = "Long"
    CRITICAL = "Critical"
    UNKNOWN = "Unknown"


class Supplier(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    lead_time_days: Optional[int] = Field(None, ge=0)
    preferred: bool = False
    remarks: Optional[str] = None


class ScrapAndYield(BaseModel):
    """Scrap factors and yield rates for manufacturing consumption calculations."""

    scrap_factor_percent: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        description=(
            "Extra material ordered/consumed to cover expected scrap. "
            "e.g. 5 means order 5 % more than BOM quantity."
        ),
    )
    process_yield_percent: Decimal = Field(
        default=Decimal("100"),
        gt=0,
        le=100,
        description=(
            "Expected good-unit yield from this process step. "
            "e.g. 95 means 5 % of units are rejected."
        ),
    )
    shrinkage_factor_percent: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        description="Material lost due to shrinkage, evaporation, or trimming.",
    )
    operation_scrap_code: Optional[str] = Field(
        None,
        max_length=20,
        description="ERP/MES scrap reason code (e.g. 'TRIM', 'DEFECT', 'EVAP').",
    )

    @property
    def gross_quantity_multiplier(self) -> Decimal:
        """
        Combined multiplier to apply to the BOM net quantity so the work order
        issues enough material to yield the required good output.

        gross_qty = net_qty × gross_quantity_multiplier
        """
        scrap = Decimal("1") + (self.scrap_factor_percent / Decimal("100"))
        shrink = Decimal("1") + (self.shrinkage_factor_percent / Decimal("100"))
        yield_ = self.process_yield_percent / Decimal("100")
        return (scrap * shrink) / yield_

    def gross_quantity_for(self, net_quantity: Decimal) -> Decimal:
        """Returns the gross quantity needed to deliver `net_quantity` good units."""
        return (net_quantity * self.gross_quantity_multiplier).quantize(
            Decimal("0.0001")
        )


class ReferenceDesignator(BaseModel):
    """
    PCB/electronics reference designator entry.
    Each instance maps one refdes (e.g. 'C12') to this part on a specific board.
    """

    designator: str = Field(
        ...,
        max_length=20,
        pattern=r"^[A-Z]{1,5}\d+[A-Z]?$",
        description="IPC-7711 reference designator, e.g. R1, C12, U3A, TP101.",
    )
    board_id: str = Field(
        ..., max_length=50, description="PCB assembly part number or ID."
    )
    schematic_page: Optional[int] = Field(
        None, ge=1, description="Schematic sheet number."
    )
    x_mm: Optional[Decimal] = Field(None, description="X centroid on board (mm).")
    y_mm: Optional[Decimal] = Field(None, description="Y centroid on board (mm).")
    rotation_deg: Optional[Decimal] = Field(
        None, ge=0, lt=360, description="Placement rotation (°)."
    )
    side: Optional[str] = Field(
        None, pattern=r"^(top|bottom)$", description="'top' or 'bottom'."
    )
    dnp: bool = Field(
        default=False,
        description="Do Not Place — part is listed but intentionally unpopulated.",
    )

    @field_validator("designator", mode="before")
    @classmethod
    def uppercase_designator(cls, v: str) -> str:
        return v.upper() if isinstance(v, str) else v


class ShelfLifeInfo(BaseModel):
    """Shelf-life tracking for moisture-sensitive, chemical, or perishable parts."""

    shelf_life_days: Optional[int] = Field(
        None, gt=0, description="Total shelf life from manufacture date (days)."
    )
    floor_life_hours: Optional[int] = Field(
        None,
        gt=0,
        description=(
            "IPC/JEDEC MSL floor life once the original packaging is opened (hours). "
            "Relevant for moisture-sensitive devices (MSDs)."
        ),
    )
    msl_level: Optional[int] = Field(
        None,
        ge=1,
        le=6,
        description="IPC/JEDEC J-STD-020 Moisture Sensitivity Level (1–6).",
    )
    storage_temperature_min_c: Optional[Decimal] = Field(
        None, description="Minimum storage temperature (°C)."
    )
    storage_temperature_max_c: Optional[Decimal] = Field(
        None, description="Maximum storage temperature (°C)."
    )
    storage_humidity_max_pct: Optional[Decimal] = Field(
        None, ge=0, le=100, description="Maximum relative humidity for storage (%)."
    )
    requires_dry_pack: bool = Field(
        default=False, description="Must be stored/shipped in a sealed dry-pack bag."
    )
    requires_cold_chain: bool = Field(
        default=False, description="Must remain in a refrigerated/frozen supply chain."
    )
    date_code_format: Optional[str] = Field(
        None,
        max_length=20,
        description="Expected date-code format on the part label, e.g. 'YYWW' or 'YYYYMMDD'.",
    )

    @model_validator(mode="after")
    def validate_temp_range(self) -> "ShelfLifeInfo":
        lo = self.storage_temperature_min_c
        hi = self.storage_temperature_max_c
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError(
                "storage_temperature_min_c must be less than storage_temperature_max_c"
            )
        return self

    def is_expired(self, manufacture_date: date) -> bool:
        """True if shelf life has elapsed since manufacture_date."""
        if self.shelf_life_days is None:
            return False
        age = (date.today() - manufacture_date).days
        return age > self.shelf_life_days

    def remaining_shelf_life_days(self, manufacture_date: date) -> Optional[int]:
        if self.shelf_life_days is None:
            return None
        return max(0, self.shelf_life_days - (date.today() - manufacture_date).days)


class HazmatInfo(BaseModel):
    batch_tracking_required: bool = False
    serial_tracking_required: bool = False
    expiry_tracking_required: bool = False
    hazmat: bool = False
    temp_sensitive: bool = False
    controlled_substance: bool = False
    regulatory_certifications: Optional[list[str]] = None
    msds_url: Optional[str] = None

    model_config = {"str_strip_whitespace": True}


class ECNRecord(BaseModel):
    """A single Engineering Change Notice affecting this part."""

    ecn_number: str = Field(
        ...,
        max_length=30,
        pattern=r"^ECN-\d{4,10}$",
        description="ECN identifier, e.g. 'ECN-20240312'.",
    )
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    status: ECNStatus = Field(default=ECNStatus.DRAFT)
    initiated_by: str = Field(
        ..., max_length=100, description="Engineer or team who raised the ECN."
    )
    approved_by: Optional[str] = Field(None, max_length=100)
    date_initiated: date = Field(default_factory=date.today)
    date_approved: Optional[date] = None
    date_effective: Optional[date] = None
    affected_revision_from: Optional[str] = Field(
        None, max_length=10, description="Previous part revision this ECN supersedes."
    )
    affected_revision_to: Optional[str] = Field(
        None, max_length=10, description="New part revision introduced by this ECN."
    )
    reason_code: Optional[str] = Field(
        None,
        max_length=50,
        description="Reason category, e.g. 'COST_REDUCTION', 'SAFETY', 'SUPPLIER_CHANGE'.",
    )
    linked_documents: List[str] = Field(
        default_factory=list,
        description="Document IDs (drawings, test reports) related to this ECN.",
    )

    @model_validator(mode="after")
    def validate_approval_flow(self) -> "ECNRecord":
        if (
            self.status in (ECNStatus.APPROVED, ECNStatus.RELEASED)
            and not self.approved_by
        ):
            raise ValueError(
                "approved_by is required when status is APPROVED or RELEASED"
            )
        if self.date_approved and self.date_approved < self.date_initiated:
            raise ValueError("date_approved cannot precede date_initiated")
        if (
            self.date_effective
            and self.date_approved
            and self.date_effective < self.date_approved
        ):
            raise ValueError("date_effective cannot precede date_approved")
        return self

    @property
    def is_released(self) -> bool:
        return self.status == ECNStatus.RELEASED

    @property
    def days_open(self) -> int:
        end = self.date_approved or date.today()
        return (end - self.date_initiated).days


class BOMLink(BaseModel):
    """
    Defines this part's position inside a multi-level Bill of Materials.
    One part may appear in multiple BOMs (shared components), so a list of
    BOMLink objects is attached to the part rather than a single parent ID.
    """

    parent_bom_id: str = Field(
        ...,
        max_length=50,
        description="Part number of the immediate parent assembly in the BOM.",
    )
    bom_level: int = Field(
        ...,
        ge=1,
        description="BOM depth level (1 = top-level assembly, 2 = subassembly, …).",
    )
    quantity_per_assembly: Decimal = Field(
        ...,
        gt=0,
        description="Net quantity of this part required per one parent assembly.",
    )
    find_number: Optional[int] = Field(
        None,
        ge=1,
        description="Find number / item sequence on the engineering drawing BOM table.",
    )
    relationship_type: BOMRelationshipType = Field(default=BOMRelationshipType.ASSEMBLY)
    is_configurable: bool = Field(
        default=False,
        description="Part may be substituted based on product configuration options.",
    )
    substitute_part_numbers: List[str] = Field(
        default_factory=list,
        description="Approved alternate part numbers that may be used in place of this part.",
    )
    effective_date_start: Optional[date] = Field(
        None, description="Date from which this BOM relationship is valid."
    )
    effective_date_end: Optional[date] = Field(
        None, description="Date after which this BOM relationship is superseded."
    )
    notes: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="after")
    def validate_effectivity(self) -> "BOMLink":
        s, e = self.effective_date_start, self.effective_date_end
        if s and e and s >= e:
            raise ValueError(
                "effective_date_start must be earlier than effective_date_end"
            )
        return self

    @property
    def is_currently_effective(self) -> bool:
        today = date.today()
        after_start = (self.effective_date_start is None) or (
            today >= self.effective_date_start
        )
        before_end = (self.effective_date_end is None) or (
            today < self.effective_date_end
        )
        return after_start and before_end


class CostInfo(BaseModel):
    unit_cost: Decimal = Field(..., gt=0, decimal_places=4)
    currency: Currency = Field(default=Currency.USD)
    minimum_order_quantity: Decimal = Field(..., gt=0)
    bulk_discount_threshold: Optional[Decimal] = Field(None, gt=0)
    bulk_discount_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    last_price_update: Optional[date] = None

    @model_validator(mode="after")
    def validate_bulk_discount(self) -> "CostInfo":
        has_threshold = self.bulk_discount_threshold is not None
        has_discount = self.bulk_discount_percent is not None
        if has_threshold != has_discount:
            raise ValueError(
                "Both bulk_discount_threshold and bulk_discount_percent must be provided together"
            )
        return self

    def effective_unit_cost(self, quantity: Decimal) -> Decimal:
        """Returns unit cost after applying bulk discount if applicable."""
        if (
            self.bulk_discount_threshold
            and self.bulk_discount_percent
            and quantity >= self.bulk_discount_threshold
        ):
            discount = self.unit_cost * (self.bulk_discount_percent / Decimal("100"))
            return self.unit_cost - discount
        return self.unit_cost


class QuantityInfo(BaseModel):
    base_unit_of_measure: UnitOfMeasure = "Each"
    packaging_type: Optional[str] = None
    units_per_case: Optional[float] = Field(default=None, gt=0)
    units_per_pallet: Optional[float] = Field(default=None, gt=0)
    alternate_uoms: Optional[list[Any]] = None  # AlternateUom handled via dict in DB

    model_config = {"str_strip_whitespace": True}


class MaterialSpec(BaseModel):
    weight_kg: Optional[float] = Field(default=None, ge=0)
    volume_liters: Optional[float] = Field(default=None, ge=0)
    length_cm: Optional[float] = Field(default=None, ge=0)
    width_cm: Optional[float] = Field(default=None, ge=0)
    height_cm: Optional[float] = Field(default=None, ge=0)

    model_config = {"str_strip_whitespace": True}


class ProductComponent(BaseModel):
    component_id: Optional[str] = Field(
        None,
        min_length=1,
        description="System-generated product component identifier.",
    )
    product_id: Optional[str] = Field(
        None,
        min_length=1,
        description="Parent product identifier for this component entry.",
    )
    part_number: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[A-Z0-9\-_]+$",
        description="Unique alphanumeric part identifier (uppercase, hyphens, underscores allowed)",
    )
    part_name: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    category: PartCategory
    is_active: bool = Field(default=True)
    created_date: date = Field(default_factory=date.today)
    notes: Optional[str] = Field(None, max_length=2000)

    # asset tag
    asset_tag: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    upc: Optional[str] = None
    ean: Optional[str] = None
    cas_number: Optional[str] = None

    # drawing association
    revision: str = Field(
        default="A", max_length=10, description="Part revision/version"
    )
    drawing_number: Optional[str] = Field(None, max_length=50)

    quantity: QuantityInfo
    cost: CostInfo
    supplier_name: str
    material_spec: MaterialSpec

    # ── Manufacturing-specific fields ─────────────────────────────────────────
    scrap_and_yield: ScrapAndYield = Field(
        default_factory=ScrapAndYield,
        description="Scrap factors and process yield rates for consumption calculations.",
    )
    reference_designators: List[ReferenceDesignator] = Field(
        default_factory=list,
        description="PCB/electronics reference designators (e.g. R1, C12, U3A).",
    )
    shelf_life: Optional[ShelfLifeInfo] = Field(
        None,
        description="Shelf-life, floor-life, MSL, and storage condition requirements.",
    )
    hazmat: HazmatInfo = Field(
        default_factory=HazmatInfo,
        description="Hazardous material classification and RoHS/REACH compliance flags.",
    )
    ecn_history: List[ECNRecord] = Field(
        default_factory=list,
        description="Engineering Change Notices that have affected this part.",
    )
    parent_bom_id: Optional[str] = Field(
        None,
        max_length=50,
        description="Part number of the immediate parent assembly in the BOM.",
    )
    bom_level: Optional[int] = Field(
        None,
        ge=1,
        description="BOM depth level (1 = top-level assembly, 2 = subassembly, ...).",
    )
    quantity_per_assembly: Optional[Decimal] = Field(
        None,
        gt=0,
        description="Net quantity of this part required per one parent assembly.",
    )
    find_number: Optional[int] = Field(
        None,
        ge=1,
        description="Find number / item sequence on the engineering drawing BOM table.",
    )
    relationship_type: BOMRelationshipType = Field(default=BOMRelationshipType.ASSEMBLY)
    is_configurable: bool = Field(
        default=False,
        description="Part may be substituted based on product configuration options.",
    )
    substitute_part_numbers: List[str] = Field(
        default_factory=list,
        description="Approved alternate part numbers that may be used in place of this part.",
    )
    effective_date_start: Optional[date] = Field(
        None,
        description="Date from which this BOM relationship is valid.",
    )
    effective_date_end: Optional[date] = Field(
        None,
        description="Date after which this BOM relationship is superseded.",
    )
    bom_notes: Optional[str] = Field(None, max_length=500)

    model_config = {"str_strip_whitespace": True}

    @field_validator("part_number", mode="before")
    @classmethod
    def uppercase_part_number(cls, v: str) -> str:
        return v.upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def validate_bom_effectivity(self) -> "ProductComponent":
        s, e = self.effective_date_start, self.effective_date_end
        if s and e and s >= e:
            raise ValueError(
                "effective_date_start must be earlier than effective_date_end"
            )
        return self

    # ── Helpers ───────────────────────────────────────────────────────────────

    def total_stock_value(self) -> Decimal:
        """Returns total value of on-hand inventory."""
        return self.quantity.quantity_on_hand * self.cost.unit_cost

    def reorder_cost_estimate(self) -> Decimal:
        """Returns estimated cost of a reorder."""
        qty = self.quantity.reorder_quantity
        unit = self.cost.effective_unit_cost(qty)
        return qty * unit

    def gross_quantity_needed(self, net_quantity: Decimal) -> Decimal:
        """
        Returns how much of this part must be issued to a work order
        to obtain `net_quantity` good units, after scrap and yield losses.
        """
        return self.scrap_and_yield.gross_quantity_for(net_quantity)

    @property
    def quantity_per_parent(self) -> float:
        """Compatibility alias for BOM node quantity resolution."""
        if self.quantity_per_assembly is None:
            return 1.0
        return float(self.quantity_per_assembly)

    @property
    def latest_ecn(self) -> Optional[ECNRecord]:
        """Most recently initiated ECN, or None."""
        if not self.ecn_history:
            return None
        return max(self.ecn_history, key=lambda e: e.date_initiated)

    @property
    def active_bom_links(self) -> List[BOMLink]:
        """Compatibility view over the flattened BOM input fields."""
        if not (
            self.parent_bom_id
            and self.bom_level is not None
            and self.quantity_per_assembly is not None
        ):
            return []
        link = BOMLink(
            parent_bom_id=self.parent_bom_id,
            bom_level=self.bom_level,
            quantity_per_assembly=self.quantity_per_assembly,
            find_number=self.find_number,
            relationship_type=self.relationship_type,
            is_configurable=self.is_configurable,
            substitute_part_numbers=self.substitute_part_numbers,
            effective_date_start=self.effective_date_start,
            effective_date_end=self.effective_date_end,
            notes=self.bom_notes,
        )
        return [link] if link.is_currently_effective else []

    @property
    def is_hazardous(self) -> bool:
        return self.hazmat.is_hazardous

    @property
    def has_reference_designators(self) -> bool:
        return len(self.reference_designators) > 0


class BOMComponent(BaseModel):
    """
    A single line-item inside a BOM.

    Fields merged from the production schema, multilevel tree support,
    and MBOM enrichment fields.
    """

    line_id: str = Field(
        ..., min_length=1, description="Unique line identifier within this BOM"
    )
    component_product_id: str = Field(
        ..., min_length=1, description="FK to the product catalogue"
    )
    component_sku: str = Field(default="")
    component_name: str = Field(default="")
    component_type: BOMComponentType
    quantity_per_parent: float = Field(..., gt=0)
    uom: str = Field(
        ..., min_length=1, description="Unit of measure, e.g. 'Each', 'kg'"
    )
    usage_type: UsageType = UsageType.STANDARD
    scrap_rate_pct: Optional[float] = Field(default=None, ge=0)
    sequence_number: int = Field(..., ge=0, description="Assembly sequence order")
    operation_step: Optional[int] = Field(
        default=None, ge=0, description="Work-centre operation ref"
    )
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    material: Optional[str] = Field(
        default=None,
        description="Raw material spec, e.g. 'Western Red Cedar (1/4\" thick)'",
    )
    lead_time: Optional[LeadTimeStatus] = None
    supplier: Optional[Supplier] = None
    notes: Optional[str] = None
    children: list["BOMNode"] = Field(
        default_factory=list,
        description="Sub-components; non-empty means this component is itself an assembly.",
    )

    @model_validator(mode="after")
    def effective_to_after_from(self) -> "BOMComponent":
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must be on or after effective_from")
        return self

    @property
    def is_assembly(self) -> bool:
        """True when this component contains sub-components (non-leaf node)."""
        return len(self.children) > 0

    @property
    def net_quantity(self) -> float:
        """
        Quantity adjusted for scrap rate:
            net = quantity_per_parent / (1 - scrap_rate_pct / 100)
        Falls back to quantity_per_parent when scrap_rate_pct is unset.
        """
        if self.scrap_rate_pct:
            return self.quantity_per_parent / (1 - self.scrap_rate_pct / 100)
        return self.quantity_per_parent


class BOMNode(BaseModel):
    """
    Positional wrapper around a BOMComponent.

    Allows the same component to appear at multiple positions in the tree
    with independent position labels and quantity overrides without
    duplicating the component definition.
    """

    component: BOMComponent
    level: int = Field(default=0, ge=0, description="Depth in the BOM tree (0 = root)")
    position: Optional[str] = Field(
        default=None,
        description="Dotted find-number within the parent assembly, e.g. '1.2.3'",
    )
    quantity_override: Optional[float] = Field(
        default=None,
        gt=0,
        description=(
            "Overrides component.quantity_per_parent for this specific "
            "parent-context without modifying the master component record."
        ),
    )

    @property
    def effective_quantity(self) -> float:
        """Resolved quantity at this node position."""
        return (
            self.quantity_override
            if self.quantity_override is not None
            else self.component.quantity_per_parent
        )


class BOMBase(BaseModel):
    """Core BOM fields shared by Create, Update, and Record variants."""

    bom_id: str = Field(..., min_length=1)
    bom_code: str = Field(..., min_length=1)
    version: str = Field(default="1.0", min_length=1)
    parent_product_id: str = Field(..., min_length=1)
    parent_product_name: str = ""
    parent_sku: str = ""
    bom_type: BOMType = BOMType.MANUFACTURING
    status: BOMStatus = BOMStatus.DRAFT
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    base_quantity: float = Field(default=1, gt=0)
    base_uom: str = Field(default="Each", min_length=1)
    routing_id: Optional[str] = None
    approved_process_definition_id: Optional[str] = Field(
        default=None,
        description="Approved manufacturing/packaging process definition this BOM is authorized for.",
    )
    approved_process_definition_revision: Optional[str] = Field(
        default=None,
        description="Revision of the approved process definition captured when this BOM was released.",
    )
    approved_process_route_id: Optional[str] = Field(
        default=None,
        description="Approved process route/version used with this BOM.",
    )
    mbmr_template_id: Optional[str] = Field(
        default=None,
        description="Master Batch Manufacturing Record template bound to this BOM.",
    )
    bmr_template_id: Optional[str] = Field(
        default=None,
        description="Batch Manufacturing Record template generated/executed from this BOM.",
    )
    bpr_template_id: Optional[str] = Field(
        default=None,
        description="Batch Packaging Record template used when this BOM covers packaging.",
    )
    batch_execution_record_ids: list[str] = Field(
        default_factory=list,
        description="Batch execution records that have executed or instantiated this BOM.",
    )
    standard_work_hours: Optional[float] = Field(default=None, ge=0)
    overall_scrap_rate_pct: Optional[float] = Field(default=None, ge=0)
    expected_yield_pct: Optional[float] = Field(default=None, ge=0, le=100)
    components: list[BOMComponent] = Field(default_factory=list)
    revision_notes: Optional[str] = None
    engineering_change_no: Optional[str] = None
    remarks: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def expiry_after_effective(self) -> "BOMBase":
        if (
            self.effective_date
            and self.expiry_date
            and self.expiry_date < self.effective_date
        ):
            raise ValueError("expiry_date must be on or after effective_date")
        if self.status == BOMStatus.ACTIVE:
            if not (
                self.approved_process_definition_id
                or self.approved_process_route_id
                or self.routing_id
            ):
                raise ValueError(
                    "active BOMs must be bound to an approved process definition or route"
                )
            if not (
                self.mbmr_template_id or self.bmr_template_id or self.bpr_template_id
            ):
                raise ValueError(
                    "active BOMs must be bound to an MBMR, BMR, or BPR template"
                )
        return self


class BOMRecord(BOMBase):
    """
    Persisted BOM document with audit data and computed tree metrics.
    """

    is_current_version: bool = False
    approval_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    estimated_material_cost: Optional[float] = None
    total_component_lines: int = 0
    is_multi_level: bool = False
    has_alternates: bool = False
    is_valid: bool = False
    is_production_bound: bool = False
    raw_materials: list[BOMComponent] = Field(
        default_factory=list,
        description="All raw material component lines in this BOM, collected across every BOM level.",
    )

    @model_validator(mode="after")
    def normalize_and_compute(self) -> "BOMRecord":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.approval_date = ensure_utc(self.approval_date)

        all_components = self._walk_all_components()
        self.total_component_lines = len(all_components)
        self.is_multi_level = any(c.is_assembly for c in self.components)
        self.has_alternates = any(
            c.usage_type in {UsageType.ALTERNATE, UsageType.SUBSTITUTE}
            for c in all_components
        )
        self.raw_materials = [
            component
            for component in all_components
            if component.component_type == BOMComponentType.RAW_MATERIAL
        ]

        today = datetime.now(timezone.utc).date()
        effective = self.effective_date is None or self.effective_date <= today
        unexpired = self.expiry_date is None or self.expiry_date >= today
        self.is_valid = self.status == BOMStatus.ACTIVE and effective and unexpired
        self.is_production_bound = bool(
            self.approved_process_definition_id
            or self.approved_process_route_id
            or self.routing_id
        ) and bool(
            self.mbmr_template_id or self.bmr_template_id or self.bpr_template_id
        )

        return self

    def _walk_all_components(self) -> list[BOMComponent]:
        """DFS walk returning every component in the BOM tree."""
        result: list[BOMComponent] = []

        def _recurse(component: BOMComponent) -> None:
            result.append(component)
            for node in component.children:
                _recurse(node.component)

        for component in self.components:
            _recurse(component)
        return result

    def flat_list(self) -> list[tuple[int, str, BOMComponent]]:
        """DFS walk as (level, position, component) tuples."""
        result: list[tuple[int, str, BOMComponent]] = []

        def _recurse(component: BOMComponent, level: int, position: str) -> None:
            result.append((level, position, component))
            for idx, node in enumerate(component.children, start=1):
                child_position = node.position or f"{position}.{idx}"
                _recurse(node.component, level + 1, child_position)

        for idx, component in enumerate(self.components, start=1):
            _recurse(component, 0, str(idx))
        return result

    def find_component(self, *, product_id: str) -> list[BOMComponent]:
        """Find every component with the given product id, regardless of depth."""
        return [
            component
            for component in self._walk_all_components()
            if component.component_product_id == product_id
        ]

    def max_depth(self) -> int:
        """Maximum nesting depth, where 0 is a flat BOM."""
        depths: list[int] = []

        def _recurse(component: BOMComponent, depth: int) -> None:
            depths.append(depth)
            for node in component.children:
                _recurse(node.component, depth + 1)

        for component in self.components:
            _recurse(component, 0)
        return max(depths, default=0)


class BOMCreate(BOMBase):
    """Payload for creating a new BOM."""

    created_by: Optional[str] = None


class BOMUpdate(BaseModel):
    """Partial-update payload for stored BOM records."""

    bom_code: Optional[str] = None
    version: Optional[str] = None
    parent_product_id: Optional[str] = None
    parent_product_name: Optional[str] = None
    parent_sku: Optional[str] = None
    bom_type: Optional[BOMType] = None
    status: Optional[BOMStatus] = None
    is_current_version: Optional[bool] = None
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    approval_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    base_quantity: Optional[float] = Field(default=None, gt=0)
    base_uom: Optional[str] = None
    routing_id: Optional[str] = None
    approved_process_definition_id: Optional[str] = None
    approved_process_definition_revision: Optional[str] = None
    approved_process_route_id: Optional[str] = None
    mbmr_template_id: Optional[str] = None
    bmr_template_id: Optional[str] = None
    bpr_template_id: Optional[str] = None
    batch_execution_record_ids: Optional[list[str]] = None
    standard_work_hours: Optional[float] = Field(default=None, ge=0)
    overall_scrap_rate_pct: Optional[float] = Field(default=None, ge=0)
    expected_yield_pct: Optional[float] = Field(default=None, ge=0, le=100)
    components: Optional[list[BOMComponent]] = None
    revision_notes: Optional[str] = None
    engineering_change_no: Optional[str] = None
    remarks: Optional[str] = None
    attachments: Optional[list[str]] = None
    estimated_material_cost: Optional[float] = None
    updated_by: Optional[str] = None


class PaginatedBOMResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[BOMRecord]


class ProductComponentCreate(ProductComponent):
    pass


class ProductComponentUpdate(BaseModel):
    product_id: Optional[str] = Field(None, min_length=1)
    part_number: Optional[str] = Field(
        None,
        min_length=3,
        max_length=50,
        pattern=r"^[A-Z0-9\-_]+$",
        description="Unique alphanumeric part identifier (uppercase, hyphens, underscores allowed)",
    )
    part_name: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    category: Optional[PartCategory] = None
    is_active: Optional[bool] = None
    created_date: Optional[date] = None
    notes: Optional[str] = None
    asset_tag: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    upc: Optional[str] = None
    ean: Optional[str] = None
    cas_number: Optional[str] = None
    revision: Optional[str] = Field(None, max_length=10)
    drawing_number: Optional[str] = Field(None, max_length=50)
    quantity: Optional[QuantityInfo] = None
    cost: Optional[CostInfo] = None
    supplier_name: Optional[str] = None
    material_spec: Optional[MaterialSpec] = None
    scrap_and_yield: Optional[ScrapAndYield] = None
    reference_designators: Optional[List[ReferenceDesignator]] = Field(
        None,
        description="PCB/electronics reference designators (e.g. R1, C12, U3A).",
    )
    shelf_life: Optional[ShelfLifeInfo] = None
    hazmat: Optional[HazmatInfo] = None
    ecn_history: Optional[List[ECNRecord]] = Field(
        None,
        description="Engineering Change Notices that have affected this part.",
    )
    parent_bom_id: Optional[str] = Field(
        None,
        max_length=50,
        description="Part number of the immediate parent assembly in the BOM.",
    )
    bom_level: Optional[int] = Field(
        None,
        ge=1,
        description="BOM depth level (1 = top-level assembly, 2 = subassembly, ...).",
    )
    quantity_per_assembly: Optional[Decimal] = Field(
        None,
        gt=0,
        description="Net quantity of this part required per one parent assembly.",
    )
    find_number: Optional[int] = Field(
        None,
        ge=1,
        description="Find number / item sequence on the engineering drawing BOM table.",
    )
    relationship_type: Optional[BOMRelationshipType] = None
    is_configurable: Optional[bool] = Field(
        None,
        description="Part may be substituted based on product configuration options.",
    )
    substitute_part_numbers: Optional[List[str]] = Field(
        None,
        description="Approved alternate part numbers that may be used in place of this part.",
    )
    effective_date_start: Optional[date] = Field(
        None,
        description="Date from which this BOM relationship is valid.",
    )
    effective_date_end: Optional[date] = Field(
        None,
        description="Date after which this BOM relationship is superseded.",
    )
    bom_notes: Optional[str] = Field(None, max_length=500)

    @field_validator("part_number", mode="before")
    @classmethod
    def uppercase_part_number(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def validate_bom_effectivity(self) -> "ProductComponentUpdate":
        s, e = self.effective_date_start, self.effective_date_end
        if s and e and s >= e:
            raise ValueError(
                "effective_date_start must be earlier than effective_date_end"
            )
        return self


class PaginatedProductComponentResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProductComponent]


ProductComponentRecord = ProductComponent
