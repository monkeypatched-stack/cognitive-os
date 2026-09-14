from datetime import datetime, date
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

# ─────────────────────────────────────────────
# LITERALS
# ─────────────────────────────────────────────

EdgeServerStatus = Literal[
    "Online",
    "Offline",
    "Degraded",
    "Maintenance",
    "Decommissioned",
]

EdgeServerRole = Literal[
    "Primary Gateway",
    "Redundant Gateway",
    "Protocol Translator",
    "Data Aggregator",
    "Local Historian",
    "Other",
]

ConnectivityType = Literal[
    "Ethernet",
    "Wi-Fi",
    "4G / LTE",
    "5G",
    "Fibre",
    "Serial RS-485",
    "Other",
]

OperatingSystem = Literal[
    "Linux",
    "Windows IoT",
    "FreeRTOS",
    "VxWorks",
    "QNX",
    "Other",
]

# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────


class EdgeServer(BaseModel):
    server_id: str = Field(..., min_length=1)
    serial_no: str = Field(..., min_length=1)
    model_no: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None

    role: EdgeServerRole = "Primary Gateway"
    status: EdgeServerStatus = "Offline"

    # Linkage
    asset_tag: Optional[str] = None
    parent_server_id: Optional[str] = None  # for redundant / hierarchical setups

    # Location
    plant_id: str
    building_id: str
    floor_id: str
    room_id: str
    bay_id: str
    workstation_id: str
    rack_position: Optional[str] = None  # e.g. "Rack A - U12"

    # Manufacturer
    manufacturer: str
    manufacturer_country: Optional[str] = None

    # Network
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    hostname: Optional[str] = None
    subnet_mask: Optional[str] = None
    gateway_ip: Optional[str] = None
    connectivity_type: ConnectivityType = "Ethernet"
    vpn_enabled: bool = False

    # Hardware specs
    cpu: Optional[str] = None  # e.g. "Intel Atom x6425E"
    ram_gb: Optional[float] = Field(None, gt=0)
    storage_gb: Optional[float] = Field(None, gt=0)
    operating_system: OperatingSystem = "Linux"
    os_version: Optional[str] = None
    firmware_version: Optional[str] = None

    # Supported protocols (list of protocols this gateway bridges)
    supported_protocols: List[str] = Field(default_factory=list)

    # Capacity
    max_sensor_count: Optional[int] = Field(None, gt=0)
    connected_sensor_count: int = Field(default=0, ge=0)

    # Uptime / health
    last_heartbeat: Optional[datetime] = None
    uptime_seconds: Optional[int] = Field(None, ge=0)

    # Cloud / SCADA upstream
    upstream_broker_url: Optional[str] = None  # MQTT broker / OPC-UA server URL
    upstream_topic_prefix: Optional[str] = None

    # Dates
    purchase_date: Optional[date] = None
    installation_date: Optional[date] = None
    warranty_expiry_date: Optional[date] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Validators ────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def installation_after_purchase(self):
        if self.purchase_date and self.installation_date:
            if self.installation_date < self.purchase_date:
                raise ValueError(
                    "'installation_date' cannot be before 'purchase_date'."
                )
        return self

    @model_validator(mode="after")
    def sensor_count_within_capacity(self):
        if (
            self.max_sensor_count is not None
            and self.connected_sensor_count > self.max_sensor_count
        ):
            raise ValueError(
                f"'connected_sensor_count' ({self.connected_sensor_count}) exceeds "
                f"'max_sensor_count' ({self.max_sensor_count})."
            )
        return self

    @model_validator(mode="after")
    def redundant_requires_parent(self):
        if self.role == "Redundant Gateway" and not self.parent_server_id:
            raise ValueError(
                "A 'Redundant Gateway' must reference a 'parent_server_id'."
            )
        return self


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────


class EdgeServerCreate(EdgeServer):
    pass


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────


class EdgeServerUpdate(BaseModel):
    serial_no: Optional[str] = None
    model_no: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    role: Optional[EdgeServerRole] = None
    status: Optional[EdgeServerStatus] = None
    asset_tag: Optional[str] = None
    parent_server_id: Optional[str] = None
    facility: Optional[str] = None
    building: Optional[str] = None
    room: Optional[str] = None
    rack_position: Optional[str] = None
    manufacturer: Optional[str] = None
    manufacturer_country: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    hostname: Optional[str] = None
    subnet_mask: Optional[str] = None
    gateway_ip: Optional[str] = None
    connectivity_type: Optional[ConnectivityType] = None
    vpn_enabled: Optional[bool] = None
    cpu: Optional[str] = None
    ram_gb: Optional[float] = Field(None, gt=0)
    storage_gb: Optional[float] = Field(None, gt=0)
    operating_system: Optional[OperatingSystem] = None
    os_version: Optional[str] = None
    firmware_version: Optional[str] = None
    supported_protocols: Optional[List[str]] = None
    max_sensor_count: Optional[int] = Field(None, gt=0)
    connected_sensor_count: Optional[int] = Field(None, ge=0)
    last_heartbeat: Optional[datetime] = None
    uptime_seconds: Optional[int] = Field(None, ge=0)
    upstream_broker_url: Optional[str] = None
    upstream_topic_prefix: Optional[str] = None
    purchase_date: Optional[date] = None
    installation_date: Optional[date] = None
    warranty_expiry_date: Optional[date] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# RESPONSE & PAGINATION
# ─────────────────────────────────────────────


class EdgeServerResponse(EdgeServer):
    class Config:
        from_attributes = True


class PaginatedEdgeServerResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[EdgeServerResponse]
