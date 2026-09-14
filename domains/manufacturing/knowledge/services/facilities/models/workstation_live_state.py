from typing import List, Optional
from pydantic import BaseModel, Field
from services.events.models.events import Event


class WorkSrationLiveState(BaseModel):
    # Unique Identifiers for the Location
    node_id: str = Field(description="The UI canvas node ID, e.g., 'Work Station 1'")
    work_station_id: Optional[str] = Field(
        None, description="Physical hardware identifier for the station"
    )

    # Order Tracking Context
    work_order_id: Optional[str] = Field(
        None, description="The parent work order database ID"
    )
    order_number: Optional[str] = Field(
        None, description="The human-readable tracking number, e.g., 'WO-9941'"
    )
    order_status: str = Field(
        "Idle", description="Live state: Idle, Setup, Running, Paused, Cleanup"
    )

    # Personnel Tracking (RFID/NFC Badge scans)
    employee_id: Optional[str] = Field(
        None, description="The operator currently logged into this station"
    )
    work_station_id: Optional[str] = Field(
        None, description="Current workstation being tracked"
    )

    # Active Hardware & Tooling Ecosystem (Live RFID Detections)
    active_machine_id: List[str] = Field(
        default_factory=list, description="Fixed machines assigned/connected"
    )
    active_equipment_id: List[str] = Field(
        default_factory=list,
        description="Mobile assets docked here, e.g., ['SS-BIN-01']",
    )
    active_device: Optional[str] = Field(
        None, description="Primary active handheld or test device connected"
    )

    # Bill of Materials (BOM) Expected Lists
    component_list: List[str] = Field(
        default_factory=list, description="Target parts/components needed for this job"
    )
    raw_material_list: List[str] = Field(
        default_factory=list, description="Raw materials/lot batches required"
    )

    # Live Material Telemetry & Metrics (Real-time updates from scans)
    active_component_count: List[int] = Field(
        default_factory=list,
        description="Current quantities of active components present",
    )
    active_rawmaterial_quantity: List[float] = Field(
        default_factory=list,
        description="Current mass/volume of raw materials remaining",
    )
    active_completed_product_count: int = Field(
        0, description="Running tally of finished pieces passing through this node"
    )


# active scan events

# 1. user_scan_events: List[UserScanEvent]
# 2. machine_scan_events: List[Event]
# 3. equipment_scan_events: List[Event]
# 4. component_scan_event: List[ComponentScanEvent]
# 5. raw_material_scan_event: List[RawMaterialScanEvent]
# 6. completed_product_scan_event : List[CompletedProductScanEvent]

# Derived fields
# 1. remaining raw material
# 2. machine / equipment down time by workstation
# 2.1. down time due to cleaning
# 2.2. down time due to mainenance
# 2.3. down time due to calibtation
# 3. actual cycle time and tack time at station
# 4. scheduled time vs actual time taken for step

# Next Phase
# machine performance
# sensor readings
# readings from plc
