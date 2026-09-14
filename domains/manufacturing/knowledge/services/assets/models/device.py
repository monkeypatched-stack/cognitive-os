from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator

DeviceStatus = Literal["Online", "Offline", "Lost", "Retired"]


def _normalize_device_status(value):
    if isinstance(value, str):
        normalized = {
            "Active": "Online",
            "Inactive": "Offline",
            "Idle": "Offline",
        }.get(value, value)
        return normalized
    return value


class Device(BaseModel):
    id: str
    device_id: str
    user_id: str
    device_name: str
    type: str
    make_model: str
    os: str
    os_version: str
    imei: str
    serial_no: str
    assigned_to: str
    department: str
    location: str
    mdm_enrolled: bool
    manufacturer: Optional[str] = None
    battery: int = Field(..., ge=0, le=100)
    last_active: str
    status: DeviceStatus

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_device_status(value)


class DeviceCreate(Device):
    pass


class DeviceUpdate(BaseModel):
    device_name: Optional[str] = None
    type: Optional[str] = None
    make_model: Optional[str] = None
    os: Optional[str] = None
    os_version: Optional[str] = None
    imei: Optional[str] = None
    serial_no: Optional[str] = None
    assigned_to: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    mdm_enrolled: Optional[bool] = None
    battery: Optional[int] = Field(None, ge=0, le=100)
    last_active: Optional[str] = None
    status: Optional[DeviceStatus] = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_device_status(value)


class DeviceResponse(Device):
    class Config:
        from_attributes = True


class PaginatedDeviceResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DeviceResponse]
