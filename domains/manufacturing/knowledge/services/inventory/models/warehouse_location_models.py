"""
warehouse_location_models.py
----------------------------
Pydantic models for a 6-level warehouse location hierarchy:

    Lot -> Zone -> Area -> Aisle -> Rack -> Bin

Each level is a standalone model with its own identity, status, capacity,
and dimensional fields. Higher levels embed references (IDs) to their
parent; the aggregate models (WarehouseLocation, LocationTree) compose
the full hierarchy for reads / API responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

LocationStatus = Literal[
    "Active", "Inactive", "Under-Maintenance", "Reserved", "Blocked"
]

StorageType = Literal[
    "Ambient",
    "Cold-Storage",
    "Frozen",
    "Hazardous",
    "High-Value",
    "Bulk",
    "Rack-Storage",
    "Floor-Storage",
    "Mezzanine",
]

TemperatureZone = Literal["Ambient", "Chilled", "Frozen", "Controlled"]

BinType = Literal[
    "Standard",
    "Oversized",
    "Small-Parts",
    "Liquid",
    "Fragile",
    "Hazmat",
    "Returns",
    "QC-Hold",
    "Staging",
]

Orientation = Literal["Horizontal", "Vertical", "Angled"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _LocationBase(BaseModel):
    """Fields common to every level of the hierarchy."""

    code: str = Field(
        ..., min_length=1, description="Short mnemonic code, e.g. 'LOT-A'"
    )
    name: str = Field(..., min_length=1)
    status: LocationStatus = "Active"
    description: Optional[str] = None
    tags: list[str] = Field(
        default_factory=list, description="Free-form labels for search / filtering"
    )
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @property
    def is_available(self) -> bool:
        return self.is_active and self.status == "Active"


class _CapacityMixin(BaseModel):
    """Weight / volume capacity tracking, mixed into capacity-bearing levels."""

    max_weight_kg: Optional[float] = Field(default=None, ge=0)
    current_weight_kg: float = Field(default=0.0, ge=0)
    max_volume_m3: Optional[float] = Field(default=None, ge=0)
    current_volume_m3: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def weight_within_capacity(self) -> "_CapacityMixin":
        if (
            self.max_weight_kg is not None
            and self.current_weight_kg > self.max_weight_kg
        ):
            raise ValueError(
                f"current_weight_kg ({self.current_weight_kg}) exceeds "
                f"max_weight_kg ({self.max_weight_kg})"
            )
        if (
            self.max_volume_m3 is not None
            and self.current_volume_m3 > self.max_volume_m3
        ):
            raise ValueError(
                f"current_volume_m3 ({self.current_volume_m3}) exceeds "
                f"max_volume_m3 ({self.max_volume_m3})"
            )
        return self

    @property
    def weight_utilization_pct(self) -> Optional[float]:
        if self.max_weight_kg:
            return round(self.current_weight_kg / self.max_weight_kg * 100, 2)
        return None

    @property
    def volume_utilization_pct(self) -> Optional[float]:
        if self.max_volume_m3:
            return round(self.current_volume_m3 / self.max_volume_m3 * 100, 2)
        return None


class _DimensionMixin(BaseModel):
    """Physical dimensions in metres, mixed into spatially-defined levels."""

    length_m: Optional[float] = Field(default=None, ge=0)
    width_m: Optional[float] = Field(default=None, ge=0)
    height_m: Optional[float] = Field(default=None, ge=0)

    @property
    def floor_area_m2(self) -> Optional[float]:
        if self.length_m and self.width_m:
            return round(self.length_m * self.width_m, 4)
        return None

    @property
    def volume_m3(self) -> Optional[float]:
        if self.length_m and self.width_m and self.height_m:
            return round(self.length_m * self.width_m * self.height_m, 4)
        return None


class Lot(_LocationBase, _DimensionMixin, _CapacityMixin):
    """Largest physical unit: a fenced plot, building, or outdoor storage area."""

    lot_id: str = Field(..., min_length=1)
    warehouse_id: str = Field(
        ..., min_length=1, description="Parent warehouse / site FK"
    )
    storage_type: StorageType = "Ambient"
    temperature_zone: TemperatureZone = "Ambient"
    is_outdoor: bool = False
    security_level: Literal["Open", "Restricted", "High-Security"] = "Open"
    address: Optional[str] = None
    gps_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    gps_lng: Optional[float] = Field(default=None, ge=-180, le=180)


class Zone(_LocationBase, _DimensionMixin, _CapacityMixin):
    """Functional sub-division of a Lot."""

    zone_id: str = Field(..., min_length=1)
    lot_id: str = Field(..., min_length=1, description="Parent Lot FK")
    zone_type: Literal[
        "Receiving",
        "Shipping",
        "Picking",
        "Bulk-Storage",
        "Cross-Dock",
        "Returns",
        "Quarantine",
        "Value-Added-Services",
    ] = "Bulk-Storage"
    storage_type: StorageType = "Ambient"
    temperature_zone: TemperatureZone = "Ambient"
    is_temperature_controlled: bool = False
    allows_hazmat: bool = False
    fire_suppression: bool = False


class Area(_LocationBase, _DimensionMixin, _CapacityMixin):
    """Named section within a Zone, grouped by product category or pick strategy."""

    area_id: str = Field(..., min_length=1)
    zone_id: str = Field(..., min_length=1, description="Parent Zone FK")
    storage_type: StorageType = "Rack-Storage"
    velocity_class: Literal["A", "B", "C", "D", "Unclassified"] = "Unclassified"
    product_category: Optional[str] = None
    picking_method: Literal["Manual", "Forklift", "AGV", "Conveyor", "Mixed"] = "Manual"
    requires_ppe: bool = False


class Aisle(_LocationBase):
    """A corridor between rows of racks inside an Area."""

    aisle_id: str = Field(..., min_length=1)
    area_id: str = Field(..., min_length=1, description="Parent Area FK")
    aisle_number: int = Field(..., ge=1)
    orientation: Orientation = "Horizontal"
    width_m: Optional[float] = Field(
        default=None, ge=0, description="Clear aisle width"
    )
    length_m: Optional[float] = Field(default=None, ge=0)
    allows_forklift: bool = False
    is_one_way: bool = False
    lighting_lux: Optional[float] = Field(default=None, ge=0)


class Rack(_LocationBase, _DimensionMixin, _CapacityMixin):
    """A physical shelving structure within an Aisle."""

    rack_id: str = Field(..., min_length=1)
    aisle_id: str = Field(..., min_length=1, description="Parent Aisle FK")
    rack_number: int = Field(..., ge=1)
    rack_type: Literal[
        "Selective",
        "Drive-In",
        "Push-Back",
        "Pallet-Flow",
        "Cantilever",
        "Mezzanine",
        "Mobile",
        "Carton-Flow",
    ] = "Selective"
    total_levels: int = Field(..., ge=1, description="Number of vertical shelf levels")
    side: Literal["Left", "Right", "Center", "N/A"] = "N/A"
    is_double_deep: bool = False
    max_weight_per_level_kg: Optional[float] = Field(default=None, ge=0)
    material: Literal["Steel", "Aluminum", "Wood", "Composite"] = "Steel"
    install_date: Optional[datetime] = None


class Bin(_LocationBase, _DimensionMixin, _CapacityMixin):
    """The atomic storage cell: one slot on one shelf level of one rack."""

    bin_id: str = Field(..., min_length=1)
    rack_id: str = Field(..., min_length=1, description="Parent Rack FK")
    level: int = Field(..., ge=1, description="Vertical level (1 = floor level)")
    column: int = Field(..., ge=1, description="Horizontal column within the rack")
    level_label: str = Field(default="", description="Human label e.g. 'A', 'B', 'C'")
    bin_type: BinType = "Standard"
    is_occupied: bool = False
    is_pickable: bool = True
    is_replenishment_source: bool = False
    current_sku: Optional[str] = None
    current_qty: float = Field(default=0.0, ge=0)
    lot_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    last_counted_at: Optional[datetime] = None
    last_movement_at: Optional[datetime] = None
    lot_code: str = ""
    zone_code: str = ""
    area_code: str = ""
    aisle_code: str = ""
    rack_code: str = ""

    @model_validator(mode="after")
    def occupied_requires_sku(self) -> "Bin":
        if self.is_occupied and not self.current_sku:
            raise ValueError("is_occupied=True requires current_sku to be set")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def bin_address(self) -> str:
        parts = [
            self.lot_code,
            self.zone_code,
            self.area_code,
            self.aisle_code,
            self.rack_code,
            self.code,
        ]
        filled = [part for part in parts if part]
        return " / ".join(filled) if filled else self.code

    @property
    def is_empty(self) -> bool:
        return not self.is_occupied and self.current_qty == 0


class WarehouseLocation(BaseModel):
    """Flat denormalised view of a bin's full ancestry."""

    bin: Bin
    rack: Rack
    aisle: Aisle
    area: Area
    zone: Zone
    lot: Lot

    @computed_field  # type: ignore[misc]
    @property
    def full_address(self) -> str:
        return (
            f"{self.lot.code} / {self.zone.code} / {self.area.code} / "
            f"{self.aisle.code} / {self.rack.code} / {self.bin.code}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def is_available(self) -> bool:
        """True only when every ancestor level is Active and the bin is free."""
        return (
            self.lot.is_available
            and self.zone.is_available
            and self.area.is_available
            and self.aisle.is_available
            and self.rack.is_available
            and self.bin.is_available
            and not self.bin.is_occupied
        )


class LocationTree(BaseModel):
    """Hierarchical tree rooted at a Lot."""

    lot: Lot
    zones: list[ZoneTree] = Field(default_factory=list)

    @property
    def total_bins(self) -> int:
        return sum(zone.total_bins for zone in self.zones)

    @property
    def available_bins(self) -> int:
        return sum(zone.available_bins for zone in self.zones)


class ZoneTree(BaseModel):
    zone: Zone
    areas: list[AreaTree] = Field(default_factory=list)

    @property
    def total_bins(self) -> int:
        return sum(area.total_bins for area in self.areas)

    @property
    def available_bins(self) -> int:
        return sum(area.available_bins for area in self.areas)


class AreaTree(BaseModel):
    area: Area
    aisles: list[AisleTree] = Field(default_factory=list)

    @property
    def total_bins(self) -> int:
        return sum(aisle.total_bins for aisle in self.aisles)

    @property
    def available_bins(self) -> int:
        return sum(aisle.available_bins for aisle in self.aisles)


class AisleTree(BaseModel):
    aisle: Aisle
    racks: list[RackTree] = Field(default_factory=list)

    @property
    def total_bins(self) -> int:
        return sum(rack.total_bins for rack in self.racks)

    @property
    def available_bins(self) -> int:
        return sum(rack.available_bins for rack in self.racks)


class RackTree(BaseModel):
    rack: Rack
    bins: list[Bin] = Field(default_factory=list)

    @property
    def total_bins(self) -> int:
        return len(self.bins)

    @property
    def available_bins(self) -> int:
        return sum(
            1 for bin_ in self.bins if not bin_.is_occupied and bin_.is_available
        )

    @property
    def occupancy_pct(self) -> float:
        if not self.bins:
            return 0.0
        occupied = sum(1 for bin_ in self.bins if bin_.is_occupied)
        return round(occupied / len(self.bins) * 100, 2)


LocationTree.model_rebuild()
ZoneTree.model_rebuild()
AreaTree.model_rebuild()
AisleTree.model_rebuild()
RackTree.model_rebuild()


class LotCreate(BaseModel):
    lot_id: str
    warehouse_id: str
    code: str
    name: str
    storage_type: StorageType = "Ambient"
    temperature_zone: TemperatureZone = "Ambient"
    status: LocationStatus = "Active"
    description: Optional[str] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    max_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    is_outdoor: bool = False
    security_level: Literal["Open", "Restricted", "High-Security"] = "Open"
    address: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class LotUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[LocationStatus] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    storage_type: Optional[StorageType] = None
    temperature_zone: Optional[TemperatureZone] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    max_weight_kg: Optional[float] = None
    current_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    current_volume_m3: Optional[float] = None
    is_outdoor: Optional[bool] = None
    security_level: Optional[Literal["Open", "Restricted", "High-Security"]] = None
    address: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class ZoneCreate(BaseModel):
    zone_id: str
    lot_id: str
    code: str
    name: str
    zone_type: Literal[
        "Receiving",
        "Shipping",
        "Picking",
        "Bulk-Storage",
        "Cross-Dock",
        "Returns",
        "Quarantine",
        "Value-Added-Services",
    ] = "Bulk-Storage"
    storage_type: StorageType = "Ambient"
    temperature_zone: TemperatureZone = "Ambient"
    status: LocationStatus = "Active"
    description: Optional[str] = None
    is_temperature_controlled: bool = False
    allows_hazmat: bool = False


class ZoneUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[LocationStatus] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    zone_type: Optional[
        Literal[
            "Receiving",
            "Shipping",
            "Picking",
            "Bulk-Storage",
            "Cross-Dock",
            "Returns",
            "Quarantine",
            "Value-Added-Services",
        ]
    ] = None
    storage_type: Optional[StorageType] = None
    temperature_zone: Optional[TemperatureZone] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    max_weight_kg: Optional[float] = None
    current_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    current_volume_m3: Optional[float] = None
    is_temperature_controlled: Optional[bool] = None
    allows_hazmat: Optional[bool] = None
    fire_suppression: Optional[bool] = None


class AreaCreate(BaseModel):
    area_id: str
    zone_id: str
    code: str
    name: str
    storage_type: StorageType = "Rack-Storage"
    velocity_class: Literal["A", "B", "C", "D", "Unclassified"] = "Unclassified"
    product_category: Optional[str] = None
    picking_method: Literal["Manual", "Forklift", "AGV", "Conveyor", "Mixed"] = "Manual"
    status: LocationStatus = "Active"
    requires_ppe: bool = False


class AreaUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[LocationStatus] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    storage_type: Optional[StorageType] = None
    velocity_class: Optional[Literal["A", "B", "C", "D", "Unclassified"]] = None
    product_category: Optional[str] = None
    picking_method: Optional[
        Literal["Manual", "Forklift", "AGV", "Conveyor", "Mixed"]
    ] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    max_weight_kg: Optional[float] = None
    current_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    current_volume_m3: Optional[float] = None
    requires_ppe: Optional[bool] = None


class AisleCreate(BaseModel):
    aisle_id: str
    area_id: str
    code: str
    name: str
    aisle_number: int
    orientation: Orientation = "Horizontal"
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    allows_forklift: bool = False
    is_one_way: bool = False
    status: LocationStatus = "Active"


class AisleUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[LocationStatus] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    aisle_number: Optional[int] = None
    orientation: Optional[Orientation] = None
    width_m: Optional[float] = None
    length_m: Optional[float] = None
    allows_forklift: Optional[bool] = None
    is_one_way: Optional[bool] = None
    lighting_lux: Optional[float] = None


class RackCreate(BaseModel):
    rack_id: str
    aisle_id: str
    code: str
    name: str
    rack_number: int
    total_levels: int
    rack_type: Literal[
        "Selective",
        "Drive-In",
        "Push-Back",
        "Pallet-Flow",
        "Cantilever",
        "Mezzanine",
        "Mobile",
        "Carton-Flow",
    ] = "Selective"
    side: Literal["Left", "Right", "Center", "N/A"] = "N/A"
    is_double_deep: bool = False
    max_weight_kg: Optional[float] = None
    max_weight_per_level_kg: Optional[float] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    status: LocationStatus = "Active"


class RackUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[LocationStatus] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    rack_number: Optional[int] = None
    rack_type: Optional[
        Literal[
            "Selective",
            "Drive-In",
            "Push-Back",
            "Pallet-Flow",
            "Cantilever",
            "Mezzanine",
            "Mobile",
            "Carton-Flow",
        ]
    ] = None
    total_levels: Optional[int] = None
    side: Optional[Literal["Left", "Right", "Center", "N/A"]] = None
    is_double_deep: Optional[bool] = None
    max_weight_kg: Optional[float] = None
    current_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    current_volume_m3: Optional[float] = None
    max_weight_per_level_kg: Optional[float] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    material: Optional[Literal["Steel", "Aluminum", "Wood", "Composite"]] = None
    install_date: Optional[datetime] = None


class BinCreate(BaseModel):
    bin_id: str
    rack_id: str
    code: str
    name: str
    level: int
    column: int
    level_label: str = ""
    bin_type: BinType = "Standard"
    max_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    is_pickable: bool = True
    is_replenishment_source: bool = False
    status: LocationStatus = "Active"
    lot_code: str = ""
    zone_code: str = ""
    area_code: str = ""
    aisle_code: str = ""
    rack_code: str = ""


class BinUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    status: Optional[LocationStatus] = None
    is_occupied: Optional[bool] = None
    is_pickable: Optional[bool] = None
    current_sku: Optional[str] = None
    current_qty: Optional[float] = None
    current_weight_kg: Optional[float] = None
    current_volume_m3: Optional[float] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    last_counted_at: Optional[datetime] = None
    last_movement_at: Optional[datetime] = None
    bin_type: Optional[BinType] = None
    level: Optional[int] = None
    column: Optional[int] = None
    level_label: Optional[str] = None
    max_weight_kg: Optional[float] = None
    max_volume_m3: Optional[float] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    is_replenishment_source: Optional[bool] = None
    lot_code: Optional[str] = None
    zone_code: Optional[str] = None
    area_code: Optional[str] = None
    aisle_code: Optional[str] = None
    rack_code: Optional[str] = None


class PaginatedLotResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Lot]


class PaginatedZoneResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Zone]


class PaginatedAreaResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Area]


class PaginatedAisleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Aisle]


class PaginatedRackResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Rack]


class PaginatedBinResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[Bin]


class PaginatedLocationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WarehouseLocation]
