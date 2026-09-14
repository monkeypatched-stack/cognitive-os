from datetime import datetime, date, timedelta
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, field_validator, model_validator

# ─────────────────────────────────────────────
# LITERALS
# ─────────────────────────────────────────────

SensorStatus = Literal[
    "Active",
    "Inactive",
    "Faulty",
    "Calibrating",
    "Decommissioned",
    "Offline",
]

SensorType = Literal[
    "Temperature",
    "Pressure",
    "Humidity",
    "Vibration",
    "Flow Rate",
    "Level",
    "Proximity",
    "Current",
    "Voltage",
    "Speed / RPM",
    "Force / Load",
    "Gas / Emission",
    "Vision / Camera",
    "Other",
]

CommunicationProtocol = Literal[
    "MQTT",
    "OPC-UA",
    "Modbus RTU",
    "Modbus TCP",
    "PROFINET",
    "EtherNet/IP",
    "IO-Link",
    "BACnet",
    "Zigbee",
    "LoRaWAN",
    "4–20mA",
    "Other",
]

MountingType = Literal[
    "Surface Mount",
    "Inline",
    "Clamp-On",
    "Immersion",
    "Wireless",
    "Panel Mount",
    "Other",
]

# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────


class Sensor(BaseModel):
    sensor_id: str = Field(..., min_length=1)
    serial_no: str = Field(..., min_length=1)
    model_no: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None

    sensor_type: SensorType
    status: SensorStatus = "Inactive"
    communication_protocol: CommunicationProtocol = "MQTT"
    mounting_type: MountingType = "Other"

    # Linkage
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    sensors_id: Optional[str] = None
    edge_server_id: Optional[str] = None
    asset_tag: Optional[str] = None

    # Location
    facility: Optional[str] = None
    building: Optional[str] = None
    room: Optional[str] = None
    position: str = None

    # Manufacturer
    manufacturer: str
    manufacturer_country: Optional[str] = None

    # Measurement specs
    measurement_unit: Optional[str] = None
    measurement_range_min: Optional[float] = None
    measurement_range_max: Optional[float] = None
    accuracy: Optional[str] = None
    resolution: Optional[str] = None
    sampling_rate_hz: Optional[float] = Field(None, gt=0)

    # Thresholds
    alert_threshold_low: Optional[float] = None
    alert_threshold_high: Optional[float] = None

    # Dates
    purchase_date: Optional[date] = None
    installation_date: Optional[date] = None
    warranty_expiry_date: Optional[date] = None
    last_calibration_date: Optional[date] = None
    next_calibration_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(None, gt=0)

    # Firmware / network
    firmware_version: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    topic: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator(
        "purchase_date",
        "installation_date",
        "warranty_expiry_date",
        "last_calibration_date",
        "next_calibration_date",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v):
        return None if v == "" else v

    @model_validator(mode="after")
    def installation_after_purchase(self):
        if self.purchase_date and self.installation_date:
            if self.installation_date < self.purchase_date:
                raise ValueError(
                    "'installation_date' cannot be before 'purchase_date'."
                )
        return self

    @model_validator(mode="after")
    def next_calibration_after_last(self):
        if self.last_calibration_date and self.next_calibration_date:
            if self.next_calibration_date <= self.last_calibration_date:
                raise ValueError(
                    "'next_calibration_date' must be after 'last_calibration_date'."
                )
        return self

    @model_validator(mode="after")
    def auto_next_calibration(self):
        if (
            self.last_calibration_date
            and self.calibration_interval_days
            and not self.next_calibration_date
        ):
            self.next_calibration_date = self.last_calibration_date + timedelta(
                days=self.calibration_interval_days
            )
        return self

    @model_validator(mode="after")
    def validate_measurement_range(self):
        if (
            self.measurement_range_min is not None
            and self.measurement_range_max is not None
        ):
            if self.measurement_range_min >= self.measurement_range_max:
                raise ValueError(
                    "'measurement_range_min' must be less than 'measurement_range_max'."
                )
        return self

    @model_validator(mode="after")
    def validate_alert_thresholds(self):
        if (
            self.alert_threshold_low is not None
            and self.alert_threshold_high is not None
        ):
            if self.alert_threshold_low >= self.alert_threshold_high:
                raise ValueError(
                    "'alert_threshold_low' must be less than 'alert_threshold_high'."
                )
        return self


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────


class SensorCreate(Sensor):
    pass


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────


class SensorUpdate(BaseModel):
    serial_no: Optional[str] = None
    model_no: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    sensor_type: Optional[SensorType] = None
    status: Optional[SensorStatus] = None
    communication_protocol: Optional[CommunicationProtocol] = None
    mounting_type: Optional[MountingType] = None
    machine_id: Optional[str] = None
    equipment_id: Optional[str] = None
    edge_server_id: Optional[str] = None
    asset_tag: Optional[str] = None
    facility: Optional[str] = None
    building: Optional[str] = None
    room: Optional[str] = None
    position: Optional[str] = None
    manufacturer: Optional[str] = None
    manufacturer_country: Optional[str] = None
    measurement_unit: Optional[str] = None
    measurement_range_min: Optional[float] = None
    measurement_range_max: Optional[float] = None
    accuracy: Optional[str] = None
    resolution: Optional[str] = None
    sampling_rate_hz: Optional[float] = Field(None, gt=0)
    alert_threshold_low: Optional[float] = None
    alert_threshold_high: Optional[float] = None
    purchase_date: Optional[date] = None
    installation_date: Optional[date] = None
    warranty_expiry_date: Optional[date] = None
    last_calibration_date: Optional[date] = None
    next_calibration_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(None, gt=0)
    firmware_version: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    topic: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator(
        "purchase_date",
        "installation_date",
        "warranty_expiry_date",
        "last_calibration_date",
        "next_calibration_date",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v):
        return None if v == "" else v


# ─────────────────────────────────────────────
# RESPONSE & PAGINATION
# ─────────────────────────────────────────────


class SensorResponse(Sensor):
    class Config:
        from_attributes = True


class PaginatedSensorResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[SensorResponse]
