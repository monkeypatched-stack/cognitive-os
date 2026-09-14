from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PLCStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    FAULTED = "faulted"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class ProgramStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    FAULTED = "faulted"
    UNKNOWN = "unknown"


class TagDataType(str, Enum):
    BOOL = "bool"
    INT = "int"
    DINT = "dint"
    REAL = "real"
    STRING = "string"
    TIMER = "timer"
    COUNTER = "counter"
    OTHER = "other"


class CommunicationProtocol(str, Enum):
    ETHERNET_IP = "ethernet_ip"
    PROFINET = "profinet"
    MODBUS_TCP = "modbus_tcp"
    MODBUS_RTU = "modbus_rtu"
    OPC_UA = "opc_ua"
    PROFIBUS = "profibus"
    SERIAL = "serial"
    OTHER = "other"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CPUInfo(BaseModel):
    manufacturer: Optional[str] = None
    model: str = Field(..., min_length=1)
    serial_number: Optional[str] = None
    firmware_version: Optional[str] = None
    memory_mb: Optional[float] = Field(None, ge=0)

    model_config = {"str_strip_whitespace": True}


class IOModule(BaseModel):
    module_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    module_type: Optional[str] = None
    slot: Optional[str] = None
    digital_inputs: int = Field(default=0, ge=0)
    digital_outputs: int = Field(default=0, ge=0)
    analog_inputs: int = Field(default=0, ge=0)
    analog_outputs: int = Field(default=0, ge=0)
    healthy: bool = True

    model_config = {"str_strip_whitespace": True}


class Program(BaseModel):
    program_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    version: Optional[str] = None
    status: ProgramStatus = ProgramStatus.UNKNOWN
    checksum: Optional[str] = None
    last_modified: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class Tag(BaseModel):
    name: str = Field(..., min_length=1)
    address: Optional[str] = None
    data_type: TagDataType = TagDataType.OTHER
    value: Optional[Any] = None
    unit: Optional[str] = None
    description: Optional[str] = None
    last_updated: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class CommunicationPort(BaseModel):
    port_id: str = Field(..., min_length=1)
    protocol: CommunicationProtocol = CommunicationProtocol.OTHER
    ip_address: Optional[str] = None
    port_number: Optional[int] = Field(None, ge=0, le=65535)
    baud_rate: Optional[int] = Field(None, gt=0)
    enabled: bool = True

    model_config = {"str_strip_whitespace": True}


class NetworkDevice(BaseModel):
    device_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    device_type: Optional[str] = None
    protocol: Optional[CommunicationProtocol] = None
    last_seen: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class DiagnosticEvent(BaseModel):
    event_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO
    code: Optional[str] = None
    message: str = Field(..., min_length=1)
    is_acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class PLC(BaseModel):
    plc_id: str = Field(
        ..., min_length=1, description="Unique identifier for this PLC instance"
    )
    name: str = Field(
        ..., min_length=1, description="Human-readable name, e.g. 'Line-3 Conveyor PLC'"
    )
    description: Optional[str] = None
    location: Optional[str] = Field(
        None, description="Physical location, e.g. 'Panel CP-03, Building 2'"
    )
    asset_tag: Optional[str] = None
    plant_area: Optional[str] = Field(
        None, description="Plant / production area, e.g. 'Filling Line', 'HVAC'"
    )
    status: PLCStatus = PLCStatus.UNKNOWN
    last_status_change: Optional[datetime] = None
    is_online: bool = False
    last_seen: Optional[datetime] = None
    cpu: CPUInfo
    io_modules: list[IOModule] = Field(default_factory=list)
    power_supply_voltage: Optional[float] = Field(
        None, description="Measured supply voltage (V)"
    )
    programs: list[Program] = Field(default_factory=list)
    project_name: Optional[str] = None
    project_version: Optional[str] = None
    programming_software: Optional[str] = Field(
        None, description="E.g. 'TIA Portal V17', 'GX Works3'"
    )
    last_download: Optional[datetime] = None
    tags: list[Tag] = Field(default_factory=list)
    communication_ports: list[CommunicationPort] = Field(default_factory=list)
    connected_devices: list[NetworkDevice] = Field(default_factory=list)
    diagnostic_events: list[DiagnosticEvent] = Field(default_factory=list)
    active_fault_count: Optional[int] = Field(None, ge=0)
    commissioned_date: Optional[datetime] = None
    last_maintenance_date: Optional[datetime] = None
    next_maintenance_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    custom_attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value pairs for site-specific metadata",
    )

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def maintenance_date_order(self) -> "PLC":
        if self.last_maintenance_date and self.next_maintenance_date:
            if self.next_maintenance_date <= self.last_maintenance_date:
                raise ValueError(
                    "next_maintenance_date must be after last_maintenance_date"
                )
        return self

    @model_validator(mode="after")
    def sync_active_fault_count(self) -> "PLC":
        if self.active_fault_count is None:
            self.active_fault_count = sum(
                1
                for event in self.diagnostic_events
                if event.severity
                in (DiagnosticSeverity.ERROR, DiagnosticSeverity.CRITICAL)
                and not event.is_acknowledged
            )
        return self

    @computed_field  # type: ignore[misc]
    @property
    def total_digital_inputs(self) -> int:
        return sum(module.digital_inputs for module in self.io_modules)

    @computed_field  # type: ignore[misc]
    @property
    def total_digital_outputs(self) -> int:
        return sum(module.digital_outputs for module in self.io_modules)

    @computed_field  # type: ignore[misc]
    @property
    def total_analog_inputs(self) -> int:
        return sum(module.analog_inputs for module in self.io_modules)

    @computed_field  # type: ignore[misc]
    @property
    def total_analog_outputs(self) -> int:
        return sum(module.analog_outputs for module in self.io_modules)

    @computed_field  # type: ignore[misc]
    @property
    def has_active_faults(self) -> bool:
        return (self.active_fault_count or 0) > 0


class PLCCreate(PLC):
    pass


class PLCUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
    location: Optional[str] = None
    asset_tag: Optional[str] = None
    plant_area: Optional[str] = None
    status: Optional[PLCStatus] = None
    last_status_change: Optional[datetime] = None
    is_online: Optional[bool] = None
    last_seen: Optional[datetime] = None
    cpu: Optional[CPUInfo] = None
    io_modules: Optional[list[IOModule]] = None
    power_supply_voltage: Optional[float] = None
    programs: Optional[list[Program]] = None
    project_name: Optional[str] = None
    project_version: Optional[str] = None
    programming_software: Optional[str] = None
    last_download: Optional[datetime] = None
    tags: Optional[list[Tag]] = None
    communication_ports: Optional[list[CommunicationPort]] = None
    connected_devices: Optional[list[NetworkDevice]] = None
    diagnostic_events: Optional[list[DiagnosticEvent]] = None
    active_fault_count: Optional[int] = Field(None, ge=0)
    commissioned_date: Optional[datetime] = None
    last_maintenance_date: Optional[datetime] = None
    next_maintenance_date: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    custom_attributes: Optional[dict[str, Any]] = None

    model_config = {"str_strip_whitespace": True}


class PLCResponse(PLC):
    class Config:
        from_attributes = True


class PaginatedPLCResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PLCResponse]
