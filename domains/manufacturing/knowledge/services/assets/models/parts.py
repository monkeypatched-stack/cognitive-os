from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator

PartCategory = Literal[
    "Mechanical", "Electrical", "Hydraulic", "Pneumatic", "Electronic", "Consumable"
]
PartStatus = Literal[
    "In Stock", "Low Stock", "Out of Stock", "On Order", "Discontinued"
]


class MachinePart(BaseModel):
    id: str
    part_id: str
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None

    part_number: str
    part_name: str
    category: PartCategory
    status: PartStatus = "In Stock"

    manufacturer: Optional[str] = None
    manufacturer_part_number: Optional[str] = None
    supplier: Optional[str] = None
    supplier_part_number: Optional[str] = None

    gstin: Optional[str] = None
    cpid: Optional[str] = None
    giai: Optional[str] = None

    asset_tag: Optional[str] = None

    # calibration
    last_calibration_date: Optional[str] = None
    calibration_frequency_days: Optional[int] = None
    calibration_next_due_date: Optional[str] = None

    # maintenance
    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = None
    maintenance_next_due_date: Optional[str] = None

    # cleaning
    last_cleaning_date: Optional[str] = None
    cleaning_frequency_days: Optional[int] = None
    cleaning_next_due_date: Optional[str] = None

    notes: Optional[str] = None


class MachinePartCreate(MachinePart):
    pass


class MachinePartUpdate(BaseModel):
    part_number: str
    part_name: str
    category: PartCategory
    status: PartStatus = "In Stock"

    manufacturer: Optional[str] = None
    manufacturer_part_number: Optional[str] = None
    supplier: Optional[str] = None
    supplier_part_number: Optional[str] = None

    gstin: Optional[str] = None
    cpid: Optional[str] = None
    giai: Optional[str] = None

    asset_tag: Optional[str] = None

    # calibration
    last_calibration_date: Optional[str] = None
    calibration_frequency_days: Optional[int] = None
    calibration_next_due_date: Optional[str] = None

    # maintenance
    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = None
    maintenance_next_due_date: Optional[str] = None

    # cleaning
    last_cleaning_date: Optional[str] = None
    cleaning_frequency_days: Optional[int] = None
    cleaning_next_due_date: Optional[str] = None

    notes: Optional[str] = None


class MachinePartResponse(MachinePart):
    class Config:
        from_attributes = True


class PaginatedMachinePartResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MachinePartResponse]
