# ─────────────────────────────────────────────
# MATERIAL USED
# ─────────────────────────────────────────────

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class MaterialUsed(BaseModel):

    # ── Identity ──────────────────────────────
    material_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    asset_tag: Optional[str] = None  # e.g. "AST-00142" — physical label on container
    cas_number: Optional[str] = None  # e.g. "7647-01-0" (if applicable)
    material_type: Optional[str] = None  # e.g. "Raw Material", "Excipient", "Solvent"
    description: Optional[str] = None

    # ── Quantity ──────────────────────────────
    grade: Optional[str] = None  # e.g. "Food Grade", "USP", "Technical"
    concentration: Optional[str] = None  # e.g. "2% v/v" (for solutions/mixtures)
    quantity: Optional[float] = Field(None, gt=0)
    quantity_units: Optional[str] = None  # e.g. "mL", "g", "kg", "L"
    current_stock: Optional[float] = Field(None, ge=0)
    minimum_stock: Optional[float] = Field(None, ge=0)
    maximum_stock: Optional[float] = Field(None, ge=0)

    # ── Lot / Expiry ──────────────────────────
    lot_no: Optional[str] = None
    batch_no: Optional[str] = None  # internal manufacturing batch
    expiry_date: Optional[date] = None
    retest_date: Optional[date] = None  # for materials that require periodic retesting
    manufactured_date: Optional[date] = None
    date_received: Optional[date] = None
    date_opened: Optional[date] = None
    use_within_days: Optional[int] = Field(None, gt=0)

    # ── Storage ───────────────────────────────
    storage_temperature: Optional[str] = None  # e.g. "15–25 °C"
    storage_conditions: Optional[str] = None  # e.g. "Store in a dry place"
    container_type: Optional[str] = None  # e.g. "HDPE drum", "IBC tote"

    # ── Packaging ─────────────────────────────
    packaging_type: Optional[str] = None  # e.g. "Drum", "Bag", "Pallet", "IBC"
    packaging_size: Optional[str] = None  # e.g. "25 kg", "200 L"
    packaging_material: Optional[str] = (
        None  # e.g. "HDPE", "Aluminium foil", "PP woven"
    )
    units_per_pack: Optional[int] = Field(None, gt=0)
    pack_quantity: Optional[int] = Field(None, gt=0)
    is_sealed: bool = False
    seal_integrity_ok: Optional[bool] = None  # result of incoming seal inspection

    # ── Handling ──────────────────────────────
    handling_instructions: Optional[str] = None  # e.g. "Avoid contact with moisture"
    max_stack_height: Optional[int] = Field(None, gt=0)  # number of pallets/units
    is_fragile: bool = False
    is_hygroscopic: bool = False  # absorbs moisture — needs sealed storage
    is_light_sensitive: bool = False  # needs opaque/dark packaging
    requires_inert_atmosphere: bool = (
        False  # e.g. nitrogen blanket for reactive materials
    )
    special_handling_notes: Optional[str] = None

    # ── Safety (GHS) ──────────────────────────
    hazard_class: Optional[str] = None  # e.g. "Flammable", "Corrosive"
    ghs_codes: list[str] = Field(default_factory=list)  # e.g. ["H290", "H314"]
    requires_fume_hood: bool = False
    requires_ppe: bool = False
    ppe_details: Optional[str] = None  # e.g. "Nitrile gloves, safety goggles"
    sds_reference: Optional[str] = None  # Safety Data Sheet doc ref or URL
    un_number: Optional[str] = None  # e.g. "UN1263" for transport classification
    transport_class: Optional[str] = None  # e.g. "Class 3 Flammable Liquid"

    # ── Quality ───────────────────────────────
    coa_reference: Optional[str] = None  # Certificate of Analysis doc ref
    coa_date: Optional[date] = None
    approved_by: Optional[str] = None  # QC approver name/ID
    approval_date: Optional[date] = None
    qc_status: Optional[str] = None  # e.g. "Approved", "Quarantine", "Rejected"
    purity_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    moisture_content_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    test_results: Optional[str] = None  # freeform or JSON blob of test values
    appearance: Optional[str] = None  # e.g. "White crystalline powder"
    odour: Optional[str] = None  # e.g. "Odourless"

    # ── Regulatory ────────────────────────────
    is_food_grade: bool = False
    is_pharma_grade: bool = False
    is_kosher: bool = False
    is_halal: bool = False
    is_organic: bool = False
    reach_compliant: Optional[bool] = None  # EU REACH regulation
    rohs_compliant: Optional[bool] = None  # RoHS (electronics/hazardous substances)
    fda_registered: Optional[bool] = None
    certifications: list[str] = Field(default_factory=list)  # e.g. ["ISO 9001", "GMP"]
    regulatory_notes: Optional[str] = None

    # ── Supplier / Procurement ─────────────────
    supplier: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return self.expiry_date is not None and self.expiry_date < date.today()

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expiry_date is None:
            return None
        return (self.expiry_date - date.today()).days

    @property
    def is_retest_due(self) -> bool:
        return self.retest_date is not None and self.retest_date <= date.today()

    @property
    def is_below_minimum_stock(self) -> bool:
        if self.current_stock is None or self.minimum_stock is None:
            return False
        return self.current_stock < self.minimum_stock

    # ── Validators ────────────────────────────

    @model_validator(mode="after")
    def expiry_after_manufacture(self) -> "MaterialUsed":
        if self.manufactured_date and self.expiry_date:
            if self.expiry_date <= self.manufactured_date:
                raise ValueError("expiry_date must be after manufactured_date")
        return self

    @model_validator(mode="after")
    def opened_before_expiry(self) -> "MaterialUsed":
        if self.date_opened and self.expiry_date:
            if self.date_opened > self.expiry_date:
                raise ValueError("date_opened cannot be after expiry_date")
        return self

    @model_validator(mode="after")
    def received_before_expiry(self) -> "MaterialUsed":
        if self.date_received and self.expiry_date:
            if self.date_received > self.expiry_date:
                raise ValueError("date_received cannot be after expiry_date")
        return self

    @model_validator(mode="after")
    def retest_before_expiry(self) -> "MaterialUsed":
        if self.retest_date and self.expiry_date:
            if self.retest_date > self.expiry_date:
                raise ValueError("retest_date cannot be after expiry_date")
        return self

    @model_validator(mode="after")
    def stock_levels_consistent(self) -> "MaterialUsed":
        if self.minimum_stock is not None and self.maximum_stock is not None:
            if self.minimum_stock >= self.maximum_stock:
                raise ValueError("minimum_stock must be less than maximum_stock")
        return self

    @model_validator(mode="after")
    def approval_date_requires_approver(self) -> "MaterialUsed":
        if self.approval_date and not self.approved_by:
            raise ValueError("approval_date requires approved_by to be set")
        return self


class MaterialUsedCreate(MaterialUsed):
    pass


class MaterialUsedUpdate(BaseModel):
    name: Optional[str] = None
    asset_tag: Optional[str] = None
    cas_number: Optional[str] = None
    material_type: Optional[str] = None
    description: Optional[str] = None

    grade: Optional[str] = None
    concentration: Optional[str] = None
    quantity: Optional[float] = Field(None, gt=0)
    quantity_units: Optional[str] = None

    lot_no: Optional[str] = None
    batch_no: Optional[str] = None
    expiry_date: Optional[date] = None
    retest_date: Optional[date] = None
    manufactured_date: Optional[date] = None
    date_received: Optional[date] = None
    date_opened: Optional[date] = None
    use_within_days: Optional[int] = Field(None, gt=0)

    storage_location: Optional[str] = None
    storage_temperature: Optional[str] = None
    storage_conditions: Optional[str] = None
    container_type: Optional[str] = None

    packaging_type: Optional[str] = None
    packaging_size: Optional[str] = None
    packaging_material: Optional[str] = None
    units_per_pack: Optional[int] = Field(None, gt=0)
    pack_quantity: Optional[int] = Field(None, gt=0)
    is_sealed: Optional[bool] = None
    seal_integrity_ok: Optional[bool] = None

    handling_instructions: Optional[str] = None
    max_stack_height: Optional[int] = Field(None, gt=0)
    is_fragile: Optional[bool] = None
    is_hygroscopic: Optional[bool] = None
    is_light_sensitive: Optional[bool] = None
    requires_inert_atmosphere: Optional[bool] = None
    special_handling_notes: Optional[str] = None

    hazard_class: Optional[str] = None
    ghs_codes: Optional[list[str]] = None
    requires_fume_hood: Optional[bool] = None
    requires_ppe: Optional[bool] = None
    ppe_details: Optional[str] = None
    sds_reference: Optional[str] = None
    un_number: Optional[str] = None
    transport_class: Optional[str] = None

    coa_reference: Optional[str] = None
    coa_date: Optional[date] = None
    approved_by: Optional[str] = None
    approval_date: Optional[date] = None
    qc_status: Optional[str] = None
    purity_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    moisture_content_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    test_results: Optional[str] = None
    appearance: Optional[str] = None
    odour: Optional[str] = None

    is_food_grade: Optional[bool] = None
    is_pharma_grade: Optional[bool] = None
    is_kosher: Optional[bool] = None
    is_halal: Optional[bool] = None
    is_organic: Optional[bool] = None
    reach_compliant: Optional[bool] = None
    rohs_compliant: Optional[bool] = None
    fda_registered: Optional[bool] = None
    certifications: Optional[list[str]] = None
    regulatory_notes: Optional[str] = None

    supplier: Optional[str] = None
    catalog_no: Optional[str] = None
    purchase_order: Optional[str] = None
    country_of_origin: Optional[str] = None
    lead_time_days: Optional[int] = Field(None, gt=0)
    approved_vendor: Optional[bool] = None

    current_stock: Optional[float] = Field(None, ge=0)
    stock_units: Optional[str] = None
    minimum_stock: Optional[float] = Field(None, ge=0)
    maximum_stock: Optional[float] = Field(None, ge=0)
    reorder_quantity: Optional[float] = Field(None, gt=0)
    bin_location: Optional[str] = None
    is_active: Optional[bool] = None


class MaterialUsedResponse(MaterialUsed):
    class Config:
        from_attributes = True


class PaginatedMaterialUsedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MaterialUsedResponse]
